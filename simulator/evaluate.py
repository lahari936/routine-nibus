"""Replay days through the rules the way the 15-minute scheduler would.

python -m simulator.evaluate        # scenario results + false-alert count over normal days
"""
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

import yaml

from core.baseline import compute_baseline
from core.models import Deviation, Event, Home
from core.rules import evaluate
from simulator.generate_history import HOME, generate_history, make_event

SCENARIOS = Path(__file__).parent / "scenarios"
CHECK_EVERY = timedelta(minutes=15)


def replay_day(home: Home, base, events: list[Event], day: date) -> dict[str, tuple[datetime, Deviation]]:
    """First firing of each rule during `day`, checking every 15 minutes."""
    t = datetime.combine(day, time(), tzinfo=home.tz).astimezone(timezone.utc)
    end = datetime.combine(day + timedelta(days=1), time(), tzinfo=home.tz).astimezone(timezone.utc)
    fired: dict[str, tuple[datetime, Deviation]] = {}
    while t < end:
        for d in evaluate(home, base, events, t):
            fired.setdefault(d.rule, (t, d))
        t += CHECK_EVERY
    return fired


def load_scenario(path: Path, home: Home = HOME) -> tuple[date, list[str], list[Event]]:
    spec = yaml.safe_load(path.read_text())
    day: date = spec["date"]
    events = []
    for i, (hhmm, device, kind) in enumerate(spec["events"]):
        h, m = map(int, hhmm.split(":"))
        events.append(make_event(home, day, h * 60 + m, device, kind, i))
    return day, spec["expect"], events


def run_scenario(path: Path, home: Home = HOME, seed: int = 7) -> dict[str, tuple[datetime, Deviation]]:
    day, _, events = load_scenario(path, home)
    history = generate_history(home, day, 28, seed)
    base = compute_baseline(home, history, day)
    yesterday = [e for e in history if e.ts.astimezone(home.tz).date() == day - timedelta(days=1)]
    return replay_day(home, base, yesterday + events, day)


def false_alerts(home: Home = HOME, seed: int = 0, days: int = 28, start: date = date(2026, 10, 1)) -> list[tuple[date, Deviation]]:
    """Alerts raised over `days` normal synthetic days, each judged against the prior 28 days."""
    history = generate_history(home, start + timedelta(days=28 + days), 28 + days, seed)
    alerts = []
    for i in range(days):
        day = start + timedelta(days=28 + i)
        base = compute_baseline(home, history, day)
        recent = [e for e in history if day - timedelta(days=1) <= e.ts.astimezone(home.tz).date() <= day]
        alerts += [(day, d) for _, d in replay_day(home, base, recent, day).values()]
    return alerts


if __name__ == "__main__":
    for path in sorted(SCENARIOS.glob("*.yml")):
        _, expect, _ = load_scenario(path)
        fired = run_scenario(path)
        ok = "PASS" if sorted(fired) == sorted(expect) else "FAIL"
        print(f"{ok} {path.stem:22} expected {expect} got {sorted(fired)}")
        for rule, (t, d) in fired.items():
            print(f"     {t.astimezone(HOME.tz):%H:%M} {rule}: {d.reason}")
    print()
    for seed in range(10):
        alerts = false_alerts(seed=seed)
        print(f"seed {seed}: {len(alerts)} false alerts over 28 normal days "
              + "; ".join(f"{d} {a.rule}" for d, a in alerts))
