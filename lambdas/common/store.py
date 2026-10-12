"""DynamoDB single-table access. Every item carries its payload as a JSON string in `doc`,
so floats, tuples and nested models round-trip without Decimal handling.

PK               SK                        doc
HOMES            HOME#{id}                 -                      (index of homes)
HOME#{id}        META                      Home
HOME#{id}        EVT#{utc ts}#{event_id}   Event
HOME#{id}        BASE#current              Baseline
HOME#{id}        ALERT#{local date}#{rule} Alert                  (one per rule per day)
HOME#{id}        CHECKIN                   {task_token, alert_sk} (ttl)
ACCT#{account}   META                      {home_id, outdoor, tokens}
EVTID#{req id}   DEDUPE                    -                      (ttl 7 days)
ACK#{id}         ACK                       {task_token, home_id, alert_sk, caregiver} (ttl)
UNCLAIMED        {expires}#{id}            Tokens                 (ttl, one-way linking)
"""
import json
import os
import time
from datetime import datetime, timezone
from typing import Any

import boto3
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

from core.models import Alert, Baseline, Event, Home

DAY = 86400


def ts_key(ts: datetime) -> str:
    return ts.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


class DynamoStore:
    def __init__(self, table=None):
        self.table = table or boto3.resource("dynamodb").Table(os.environ["TABLE_NAME"])

    # --- generic ---

    def get(self, pk: str, sk: str) -> dict[str, Any] | None:
        item = self.table.get_item(Key={"pk": pk, "sk": sk}).get("Item")
        return json.loads(item["doc"]) if item and "doc" in item else item

    def put(self, pk: str, sk: str, doc: Any = None, ttl: int | None = None, only_new: bool = False) -> bool:
        item: dict[str, Any] = {"pk": pk, "sk": sk}
        if doc is not None:
            item["doc"] = doc if isinstance(doc, str) else json.dumps(doc)
        if ttl:
            item["ttl"] = int(time.time()) + ttl
        try:
            self.table.put_item(Item=item, **({"ConditionExpression": "attribute_not_exists(pk)"} if only_new else {}))
            return True
        except ClientError as e:
            if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
                return False
            raise

    def delete(self, pk: str, sk: str) -> None:
        self.table.delete_item(Key={"pk": pk, "sk": sk})

    def query(self, pk: str, sk_from: str | None = None, sk_to: str | None = None) -> list[dict[str, Any]]:
        cond = Key("pk").eq(pk)
        if sk_from is not None:
            cond &= Key("sk").between(sk_from, sk_to)
        items, kw = [], {"KeyConditionExpression": cond}
        while True:
            page = self.table.query(**kw)
            items += page["Items"]
            if "LastEvaluatedKey" not in page:
                return items
            kw["ExclusiveStartKey"] = page["LastEvaluatedKey"]

    # --- homes ---

    def home(self, home_id: str) -> Home | None:
        doc = self.get(f"HOME#{home_id}", "META")
        return Home(**doc) if doc else None

    def put_home(self, home: Home) -> None:
        self.put(f"HOME#{home.id}", "META", home.model_dump_json())
        self.put("HOMES", f"HOME#{home.id}")

    def home_ids(self) -> list[str]:
        return [i["sk"].removeprefix("HOME#") for i in self.query("HOMES", "HOME#", "HOME#~")]

    def events(self, home_id: str, start: datetime, end: datetime) -> list[Event]:
        items = self.query(f"HOME#{home_id}", f"EVT#{ts_key(start)}", f"EVT#{ts_key(end)}~")
        return [Event.model_validate_json(i["doc"]) for i in items]

    def baseline(self, home_id: str) -> Baseline | None:
        doc = self.get(f"HOME#{home_id}", "BASE#current")
        return Baseline(**doc) if doc else None

    def put_baseline(self, base: Baseline) -> None:
        self.put(f"HOME#{base.home_id}", "BASE#current", base.model_dump_json())

    def add_alert(self, sk: str, alert: Alert) -> bool:
        return self.put(f"HOME#{alert.home_id}", sk, alert.model_dump_json(), only_new=True)

    def alert(self, home_id: str, sk: str) -> Alert | None:
        doc = self.get(f"HOME#{home_id}", sk)
        return Alert(**doc) if doc else None

    def update_alert(self, home_id: str, sk: str, **changes) -> Alert:
        alert = self.alert(home_id, sk).model_copy(update=changes)
        self.put(f"HOME#{home_id}", sk, alert.model_dump_json())
        return alert

    def alerts(self, home_id: str, day_from: str = "", day_to: str = "~") -> list[Alert]:
        return [Alert.model_validate_json(i["doc"])
                for i in self.query(f"HOME#{home_id}", f"ALERT#{day_from}", f"ALERT#{day_to}")]

    # --- webhook.Store protocol ---

    def seen(self, request_id: str) -> bool:
        return self.get(f"EVTID#{request_id}", "DEDUPE") is not None

    def mark_seen(self, request_id: str) -> None:
        self.put(f"EVTID#{request_id}", "DEDUPE", ttl=7 * DAY)

    def home_for_account(self, account_id: str) -> tuple[str, set[str]] | None:
        acct = self.get(f"ACCT#{account_id}", "META")
        return (acct["home_id"], set(acct.get("outdoor", []))) if acct else None

    def save_event(self, event: Event) -> None:
        # Keyed by event_id, so a duplicate delivery overwrites itself instead of adding a row.
        self.put(f"HOME#{event.home_id}", f"EVT#{ts_key(event.ts)}#{event.event_id}", event.model_dump_json())

    def save_events(self, events: list[Event]) -> None:
        with self.table.batch_writer(overwrite_by_pkeys=["pk", "sk"]) as batch:
            for e in events:
                batch.put_item(Item={"pk": f"HOME#{e.home_id}", "sk": f"EVT#{ts_key(e.ts)}#{e.event_id}",
                                     "doc": e.model_dump_json()})

    def unlink(self, account_id: str) -> None:
        """Ring revoked our access: delete the household's data."""
        acct = self.get(f"ACCT#{account_id}", "META")
        if acct:
            pk = f"HOME#{acct['home_id']}"
            with self.table.batch_writer() as batch:
                for item in self.query(pk):
                    batch.delete_item(Key={"pk": pk, "sk": item["sk"]})
            self.delete("HOMES", pk)
        self.delete(f"ACCT#{account_id}", "META")
