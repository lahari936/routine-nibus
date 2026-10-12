"""One-sentence, caregiver-readable reasons. No jargon, no medical claims."""
from datetime import datetime

from core.models import Baseline, RecurringVisit, minute_of_day


def clock(minutes: float) -> str:
    h, m = divmod(round(minutes), 60)
    h %= 24
    return f"{h % 12 or 12}:{m:02d} {'AM' if h < 12 else 'PM'}"


def clock_dt(local: datetime) -> str:
    return clock(minute_of_day(local))


def device_name(device_id: str) -> str:
    return device_id.replace("_", " ").capitalize()


def missed_morning(base: Baseline) -> str:
    if base.learning:
        return (f"No activity yet today. Still learning this home's routine, "
                f"so we expected activity by {clock(base.first_activity.p95)}.")
    return f"No activity yet today. Usually first active by {clock(base.first_activity.p90)} (9 of 10 days)."


def missed_visit(visit: RecurringVisit) -> str:
    return f"The usual {visit.label} didn't happen."


def night_door(device_id: str, local: datetime) -> str:
    return f"{device_name(device_id)} opened at {clock_dt(local)}, which is unusual for this home."


def unanswered_doorbell(rings: int, first_local: datetime) -> str:
    return f"Doorbell rang {rings} times since {clock_dt(first_local)} with no answer."


def device_offline(device_id: str, since_local: datetime) -> str:
    return f"{device_name(device_id)} offline since {clock_dt(since_local)}, so monitoring is limited."
