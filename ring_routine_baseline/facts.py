"""Structured, verifiable facts about a day. The digest and the agent only ever narrate these;
they never see raw events and never decide whether something is an alert."""
from datetime import date
from typing import Any

from ring_routine_baseline.baseline import VISIT_SLACK
from ring_routine_baseline.explain import clock, clock_dt
from ring_routine_baseline.models import Alert, Baseline, Event, Home

WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def day_summary(home: Home, base: Baseline | None, events: list[Event], alerts: list[Alert], day: date) -> dict[str, Any]:
    tz = home.tz
    todays = sorted((e for e in events if e.ts.astimezone(tz).date() == day), key=lambda e: e.ts)
    day_start = home.quiet_hours[1]
    awake = [e for e in todays if e.is_activity and e.ts.astimezone(tz).hour >= day_start]
    dings = [e.ts.astimezone(tz) for e in todays if e.kind == "ding"]
    ding_minutes = [d.hour * 60 + d.minute for d in dings]

    facts: dict[str, Any] = {
        "date": day.isoformat(),
        "weekday": day.strftime("%A"),
        "first_activity": clock_dt(awake[0].ts.astimezone(tz)) if awake else None,
        "last_activity": clock_dt(awake[-1].ts.astimezone(tz)) if awake else None,
        "events_recorded": sum(e.kind not in ("offline", "online") for e in todays),
        "doorbell_rings": [clock_dt(d) for d in dings],
        "door_opens_in_quiet_hours": [clock_dt(e.ts.astimezone(tz)) for e in todays
                                      if e.kind == "contact_open" and home.in_quiet_hours(e.ts.astimezone(tz))],
        "device_offline_events": sum(e.kind == "offline" for e in todays),
        "alerts": [{"rule": a.rule, "reason": a.reason, "status": a.status, "acknowledged_by": a.acked_by}
                   for a in alerts if a.ts.astimezone(tz).date() == day],
    }
    if base:
        facts["learning"] = base.learning
        facts["usual_first_activity"] = clock(base.first_activity.p50)
        facts["usual_events_recorded"] = round(sum(base.hourly_rate[day.weekday()]))
        facts["usual_visits"] = [
            {"visit": v.label,
             "happened": any(v.window_start - VISIT_SLACK <= m <= v.window_end + VISIT_SLACK for m in ding_minutes)}
            for v in base.recurring_visits if day.weekday() in v.weekdays and v.hit_rate >= 0.8
        ]
    return facts


def baseline_facts(base: Baseline) -> dict[str, Any]:
    fa = base.first_activity
    return {
        "days_of_history": base.days,
        "learning": base.learning,
        "first_activity": {"typical": clock(fa.p50), "early": clock(fa.p10), "late_9_of_10_days": clock(fa.p90)},
        "usual_events_recorded_per_day": {
            name: round(sum(rates)) for name, rates in zip(WEEKDAYS, base.hourly_rate)},
        "recurring_visits": [{"visit": v.label, "weekdays": [WEEKDAYS[d] for d in v.weekdays], "how_often": f"{v.hit_rate:.0%}",
                              "window": f"{clock(v.window_start)}-{clock(v.window_end)}"} for v in base.recurring_visits],
        "night_door_open_rate": f"{base.night_door:.0%} of nights",
    }


def plain_digest(facts: dict) -> str:
    """Fallback digest when the model is unavailable: the same facts, no prose."""
    lines = [f"{facts['weekday']} {facts['date']}: first activity {facts['first_activity'] or 'not seen'}"
             + (f" (usually {facts['usual_first_activity']})" if facts.get("usual_first_activity") else "") + "."]
    lines += [f"The usual {v['visit']} {'happened' if v['happened'] else 'did not happen'}." for v in facts.get("usual_visits", [])]
    lines += [f"Alert: {a['reason'].rstrip('.')} ({a['status'].replace('_', ' ')})." for a in facts["alerts"]] or ["No alerts."]
    return " ".join(lines)
