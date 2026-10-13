"""Strands agent that answers caregiver questions ("How was Mom's week?") from structured facts only.

  python -m agent.caregiver_agent --home demo-home "How was Mom's week?"    # needs AWS creds + Bedrock access

Claude runs on Amazon Bedrock through the Anthropic SDK's Bedrock (Mantle) client, plugged into Strands'
Anthropic model provider. Tools read DynamoDB through the same store as the Lambdas.
"""
import argparse
import os
from datetime import date, datetime, timedelta, timezone

import anthropic
from strands import Agent, tool
from strands.models.anthropic import AnthropicModel

from core.facts import baseline_facts, day_summary

MODEL_ID = os.environ.get("BEDROCK_MODEL_ID", "anthropic.claude-opus-5-5")
MAX_RANGE_DAYS = 31

SYSTEM = """You help family caregivers understand how an older relative's daily routine is going at home.
Your only data comes from the tools: motion, doorbell and door-sensor events from Ring devices, summarised
per day and compared with the home's own learned routine. There is no video and no health data.

Look facts up with the tools before answering. Ground every statement in tool results and cite the specific
days (e.g. "Tue Oct 14") and alerts you rely on. When something was normal, say so briefly; spend words on
what differed from usual and on alerts, including whether someone acknowledged them. If the data doesn't
cover something, say that plainly. Describe routine changes only: never guess at causes, health or
medical conditions. Keep answers short and warm, written for a busy family member."""


def bedrock_model(effort: str = "medium") -> AnthropicModel:
    model = AnthropicModel(model_id=MODEL_ID, max_tokens=16000, params={"output_config": {"effort": effort}},
                           client_args={"api_key": "unused-bedrock-signs-requests"})
    model.client = anthropic.AsyncAnthropicBedrockMantle(aws_region=os.environ.get("AWS_REGION", "us-east-1"))
    return model


def build_agent(store, home_id: str, now: datetime | None = None, model=None) -> Agent:
    home = store.home(home_id)
    if home is None:
        raise ValueError(f"unknown home {home_id}")
    today = (now or datetime.now(timezone.utc)).astimezone(home.tz).date()

    def local_midnight(d: date) -> datetime:
        return datetime(d.year, d.month, d.day, tzinfo=home.tz)

    @tool
    def get_day_summary(day: str) -> dict:
        """Facts for one calendar day at the home: first and last activity, events recorded vs a usual day,
        doorbell rings, expected visits and whether they happened, door opens during quiet hours, alerts.

        Args:
            day: Date as YYYY-MM-DD, in the home's local calendar.
        """
        d = date.fromisoformat(day)
        if d > today:
            return {"error": f"{day} is in the future; today is {today}"}
        events = store.events(home_id, local_midnight(d), local_midnight(d + timedelta(days=1)))
        alerts = store.alerts(home_id, d.isoformat(), d.isoformat() + "~")
        return day_summary(home, store.baseline(home_id), events, alerts, d)

    @tool
    def get_alerts(start_date: str, end_date: str) -> list[dict]:
        """Alerts raised between two dates (inclusive): rule, reason, status, who acknowledged it.

        Args:
            start_date: First day, YYYY-MM-DD.
            end_date: Last day, YYYY-MM-DD.
        """
        start, end = date.fromisoformat(start_date), date.fromisoformat(end_date)
        if (end - start).days > MAX_RANGE_DAYS:
            return [{"error": f"range is limited to {MAX_RANGE_DAYS} days"}]
        return [{"date": a.ts.astimezone(home.tz).date().isoformat(), "rule": a.rule, "reason": a.reason,
                 "status": a.status, "acknowledged_by": a.acked_by}
                for a in store.alerts(home_id, start.isoformat(), end.isoformat() + "~")]

    @tool
    def get_baseline() -> dict:
        """The home's learned routine over the last 28 days: typical wake-up time, usual activity per weekday,
        recurring visits (e.g. an aide), how often the door opens at night, and whether it is still learning."""
        base = store.baseline(home_id)
        return baseline_facts(base) if base else {"learning": True, "note": "No routine learned yet."}

    prompt = SYSTEM + f"\n\nThe home is called {home.name!r}. Today is {today:%A %Y-%m-%d} in the home's time zone."
    return Agent(model=model or bedrock_model(), system_prompt=prompt,
                 tools=[get_day_summary, get_alerts, get_baseline], callback_handler=None)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("question")
    p.add_argument("--home", default="demo-home")
    p.add_argument("--today", type=date.fromisoformat, help="pretend today is this date (demo data)")
    a = p.parse_args()
    from lambdas.common.store import DynamoStore  # needs TABLE_NAME
    now = datetime.combine(a.today, datetime.max.time(), timezone.utc) if a.today else None
    print(build_agent(DynamoStore(), a.home, now)(a.question))
