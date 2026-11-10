"""Optional LLM explanation of a plan, grounded in the rule-based findings.

The rules decide *what* is wrong; the model explains *why* in plain language
and proposes concrete next steps. Requires `pip install planlens[ai]` and
Anthropic credentials in the environment (ANTHROPIC_API_KEY or `ant auth login`).
"""

from __future__ import annotations

import json
from typing import Any

from planlens.analyze import Report

DEFAULT_MODEL = "claude-opus-5-5"

SYSTEM_PROMPT = """\
You are a senior PostgreSQL performance engineer reviewing a query plan.

You receive the EXPLAIN JSON, an automated analysis (hotspots and rule findings),
and sometimes the SQL text. Write a short review for an application developer:

1. One or two sentences on what dominates the runtime and why.
2. The fixes, most impactful first, each with the exact SQL or setting to try.
3. Anything the automated findings got wrong or missed, if you see it.

Ground every claim in numbers from the plan. If the plan has no ANALYZE data, say
that timings are estimates. Do not invent table names, columns, or indexes that
are not visible in the plan or the SQL. Keep it under 250 words and use plain text
with short numbered lists; no headings.
"""


class AIUnavailableError(RuntimeError):
    """Raised when the `anthropic` package is missing or the request cannot complete."""


def build_user_message(report: Report, sql: str | None = None) -> str:
    parts = []
    if sql:
        parts.append(f"<sql>\n{sql.strip()}\n</sql>")
    parts.append(f"<explain_json>\n{json.dumps(report.plan.raw, indent=1)}\n</explain_json>")
    analysis = json.dumps(report.to_dict(), indent=1)
    parts.append(f"<automated_analysis>\n{analysis}\n</automated_analysis>")
    return "\n\n".join(parts)


def explain(
    report: Report,
    *,
    sql: str | None = None,
    model: str = DEFAULT_MODEL,
    client: Any | None = None,
) -> str:
    """Return a plain-text explanation of the plan from Claude."""
    if client is None:
        try:
            import anthropic
        except ImportError as exc:
            raise AIUnavailableError(
                "AI explanations need the optional dependency: pip install 'planlens[ai]'"
            ) from exc
        client = anthropic.Anthropic()

    try:
        response = client.beta.messages.create(
            model=model,
            max_tokens=16000,
            system=SYSTEM_PROMPT,
            output_config={"effort": "medium"},
            # If the primary model declines, the API retries on a fallback model.
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            messages=[{"role": "user", "content": build_user_message(report, sql)}],
        )
    except Exception as exc:  # surface SDK errors as one friendly type for the CLI
        if type(exc).__module__.startswith("anthropic"):
            raise AIUnavailableError(f"Claude request failed: {exc}") from exc
        raise

    if response.stop_reason == "refusal":
        raise AIUnavailableError("Claude declined to review this plan.")

    text = "".join(block.text for block in response.content if block.type == "text").strip()
    if response.stop_reason == "max_tokens":
        text += "\n[response truncated]"
    return text
