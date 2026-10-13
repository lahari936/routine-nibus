"""Dashboard API behind a Cognito JWT authorizer. A user sees the homes that list their email as a caregiver.

GET /api/homes                              homes for this caregiver
GET /api/homes/{home_id}                    settings + baseline
GET /api/homes/{home_id}/events?from&to     ISO 8601, default last 24 h
GET /api/homes/{home_id}/alerts?from&to     local dates YYYY-MM-DD
PUT /api/homes/{home_id}/settings           partial Home update
"""
import json
from datetime import datetime, timedelta, timezone

from ring_routine_baseline.models import Home
from lambdas.common.http import as_json, query
from lambdas.common.store import DynamoStore

store = DynamoStore()
EDITABLE = {"name", "timezone", "caregivers", "quiet_hours", "checkin_enabled", "snapshot_optin", "paused",
            "chime_device_id", "chime_audio_ref", "snapshot_device_id"}


def email_of(event) -> str:
    return event["requestContext"]["authorizer"]["jwt"]["claims"].get("email", "").lower()


def allowed(home: Home | None, email: str) -> bool:
    return bool(home and email and any((c.email or "").lower() == email for c in home.caregivers))


def handler(event, context):
    email, route = email_of(event), event["routeKey"]
    if route == "GET /api/homes":
        homes = [h for h in map(store.home, store.home_ids()) if allowed(h, email)]
        return as_json(200, [h.model_dump(mode="json") for h in homes])

    home = store.home(event["pathParameters"]["home_id"])
    if not allowed(home, email):
        return as_json(404, {"error": "not found"})  # don't reveal which homes exist
    q = query(event)

    if route == "GET /api/homes/{home_id}":
        base = store.baseline(home.id)
        digests = store.query(f"HOME#{home.id}", "DIGEST#", "DIGEST#~")
        return as_json(200, {"home": home.model_dump(mode="json"),
                             "baseline": base.model_dump(mode="json") if base else None,
                             "learning": base.learning if base else True,
                             "digest": json.loads(digests[-1]["doc"]) if digests else None})
    if route == "GET /api/homes/{home_id}/events":
        end = datetime.fromisoformat(q["to"]) if "to" in q else datetime.now(timezone.utc)
        start = datetime.fromisoformat(q["from"]) if "from" in q else end - timedelta(days=1)
        return as_json(200, [e.model_dump(mode="json", exclude={"raw"}) for e in store.events(home.id, start, end)])
    if route == "GET /api/homes/{home_id}/alerts":
        alerts = store.alerts(home.id, q.get("from", ""), q.get("to", "9999") + "~")
        return as_json(200, [a.model_dump(mode="json", exclude={"escalation_exec_arn"}) for a in alerts])
    if route == "PUT /api/homes/{home_id}/settings":
        changes = {k: v for k, v in json.loads(event.get("body") or "{}").items() if k in EDITABLE}
        try:
            updated = Home(**{**home.model_dump(), **changes})
            updated.tz  # reject unknown time zones
        except Exception as e:
            return as_json(400, {"error": str(e)})
        store.put_home(updated)
        return as_json(200, updated.model_dump(mode="json"))
    return as_json(404, {"error": "no route"})
