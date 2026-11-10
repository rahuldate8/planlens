import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from planlens import analyze, load_plan
from planlens.ai import AIUnavailableError, build_user_message, explain
from planlens.cli import EXIT_ERROR, EXIT_FINDINGS, EXIT_OK, main

FIXTURES = Path(__file__).parent / "fixtures"


def test_json_output_is_machine_readable(capsys):
    assert main(["analyze", str(FIXTURES / "sort_spill.json"), "--format", "json"]) == EXIT_OK
    data = json.loads(capsys.readouterr().out)
    assert data["analyzed"] is True
    assert data["findings"][0]["rule_id"] == "PL003"


def test_fail_on_sets_exit_code_for_ci():
    plan = str(FIXTURES / "seq_scan_filter.json")
    assert main(["analyze", plan, "--fail-on", "critical"]) == EXIT_FINDINGS
    assert main(["analyze", str(FIXTURES / "healthy.json"), "--fail-on", "info"]) == EXIT_OK


def test_bad_input_returns_error(tmp_path, capsys):
    bad = tmp_path / "plan.txt"
    bad.write_text("Seq Scan on claims  (cost=0.00..1.00 rows=1 width=4)")
    assert main(["analyze", str(bad)]) == EXIT_ERROR
    assert "FORMAT JSON" in capsys.readouterr().err


class FakeMessages:
    def __init__(self, response):
        self.response = response
        self.kwargs = None

    def create(self, **kwargs):
        self.kwargs = kwargs
        return self.response


def fake_client(text="Add an index.", stop_reason="end_turn"):
    response = SimpleNamespace(
        stop_reason=stop_reason,
        content=[
            SimpleNamespace(type="thinking", thinking=""),
            SimpleNamespace(type="text", text=text),
        ],
    )
    return SimpleNamespace(beta=SimpleNamespace(messages=FakeMessages(response)))


@pytest.fixture
def report():
    return analyze(load_plan(FIXTURES / "seq_scan_filter.json"))


def test_explain_sends_plan_findings_and_sql(report):
    client = fake_client()
    assert explain(report, sql="SELECT 1", client=client) == "Add an index."
    sent = client.beta.messages.kwargs
    body = sent["messages"][0]["content"]
    assert "<sql>\nSELECT 1\n</sql>" in body
    assert "PL002" in body
    assert sent["fallbacks"] == "default"


def test_explain_raises_on_refusal(report):
    with pytest.raises(AIUnavailableError):
        explain(report, client=fake_client(stop_reason="refusal"))


def test_user_message_omits_sql_when_absent(report):
    assert "<sql>" not in build_user_message(report)
