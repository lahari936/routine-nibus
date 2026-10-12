"""Step Functions tasks for stepfunctions/escalation.asl.json.

checkin  play the chime prompt and park the task token until a ding (webhook_handler) or 10 min timeout
notify   page caregiver[idx] by SMS + email with an ack link; park the task token until /ack or 15 min timeout
resolve  set the alert's final status (unresolved)
"""
import json
import os
import secrets
from datetime import datetime, timedelta, timezone

import boto3

from core.models import Home
from lambdas.common.ring import client_for_account
from lambdas.common.store import DAY, DynamoStore

store = DynamoStore()
sfn = boto3.client("stepfunctions")
sns = boto3.client("sns")
ses = boto3.client("ses")


def checkin(alert: dict, token: str) -> None:
    home = store.home(alert["home_id"])
    client = client_for_account(store, home.account_id)
    # Needs Ring's chime-access approval. Any failure here fails the task and escalation moves on to notify.
    client.request("POST", f"/v1/devices/{home.chime_device_id}/media/audio/playback",
                   {"audio_ref": home.chime_audio_ref}, ok=(200, 201, 202, 204))
    store.put(f"HOME#{home.id}", "CHECKIN", {"task_token": token, "alert_sk": alert["alert_sk"]}, ttl=900)


def snapshot_line(home: Home) -> str:
    """Only during an active alert, only with explicit opt-in, only as an expiring link."""
    if not (home.snapshot_optin and home.snapshot_device_id and home.account_id):
        return ""
    try:
        url = client_for_account(store, home.account_id).snapshot_url(
            home.snapshot_device_id, datetime.now(timezone.utc) - timedelta(hours=1))
        return f"\nLatest doorbell snapshot (link expires soon): {url}"
    except Exception as e:
        print(f"snapshot failed: {e!r}")
        return ""


def notify(alert: dict, token: str) -> None:
    home = store.home(alert["home_id"])
    current = store.alert(home.id, alert["alert_sk"])
    if current.status != "open":  # acked while the previous step was timing out
        sfn.send_task_success(taskToken=token, output=json.dumps({"already": current.status}))
        return
    idx = alert["idx"]["n"]
    caregiver = home.caregivers[idx]
    ack_id = secrets.token_urlsafe(24)
    store.put(f"ACK#{ack_id}", "ACK", {"home_id": home.id, "alert_sk": alert["alert_sk"], "caregiver": caregiver.name},
              ttl=2 * DAY)
    # The current task token lives on the alert, so any caregiver's link stops whichever step is waiting.
    store.put(f"HOME#{home.id}", f"PENDING#{alert['alert_sk']}", {"task_token": token}, ttl=2 * DAY)

    link = f"{os.environ['ACK_BASE_URL'].rstrip('/')}/ack?token={ack_id}"
    earlier = " No one has responded yet." if idx else ""
    text = f"Routine Guardian, {home.name}: {alert['reason']}{earlier}\nI'm on it: {link}"
    sms_ok = os.environ.get("SNS_SMS_ENABLED", "true") == "true" and alert["severity"] != "low"
    if caregiver.phone and sms_ok:
        sns.publish(PhoneNumber=caregiver.phone, Message=text)
    if caregiver.email:
        ses.send_email(
            Source=os.environ["SES_FROM"], Destination={"ToAddresses": [caregiver.email]},
            Message={"Subject": {"Data": f"Routine change at {home.name}"},
                     "Body": {"Text": {"Data": text + snapshot_line(home) +
                                       "\n\nA routine change was noticed. This is not a medical alert."}}})


def handler(event, context):
    action, alert = event["action"], event["alert"]
    if action == "checkin":
        checkin(alert, event["token"])
    elif action == "notify":
        notify(alert, event["token"])
    elif action == "resolve":
        store.update_alert(alert["home_id"], alert["alert_sk"], status=event["status"])
    return {"ok": True}
