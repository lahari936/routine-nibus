"""GET /ack?token=... shows a confirm button; POST /ack acknowledges and stops escalation.

The GET never acknowledges: mail scanners and link previews fetch links on their own.
"""
import html
import json

import boto3

from lambdas.common.http import form, page, query
from lambdas.common.store import DynamoStore

store = DynamoStore()
sfn = boto3.client("stepfunctions")


def handler(event, context):
    if event["requestContext"]["http"]["method"] == "GET":
        token = html.escape(query(event).get("token", ""))
        return page(200, "Routine Guardian", f"""
            <p>Let the other caregivers know you're handling this?</p>
            <form method=post><input type=hidden name=token value='{token}'>
              <button style='font:inherit;padding:.5rem 1rem'>I'm on it</button></form>""")

    ack = store.get(f"ACK#{form(event).get('token', '')}", "ACK")
    if not ack:
        return page(404, "Link expired", "<p>This link is no longer valid.</p>")
    alert = store.alert(ack["home_id"], ack["alert_sk"])
    if alert.status != "open":
        who = f" by {alert.acked_by}" if alert.acked_by else ""
        return page(200, "Already handled", f"<p>This alert was already {alert.status.replace('_', ' ')}{html.escape(who)}.</p>")
    store.update_alert(ack["home_id"], ack["alert_sk"], status="acked", acked_by=ack["caregiver"])
    pending = store.get(f"HOME#{ack['home_id']}", f"PENDING#{ack['alert_sk']}")
    if pending:
        try:
            sfn.send_task_success(taskToken=pending["task_token"], output=json.dumps({"acked_by": ack["caregiver"]}))
        except (sfn.exceptions.TaskTimedOut, sfn.exceptions.InvalidToken, sfn.exceptions.TaskDoesNotExist):
            pass  # escalation already finished
    return page(200, "Thank you", f"<p>Thanks, {html.escape(ack['caregiver'])}. No one else will be paged for this.</p>")
