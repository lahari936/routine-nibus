"""Phase 4: day facts, daily digest, caregiver agent tools, demo export."""
import json
from datetime import date, datetime, time, timedelta

import pytest

from ring_routine_baseline.baseline import compute_baseline
from ring_routine_baseline.facts import baseline_facts, day_summary, plain_digest
from ring_routine_baseline.models import Alert
from simulator.generate_history import HOME, generate_history
from tools.export_demo import build, scenario_on

DAY = date(2026, 10, 19)


def test_day_summary_reports_missed_visit_and_alert():
    history = generate_history(HOME, DAY, 28, seed=7)
    base = compute_baseline(HOME, history, DAY)
    events = scenario_on("missed_aide", DAY)
    alert = Alert(home_id=HOME.id, ts=datetime.combine(DAY, time(11), tzinfo=HOME.tz), rule="missed_visit",
                  severity="high", reason="The usual 10:00 AM visit didn't happen.", status="acked", acked_by="Caregiver 1")
    facts = day_summary(HOME, base, events, [alert], DAY)
    assert facts["weekday"] == "Monday" and facts["first_activity"] == "7:28 AM"
    assert facts["usual_visits"] == [{"visit": "10:00 AM visit", "happened": False}]
    assert facts["alerts"] == [{"rule": "missed_visit", "reason": alert.reason, "status": "acked", "acknowledged_by": "Caregiver 1"}]
    assert facts["doorbell_rings"] == [] and facts["door_opens_in_quiet_hours"] == []
    text = plain_digest(facts)
    assert "did not happen" in text and "acked" in text

    normal = day_summary(HOME, base, scenario_on("normal_day", DAY), [], DAY)
    assert normal["usual_visits"][0]["happened"] and normal["alerts"] == []
    assert baseline_facts(base)["recurring_visits"][0]["weekdays"] == ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]


def test_demo_export_has_a_week_with_two_alerts():
    data = build(DAY)
    assert [(a["ts"][:10], a["rule"], a["status"]) for a in data["alerts"]] == [
        ("2026-10-16", "missed_visit", "acked"), ("2026-10-19", "missed_morning", "open")]
    json.dumps(data)  # serializable


# --- AWS-backed: digest Lambda and agent tools against moto ---

moto = pytest.importorskip("moto")
from tests.test_phase3 import Recorder, aws  # noqa: E402,F401  (fixture)


def seeded(aws):
    store = aws("lambdas.common.store").DynamoStore()
    aws("tools.aws_demo").seed(store, "cg1@example.com", None, None, DAY - timedelta(days=1))
    store.save_events([e.model_copy(update={"home_id": "demo-home"}) for e in scenario_on("missed_aide", DAY - timedelta(days=1))])
    store.add_alert("ALERT#2026-10-18#missed_visit", Alert(
        home_id="demo-home", ts=datetime.combine(DAY - timedelta(days=1), time(11), tzinfo=HOME.tz),
        rule="missed_visit", severity="high", reason="The usual 10:00 AM visit didn't happen.",
        status="acked", acked_by="Caregiver 1"))
    return store


class FakeBedrock:
    def __init__(self, text=None, error=None):
        self.text, self.error, self.calls = text, error, []

    def __call__(self, **kw):
        outer = self

        class Messages:
            def create(self, **req):
                outer.calls.append(req)
                if outer.error:
                    raise outer.error
                block = type("B", (), {"type": "text", "text": outer.text})()
                return type("M", (), {"content": [block], "stop_reason": "end_turn"})()
        return type("C", (), {"messages": Messages()})()


def test_digest_uses_model_text_and_falls_back_to_facts(aws, monkeypatch):
    store = seeded(aws)
    digest = aws("lambdas.daily_digest.handler")
    digest.ses = Recorder()
    fake = FakeBedrock(text="A mostly usual Sunday; the aide visit was missed and Caregiver 1 handled it.")
    monkeypatch.setattr(digest.anthropic, "AnthropicBedrockMantle", fake)

    out = digest.handler({"home_id": "demo-home", "day": "2026-10-18"}, None)
    assert out["digests"][0]["text"] == fake.text
    facts = json.loads(fake.calls[0]["messages"][0]["content"])
    assert facts["date"] == "2026-10-18" and facts["alerts"][0]["rule"] == "missed_visit"
    assert fake.calls[0]["model"] == "anthropic.claude-opus-5-5"
    assert digest.ses.calls[0][1]["Destination"] == {"ToAddresses": ["cg1@example.com"]}
    assert store.get("HOME#demo-home", "DIGEST#2026-10-18")["text"] == fake.text

    monkeypatch.setattr(digest.anthropic, "AnthropicBedrockMantle", FakeBedrock(error=RuntimeError("no access")))
    text = digest.handler({"home_id": "demo-home", "day": "2026-10-18"}, None)["digests"][0]["text"]
    assert text.startswith("Sunday 2026-10-18") and "visit didn't happen (acked)" in text


def test_agent_tools_return_grounded_facts(aws):
    pytest.importorskip("strands")
    store = seeded(aws)
    from agent.caregiver_agent import build_agent
    agent = build_agent(store, "demo-home", now=datetime.combine(DAY, time(12), tzinfo=HOME.tz))  # real Bedrock model object, never called
    assert sorted(agent.tool_names) == ["get_alerts", "get_baseline", "get_day_summary"]
    assert "Today is Monday 2026-10-19" in agent.system_prompt

    def result(r):
        return json.loads(r["content"][0]["text"]) if "text" in r["content"][0] else r["content"][0]["json"]

    day = result(agent.tool.get_day_summary(day="2026-10-18", record_direct_tool_call=False))
    assert day["usual_visits"] == [] or day["usual_visits"][0]["happened"] is False  # Sunday has no aide
    assert day["alerts"][0]["acknowledged_by"] == "Caregiver 1"
    alerts = result(agent.tool.get_alerts(start_date="2026-10-12", end_date="2026-10-19", record_direct_tool_call=False))
    assert [a["date"] for a in alerts] == ["2026-10-18"]
    assert result(agent.tool.get_baseline(record_direct_tool_call=False))["learning"] is False
    future = result(agent.tool.get_day_summary(day="2026-10-25", record_direct_tool_call=False))
    assert "future" in future["error"]


def test_agent_api_hides_other_homes(aws):
    pytest.importorskip("strands")
    seeded(aws)
    api = aws("lambdas.agent_api.handler")
    r = api.handler({"pathParameters": {"home_id": "demo-home"}, "body": '{"question": "How was the week?"}',
                     "requestContext": {"authorizer": {"jwt": {"claims": {"email": "stranger@example.com"}}}}}, None)
    assert r["statusCode"] == 404
