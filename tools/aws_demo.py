"""Drive the deployed stack for the Phase 3 acceptance demo (needs AWS credentials, boto3).

  python -m tools.aws_demo seed --email you@example.com [--phone +15551234567] [--email2 ...] [--end 2026-10-19]
  python -m simulator.replay simulator/scenarios/missed_morning.yml <ApiUrl>/ring/webhook \
      --account ava1.ring.account.demo --date 2026-10-19
  python -m tools.aws_demo check --at 2026-10-19T09:45      # home-local time; omit --at for "now"
  python -m tools.aws_demo status

`seed` writes a demo home (America/New_York) with 28 days of synthetic history ending the day before --end,
its baseline, and maps the demo Ring account to it so replayed webhooks land there.
"""
import argparse
import json
import os
from datetime import date, datetime, timedelta

import boto3

from core.baseline import compute_baseline
from core.models import Caregiver
from simulator.generate_history import HOME, generate_history

STACK = "RoutineGuardian"
HOME_ID = "demo-home"
DEMO_ACCOUNT = "ava1.ring.account.demo"


def outputs() -> dict[str, str]:
    stack = boto3.client("cloudformation").describe_stacks(StackName=STACK)["Stacks"][0]
    return {o["OutputKey"]: o["OutputValue"] for o in stack["Outputs"]}


def seed(store, email: str, phone: str | None, email2: str | None, end: date) -> None:
    caregivers = [Caregiver(name="Caregiver 1", email=email, phone=phone)]
    if email2:
        caregivers.append(Caregiver(name="Caregiver 2", email=email2))
    home = HOME.model_copy(update={"id": HOME_ID, "account_id": DEMO_ACCOUNT, "caregivers": caregivers})
    store.put_home(home)
    store.put(f"ACCT#{DEMO_ACCOUNT}", "META", {"home_id": HOME_ID, "outdoor": ["doorbell"]})
    history = generate_history(home, end, 28, seed=7)
    store.save_events(history)
    base = compute_baseline(home, history, end)
    store.put_baseline(base)
    print(f"seeded {len(history)} events; first active p90 {base.first_activity.p90:.0f} min, "
          f"visits {[v.label for v in base.recurring_visits]}")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("seed")
    s.add_argument("--email", required=True)
    s.add_argument("--phone")
    s.add_argument("--email2")
    s.add_argument("--end", type=date.fromisoformat, default=date.today())
    c = sub.add_parser("check")
    c.add_argument("--at", help="home-local ISO time, e.g. 2026-10-19T09:45")
    sub.add_parser("status")
    a = p.parse_args()

    out = outputs()
    os.environ["TABLE_NAME"] = out["TableName"]
    from lambdas.common.store import DynamoStore  # noqa: E402 (needs TABLE_NAME)
    store = DynamoStore()

    if a.cmd == "seed":
        seed(store, a.email, a.phone, a.email2, a.end)
    elif a.cmd == "check":
        payload = {"home_id": HOME_ID}
        if a.at:
            payload["now"] = datetime.fromisoformat(a.at).replace(tzinfo=HOME.tz).isoformat()
        r = boto3.client("lambda").invoke(FunctionName=out["DeviationCheckFunction"], Payload=json.dumps(payload))
        print(r["Payload"].read().decode())
    else:
        for alert in store.alerts(HOME_ID, (date.today() - timedelta(days=7)).isoformat()):
            print(f"{alert.ts:%Y-%m-%d %H:%M}Z {alert.rule:20} {alert.status:16} {alert.acked_by or ''}  {alert.reason}")
