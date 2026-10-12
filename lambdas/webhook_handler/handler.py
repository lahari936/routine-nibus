"""POST /ring/webhook. Verify, dedupe, store; a ding also answers an open check-in prompt."""
import json

import boto3

from core import webhook
from core.models import Event
from lambdas.common.http import raw_body
from lambdas.common.ring import hmac_key
from lambdas.common.store import DynamoStore

sfn = boto3.client("stepfunctions")


class Store(DynamoStore):
    def save_event(self, event: Event) -> None:
        super().save_event(event)
        if event.kind == "ding":
            resolve_checkin(self, event.home_id)


def resolve_checkin(store: DynamoStore, home_id: str) -> None:
    checkin = store.get(f"HOME#{home_id}", "CHECKIN")
    if not checkin:
        return
    store.delete(f"HOME#{home_id}", "CHECKIN")
    store.update_alert(home_id, checkin["alert_sk"], status="self_checked_in")
    try:
        sfn.send_task_success(taskToken=checkin["task_token"], output=json.dumps({"resolved": "self_checked_in"}))
    except (sfn.exceptions.TaskTimedOut, sfn.exceptions.InvalidToken, sfn.exceptions.TaskDoesNotExist):
        pass  # the 10-minute window already closed; escalation carried on


store = Store()


def handler(event, context):
    return {"statusCode": webhook.handle(raw_body(event), event.get("headers") or {}, hmac_key(), store)}
