"""Synthetic Ring event history for one elderly person living alone.

Weekdays: up around 7:30, aide rings at ~10:00 and stays ~80 min.
Weekends: up later, no aide, family sometimes visits Sunday afternoon.
Plus deliveries, passing cars on the doorbell, night bathroom trips, afternoon outings
and the occasional short doorbell Wi-Fi drop.
"""
import argparse
import json
import random
from datetime import date, datetime, time, timedelta, timezone

from core.models import Event, EventKind, Home

HOME = Home(id="home-1", name="Mom's house", timezone="America/New_York")
DEVICES = {"doorbell": False, "front_door": True, "hall_motion": True, "kitchen_motion": True}


def make_event(home: Home, day: date, minute: float, device: str, kind: EventKind, idx: int) -> Event:
    local = datetime.combine(day, time(), tzinfo=home.tz) + timedelta(minutes=minute)
    return Event(
        event_id=f"{home.id}-{day.isoformat()}-{idx:03d}",
        home_id=home.id, device_id=device, kind=kind,
        ts=local.astimezone(timezone.utc), indoor=DEVICES[device],
    )


def generate_day(home: Home, day: date, rng: random.Random) -> list[Event]:
    out: list[tuple[float, str, EventKind]] = []
    add = lambda m, dev, kind: out.append((m, dev, kind))

    def burst(m: float, n: int, dev: str = "hall_motion") -> None:
        for _ in range(n):
            add(m, dev, "motion_human")
            m += rng.uniform(1, 6)

    def door(m: float) -> None:
        add(m, "front_door", "contact_open")
        add(m + rng.uniform(0.3, 2), "front_door", "contact_close")

    weekend = day.weekday() >= 5
    wake = rng.gauss(495 if weekend else 450, 25 if weekend else 18)
    bed = rng.gauss(1290, 20)
    outing = None
    if rng.random() < 0.25:
        outing = (s := rng.uniform(780, 900), s + rng.uniform(60, 120))
    home_at = lambda m: not (outing and outing[0] <= m <= outing[1])

    if rng.random() < 0.4:  # night bathroom trip
        burst(rng.uniform(120, 260), 2)
    burst(wake, 4)
    burst(wake + rng.uniform(15, 40), 3, "kitchen_motion")
    if rng.random() < 0.7:  # fetch the paper
        door(wake + rng.uniform(25, 50))

    if not weekend:  # aide
        a = rng.gauss(600, 8)
        add(a - 0.5, "doorbell", "motion_human")
        add(a, "doorbell", "ding")
        door(a + rng.uniform(0.5, 2))
        leave = a + rng.uniform(70, 90)
        m = a + 5
        while m < leave:
            add(m, rng.choice(["hall_motion", "kitchen_motion"]), "motion_human")
            m += rng.uniform(5, 15)
        door(leave)

    for h in range(12, 21):  # daytime pottering
        if rng.random() < 0.75:
            burst(h * 60 + rng.uniform(0, 50), rng.randint(1, 3), rng.choice(["hall_motion", "kitchen_motion"]))

    if outing:
        door(outing[0])
        door(outing[1])
        burst(outing[1] + 1, 2)

    if rng.random() < 0.35:  # delivery
        m = rng.uniform(660, 1080)
        add(m - 0.5, "doorbell", "motion_human")
        add(m, "doorbell", "ding")
        if rng.random() < 0.2:
            add(m + rng.uniform(0.5, 1.5), "doorbell", "ding")
        if home_at(m):
            door(m + rng.uniform(1, 3))

    if day.weekday() == 6 and rng.random() < 0.5:  # family visit
        m = rng.gauss(840, 60)
        add(m, "doorbell", "ding")
        door(m + 1)
        burst(m + 3, 5)

    for _ in range(rng.randint(2, 6)):  # cars, passers-by
        add(rng.uniform(360, 1320), "doorbell", "motion_other")

    burst(bed - rng.uniform(10, 30), 3)

    if rng.random() < 0.05:  # short Wi-Fi drop
        m = rng.uniform(0, 1300)
        add(m, "doorbell", "offline")
        add(m + rng.uniform(10, 40), "doorbell", "online")

    # Nobody moves inside while out (except the door itself).
    out = [x for x in out if home_at(x[0]) or x[1] in ("doorbell", "front_door")]
    out.sort()
    return [make_event(home, day, min(max(m, 0), 1439.9), dev, kind, i) for i, (m, dev, kind) in enumerate(out)]


def generate_history(home: Home, end: date, days: int = 28, seed: int = 0) -> list[Event]:
    """Events for local dates [end - days, end)."""
    rng = random.Random(seed)
    return [e for i in range(days) for e in generate_day(home, end - timedelta(days=days - i), rng)]


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--end", type=date.fromisoformat, default=date.today())
    p.add_argument("--days", type=int, default=28)
    p.add_argument("--seed", type=int, default=0)
    a = p.parse_args()
    for e in generate_history(HOME, a.end, a.days, a.seed):
        print(json.dumps(e.model_dump(mode="json")))
