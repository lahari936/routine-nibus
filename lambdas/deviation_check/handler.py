"""Every 15 minutes: run the rules for each home; a new deviation becomes an alert and starts escalation.

Optional input: {"home_id": "...", "now": "<ISO 8601>"} to check one home, or at a chosen time (demo/replay).
"""
import json
import os
from datetime import datetime, time, timedelta, timezone

import boto3

from ring_routine_baseline.baseline import compute_baseline
from ring_routine_baseline.models import Alert
from ring_routine_baseline.rules import evaluate
from lambdas.common.store import DynamoStore

store = DynamoStore()
sfn = boto3.client("stepfunctions")


def check_home(home_id: str, now: datetime) -> list[str]:
    home = store.home(home_id)
    if not home or home.paused:
        return []
    local = now.astimezone(home.tz)
    base = store.baseline(home_id) or compute_baseline(home, [], local.date())  # cold start: learning defaults
    since = datetime.combine(local.date() - timedelta(days=1), time(), tzinfo=home.tz)
    started = []
    for d in evaluate(home, base, store.events(home_id, since, now), now):
        sk = f"ALERT#{local.date()}#{d.rule}"
        if not store.add_alert(sk, Alert(home_id=home_id, ts=now, **d.model_dump())):
            continue  # already raised today
        execution = sfn.start_execution(stateMachineArn=os.environ["STATE_MACHINE_ARN"], input=json.dumps({
            "home_id": home_id, "alert_sk": sk, "rule": d.rule, "severity": d.severity, "reason": d.reason,
            "checkin": bool(home.checkin_enabled and home.chime_device_id and home.chime_audio_ref),
            "caregiver_count": len(home.caregivers), "idx": {"n": 0},
        }))
        store.update_alert(home_id, sk, escalation_exec_arn=execution["executionArn"])
        started.append(f"{home_id} {sk}")
    return started


def handler(event, context):
    event = event or {}
    now = datetime.fromisoformat(event["now"]) if event.get("now") else datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("now must include a UTC offset")
    ids = [event["home_id"]] if event.get("home_id") else store.home_ids()
    return {"alerts": [a for hid in ids for a in check_home(hid, now)]}
