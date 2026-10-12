"""Phase 3 pipeline against mocked AWS (moto): webhook -> DynamoDB -> deviation check -> escalation -> ack."""
import importlib
import json
import re
import sys
from datetime import datetime, time, timedelta
from pathlib import Path

import boto3
import pytest

moto = pytest.importorskip("moto")

from simulator.evaluate import SCENARIOS, load_scenario  # noqa: E402
from simulator.generate_history import HOME  # noqa: E402
from simulator.replay import sign, to_webhook  # noqa: E402

DAY = datetime(2026, 10, 19).date()
KEY = "demo-hmac-key"
ASL = Path(__file__).parent.parent / "stepfunctions" / "escalation.asl.json"


class Recorder:
    """Stands in for a boto3 client; records every call."""

    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        return lambda **kw: self.calls.append((name, kw)) or {}


@pytest.fixture
def aws(monkeypatch):
    for k, v in {"AWS_DEFAULT_REGION": "us-east-1", "AWS_ACCESS_KEY_ID": "x", "AWS_SECRET_ACCESS_KEY": "x",
                 "TABLE_NAME": "rg", "SES_FROM": "alerts@example.com", "ACK_BASE_URL": "https://api.example.com"}.items():
        monkeypatch.setenv(k, v)
    with moto.mock_aws():
        boto3.client("dynamodb").create_table(
            TableName="rg", BillingMode="PAY_PER_REQUEST",
            KeySchema=[{"AttributeName": "pk", "KeyType": "HASH"}, {"AttributeName": "sk", "KeyType": "RANGE"}],
            AttributeDefinitions=[{"AttributeName": "pk", "AttributeType": "S"}, {"AttributeName": "sk", "AttributeType": "S"}])
        secret = boto3.client("secretsmanager").create_secret(
            Name="ring", SecretString=json.dumps({"client_id": "c", "client_secret": "s", "hmac_key": KEY}))
        monkeypatch.setenv("RING_SECRET_ARN", secret["ARN"])
        machine = boto3.client("stepfunctions").create_state_machine(
            name="esc", roleArn="arn:aws:iam::123456789012:role/x",
            definition=ASL.read_text().replace("${EscalationFunctionArn}", "arn:aws:lambda:us-east-1:123456789012:function:esc"))
        monkeypatch.setenv("STATE_MACHINE_ARN", machine["stateMachineArn"])
        for name in [m for m in sys.modules if m.startswith(("lambdas", "tools.aws_demo"))]:
            del sys.modules[name]  # module-level boto3 clients must be created inside the mock
        yield importlib.import_module


def post_webhook(wh, event):
    body = json.dumps(to_webhook(event, "ava1.ring.account.demo")).encode()
    return wh.handler({"body": body.decode(), "isBase64Encoded": False,
                       "headers": {"x-signature": sign(KEY.encode(), body)}}, None)["statusCode"]


def test_missed_morning_alerts_escalates_and_ack_stops_it(aws):
    store = aws("lambdas.common.store").DynamoStore()
    aws("tools.aws_demo").seed(store, "cg1@example.com", "+15555550100", "cg2@example.com", DAY)
    wh, dc, esc, ack = (aws(f"lambdas.{m}.handler") for m in ("webhook_handler", "deviation_check", "escalation", "ack"))

    _, _, events = load_scenario(SCENARIOS / "missed_morning.yml")
    for e in events:
        assert post_webhook(wh, e) == 200
        assert post_webhook(wh, e) == 200  # Ring retry
    day_start = datetime.combine(DAY, time(), tzinfo=HOME.tz)
    assert len(store.events("demo-home", day_start, day_start + timedelta(days=1))) == len(events)

    now = datetime.combine(DAY, time(9, 45), tzinfo=HOME.tz)
    assert dc.handler({"now": now.isoformat()}, None) == {"alerts": ["demo-home ALERT#2026-10-19#missed_morning"]}
    assert dc.handler({"now": (now + timedelta(minutes=15)).isoformat()}, None) == {"alerts": []}  # once per day

    sfn = boto3.client("stepfunctions")
    [execution] = sfn.list_executions(stateMachineArn=sfn.list_state_machines()["stateMachines"][0]["stateMachineArn"])["executions"]
    alert_input = json.loads(sfn.describe_execution(executionArn=execution["executionArn"])["input"])
    assert alert_input | {"reason": ""} == {"home_id": "demo-home", "alert_sk": "ALERT#2026-10-19#missed_morning",
                                            "rule": "missed_morning", "severity": "high", "reason": "",
                                            "checkin": False, "caregiver_count": 2, "idx": {"n": 0}}

    esc.ses, esc.sns, esc.sfn = Recorder(), Recorder(), Recorder()
    esc.handler({"action": "notify", "alert": alert_input, "token": "token-cg1"}, None)
    [(_, sms)] = esc.sns.calls
    [(_, email)] = esc.ses.calls
    assert sms["PhoneNumber"] == "+15555550100" and "No activity yet today" in sms["Message"]
    assert email["Destination"] == {"ToAddresses": ["cg1@example.com"]}
    ack_token = re.search(r"/ack\?token=(\S+)", sms["Message"])[1]

    ack.sfn = Recorder()
    get = ack.handler({"requestContext": {"http": {"method": "GET"}}, "queryStringParameters": {"token": ack_token}}, None)
    assert get["statusCode"] == 200 and "method=post" in get["body"]
    assert store.alert("demo-home", alert_input["alert_sk"]).status == "open"  # link previews don't ack

    post = {"requestContext": {"http": {"method": "POST"}}, "body": f"token={ack_token}", "isBase64Encoded": False}
    assert "Thanks, Caregiver 1" in ack.handler(post, None)["body"]
    assert ack.sfn.calls == [("send_task_success", {"taskToken": "token-cg1", "output": '{"acked_by": "Caregiver 1"}'})]
    alert = store.alert("demo-home", alert_input["alert_sk"])
    assert (alert.status, alert.acked_by) == ("acked", "Caregiver 1")
    assert "Already handled" in ack.handler(post, None)["body"]

    # Ack raced the 15-minute timeout: the next notify step closes itself instead of paging caregiver 2.
    esc.handler({"action": "notify", "alert": alert_input | {"idx": {"n": 1}}, "token": "token-cg2"}, None)
    assert len(esc.ses.calls) == 1 and esc.sfn.calls[0][1]["taskToken"] == "token-cg2"


