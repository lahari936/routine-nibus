"""Write web/public/demo.json: a week of synthetic data so the dashboard runs without AWS.

  python -m tools.export_demo [--today 2026-10-19]

The week is normal except for two scenario days: the aide misses a visit 3 days before --today
(acknowledged by Caregiver 1) and nobody is up yet on --today itself (alert still open).
"""
import argparse
import json
from datetime import date, datetime, time, timedelta
from pathlib import Path

from core.baseline import compute_baseline
from core.facts import day_summary, plain_digest
from core.models import Alert, Caregiver
from simulator.evaluate import SCENARIOS, load_scenario, replay_day
from simulator.generate_history import HOME, generate_history

OUT = Path(__file__).resolve().parent.parent / "web" / "public" / "demo.json"


def scenario_on(name: str, day: date):
    scenario_day, _, events = load_scenario(SCENARIOS / f"{name}.yml", HOME)
    shift = day - scenario_day
    return [e.model_copy(update={"ts": e.ts + shift, "event_id": f"{e.event_id}-{day}"}) for e in events]


def build(today: date) -> dict:
    home = HOME.model_copy(update={"id": "demo-home", "caregivers": [
        Caregiver(name="Caregiver 1", email="cg1@example.com", phone="+15555550100"),
        Caregiver(name="Caregiver 2", email="cg2@example.com")]})
    history = generate_history(home, today + timedelta(days=1), 35, seed=7)
    local_day = lambda e: e.ts.astimezone(home.tz).date()
    special = {today - timedelta(days=3): "missed_aide", today: "missed_morning"}
    events = [e for e in history if local_day(e) not in special]
    for day, name in special.items():
        events += [e.model_copy(update={"home_id": home.id}) for e in scenario_on(name, day)]
    # The demo's "now" is 9:45 AM on --today: nothing after that has happened yet.
    now = datetime.combine(today, time(9, 45), tzinfo=home.tz)
    events = sorted((e for e in events if e.ts <= now), key=lambda e: e.ts)

    alerts = []
    for i in range(6, -1, -1):
        day = today - timedelta(days=i)
        base = compute_baseline(home, events, day)
        for t, d in replay_day(home, base, events, day).values():
            if t <= now:
                alerts.append(Alert(home_id=home.id, ts=t, **d.model_dump(),
                                    status="acked" if day < today else "open",
                                    acked_by="Caregiver 1" if day < today else None))

    base = compute_baseline(home, events, today)
    yesterday = today - timedelta(days=1)
    facts = day_summary(home, base, events, alerts, yesterday)
    week = [e for e in events if local_day(e) > today - timedelta(days=7)]
    return {
        "today": today.isoformat(),
        "home": home.model_dump(mode="json"),
        "baseline": base.model_dump(mode="json"),
        "events": [e.model_dump(mode="json", exclude={"raw"}) for e in week],
        "alerts": [a.model_dump(mode="json") for a in alerts],
        "digest": {"date": yesterday.isoformat(), "text": plain_digest(facts), "facts": facts},
    }


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--today", type=date.fromisoformat, default=date(2026, 10, 19))
    data = build(p.parse_args().today)
    OUT.write_text(json.dumps(data, indent=1))
    print(f"wrote {OUT} ({len(data['events'])} events, alerts: {[(a['ts'][:10], a['rule']) for a in data['alerts']]})")
