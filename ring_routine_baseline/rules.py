"""Deviation rules. Pure, explainable, deterministic. Bedrock never decides whether to alert."""
from datetime import datetime, time, timedelta

from ring_routine_baseline import explain
from ring_routine_baseline.baseline import VISIT_SLACK
from ring_routine_baseline.models import Baseline, Deviation, Event, Home, minute_of_day

MORNING_GRACE = 30  # minutes past first_activity p95
VISIT_MIN_HIT_RATE = 0.8
NIGHT_DOOR_MAX_RATE = 0.05
NIGHT_LOOKBACK = timedelta(hours=12)
DING_COUNT = 3
DING_SPAN = timedelta(minutes=20)
DING_LOOKBACK = timedelta(hours=1)
ANSWER_GRACE = timedelta(minutes=5)  # give someone time to reach the door after the last ring
OFFLINE_AFTER = timedelta(hours=2)


def missed_morning(home: Home, base: Baseline, events: list[Event], now: datetime) -> Deviation | None:
    local_now = now.astimezone(home.tz)
    if minute_of_day(local_now) < base.first_activity.p95 + MORNING_GRACE:
        return None
    day_start = datetime.combine(local_now.date(), time(home.quiet_hours[1]), tzinfo=home.tz)
    if any(e.is_activity and e.ts >= day_start for e in events):
        return None
    return Deviation(rule="missed_morning", severity="high", reason=explain.missed_morning(base))


def missed_visit(home: Home, base: Baseline, events: list[Event], now: datetime) -> Deviation | None:
    local_now = now.astimezone(home.tz)
    today, m_now = local_now.date(), minute_of_day(local_now)
    dings = [
        minute_of_day(local) for e in events
        if e.kind == "ding" and (local := e.ts.astimezone(home.tz)).date() == today
    ]
    for v in base.recurring_visits:
        if v.hit_rate < VISIT_MIN_HIT_RATE or local_now.weekday() not in v.weekdays:
            continue
        if m_now <= v.window_end + VISIT_SLACK:
            continue
        if not any(v.window_start - VISIT_SLACK <= d <= v.window_end + VISIT_SLACK for d in dings):
            return Deviation(rule="missed_visit", severity="high", reason=explain.missed_visit(v))
    return None


def night_door(home: Home, base: Baseline, events: list[Event], now: datetime) -> Deviation | None:
    if base.night_door >= NIGHT_DOOR_MAX_RATE:
        return None
    opens = [
        e for e in events
        if e.kind == "contact_open" and now - e.ts <= NIGHT_LOOKBACK
        and home.in_quiet_hours(e.ts.astimezone(home.tz))
    ]
    if not opens:
        return None
    last = opens[-1]
    return Deviation(rule="night_door", severity="high",
                     reason=explain.night_door(last.device_id, last.ts.astimezone(home.tz)))


def unanswered_doorbell(home: Home, base: Baseline, events: list[Event], now: datetime) -> Deviation | None:
    dings = [e.ts for e in events if e.kind == "ding" and now - e.ts <= DING_LOOKBACK]
    for i in range(len(dings) - DING_COUNT + 1):
        first, nth = dings[i], dings[i + DING_COUNT - 1]
        if nth - first > DING_SPAN or now - nth < ANSWER_GRACE:
            continue
        if not any(e.is_activity and first <= e.ts <= now for e in events):
            rings = sum(1 for t in dings if t >= first)
            return Deviation(rule="unanswered_doorbell", severity="medium",
                             reason=explain.unanswered_doorbell(rings, first.astimezone(home.tz)))
    return None


def device_offline(home: Home, base: Baseline, events: list[Event], now: datetime) -> Deviation | None:
    last = {e.device_id: e for e in events if e.kind in ("offline", "online")}
    for dev, e in last.items():
        if e.kind == "offline" and now - e.ts >= OFFLINE_AFTER:
            return Deviation(rule="device_offline", severity="low",
                             reason=explain.device_offline(dev, e.ts.astimezone(home.tz)))
    return None


RULES = [missed_morning, missed_visit, night_door, unanswered_doorbell, device_offline]


def evaluate(home: Home, base: Baseline, events: list[Event], now: datetime) -> list[Deviation]:
    """Run every rule at `now`. Callers dedupe repeat firings (one alert per rule per day)."""
    seen = sorted((e for e in events if e.ts <= now), key=lambda e: e.ts)
    return [d for rule in RULES if (d := rule(home, base, seen, now))]