def test_ding_during_checkin_resolves_alert(aws):
    store = aws("lambdas.common.store").DynamoStore()
    aws("tools.aws_demo").seed(store, "cg1@example.com", None, None, DAY)
    wh = aws("lambdas.webhook_handler.handler")
    wh.sfn = Recorder()
    from core.models import Alert
    sk = "ALERT#2026-10-19#missed_morning"
    store.add_alert(sk, Alert(home_id="demo-home", ts=datetime.combine(DAY, time(9, 45), tzinfo=HOME.tz),
                              rule="missed_morning", severity="high", reason="r"))
    store.put("HOME#demo-home", "CHECKIN", {"task_token": "token-checkin", "alert_sk": sk})
    ding = next(e for e in load_scenario(SCENARIOS / "missed_morning.yml")[2] if e.kind == "ding")
    assert post_webhook(wh, ding) == 200
    assert store.alert("demo-home", sk).status == "self_checked_in"
    assert wh.sfn.calls[0][1]["taskToken"] == "token-checkin"
    assert store.get("HOME#demo-home", "CHECKIN") is None


def test_api_scopes_homes_to_caregiver_and_validates_settings(aws):
    store = aws("lambdas.common.store").DynamoStore()
    aws("tools.aws_demo").seed(store, "CG1@example.com", None, None, DAY)
    api = aws("lambdas.api.handler")
    call = lambda route, email, home="demo-home", body=None: api.handler({
        "routeKey": route, "pathParameters": {"home_id": home}, "body": body,
        "requestContext": {"authorizer": {"jwt": {"claims": {"email": email}}}}}, None)
    assert [h["id"] for h in json.loads(call("GET /api/homes", "cg1@example.com")["body"])] == ["demo-home"]
    assert json.loads(call("GET /api/homes", "stranger@example.com")["body"]) == []
    assert call("GET /api/homes/{home_id}", "stranger@example.com")["statusCode"] == 404
    detail = json.loads(call("GET /api/homes/{home_id}", "cg1@example.com")["body"])
    assert detail["learning"] is False and detail["baseline"]["recurring_visits"]
    assert call("PUT /api/homes/{home_id}/settings", "cg1@example.com", body='{"timezone": "Mars/Base"}')["statusCode"] == 400
    ok = call("PUT /api/homes/{home_id}/settings", "cg1@example.com", body='{"paused": true, "account_id": "evil"}')
    assert json.loads(ok["body"])["paused"] is True and store.home("demo-home").account_id == "ava1.ring.account.demo"


def test_unlink_deletes_household(aws):
    store = aws("lambdas.common.store").DynamoStore()
    aws("tools.aws_demo").seed(store, "cg1@example.com", None, None, DAY)
    store.unlink("ava1.ring.account.demo")
    assert store.home("demo-home") is None and store.query("HOME#demo-home") == [] and store.home_ids() == []


def test_asl_states_are_wired():
    asl = json.loads(ASL.read_text())
    states = asl["States"]
    targets = {asl["StartAt"]} | {s[k] for s in states.values() for k in ("Next", "Default") if k in s}
    targets |= {c["Next"] for s in states.values() for c in s.get("Choices", []) + s.get("Catch", [])}
    assert targets <= set(states) and set(states) - targets == set()
