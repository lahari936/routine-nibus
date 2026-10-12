"""Baseline computation. Pure functions over a list of events, in the home's local time."""
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from statistics import median, quantiles

from core.explain import clock
from core.models import Baseline, Event, FirstActivity, Home, RecurringVisit, minute_of_day

MIN_DAYS = 7  # below this the baseline is "learning"
DEFAULT_FIRST_ACTIVITY = 10 * 60
VISIT_BIN = 15  # minutes
VISIT_SLACK = 30  # a ding this far outside the window still counts as the visit


def compute_baseline(home: Home, events: list[Event], end: date, days: int = 28) -> Baseline:
    """Baseline from events on local dates [end - days, end)."""
    start = end - timedelta(days=days)
    by_date: dict[date, list[tuple[datetime, Event]]] = defaultdict(list)
    for e in sorted(events, key=lambda e: e.ts):
        local = e.ts.astimezone(home.tz)
        if start <= local.date() < end:
            by_date[local.date()].append((local, e))
    dates = sorted(by_date)
    n = len(dates)

    return Baseline(
        home_id=home.id,
        days=n,
        first_activity=_first_activity(home, by_date, n),
        hourly_rate=_hourly_rate(by_date),
        recurring_visits=_recurring_visits(by_date, dates) if n >= MIN_DAYS else [],
        night_door=_night_door(home, by_date, n),
    )


def _first_activity(home: Home, by_date, n: int) -> FirstActivity:
    day_start = home.quiet_hours[1] * 60
    firsts = []
    for items in by_date.values():
        mins = [minute_of_day(l) for l, e in items if e.is_activity and minute_of_day(l) >= day_start]
        if mins:
            firsts.append(min(mins))
    if n < MIN_DAYS or len(firsts) < 2:
        d = DEFAULT_FIRST_ACTIVITY
        return FirstActivity(p10=d, p50=d, p90=d, p95=d)
    q = quantiles(firsts, n=20, method="inclusive")  # 5%, 10%, ..., 95%
    return FirstActivity(p10=q[1], p50=q[9], p90=q[17], p95=q[18])


def _hourly_rate(by_date) -> list[list[float]]:
    counts = [[0.0] * 24 for _ in range(7)]
    days_per_wd = Counter(d.weekday() for d in by_date)
    for d, items in by_date.items():
        for local, e in items:
            if e.kind not in ("offline", "online"):
                counts[d.weekday()][local.hour] += 1
    rates = []
    for wd in range(7):
        c = [x / days_per_wd[wd] if days_per_wd[wd] else 0.0 for x in counts[wd]]
        rates.append([0.25 * c[h - 1] + 0.5 * c[h] + 0.25 * c[(h + 1) % 24] for h in range(24)])
    return rates


def _recurring_visits(by_date, dates: list[date]) -> list[RecurringVisit]:
    # Visits are found from doorbell presses: 15-minute bins that collect a ding on many days,
    # merged into windows, then scored per weekday.
    dings = {d: [minute_of_day(l) for l, e in by_date[d] if e.kind == "ding"] for d in dates}
    counts = Counter(int(m // VISIT_BIN) for ms in dings.values() for m in ms)
    threshold = max(3, 0.2 * len(dates))
    runs: list[list[int]] = []
    for b in sorted(b for b, c in counts.items() if c >= threshold):
        if runs and b == runs[-1][1] + 1:
            runs[-1][1] = b
        else:
            runs.append([b, b])

    visits = []
    for b0, b1 in runs:
        ws, we = b0 * VISIT_BIN, (b1 + 1) * VISIT_BIN
        matches = {d: [m for m in dings[d] if ws - VISIT_SLACK <= m <= we + VISIT_SLACK] for d in dates}
        by_wd: dict[int, list[date]] = defaultdict(list)
        for d in dates:
            by_wd[d.weekday()].append(d)
        weekdays = sorted(
            wd for wd, ds in by_wd.items()
            if len(ds) >= 2 and sum(bool(matches[d]) for d in ds) / len(ds) >= 0.5
        )
        if not weekdays:
            continue
        sel = [d for d in dates if d.weekday() in weekdays]
        hit_rate = sum(bool(matches[d]) for d in sel) / len(sel)
        typical = median(matches[d][0] for d in sel if matches[d])
        visits.append(RecurringVisit(
            label=f"{clock(round(typical / 5) * 5)} visit",
            weekdays=weekdays, window_start=ws, window_end=we, hit_rate=round(hit_rate, 3),
        ))
    return visits


def _night_door(home: Home, by_date, n: int) -> float:
    if not n:
        return 0.0
    # A "night" is keyed by the date it started on (02:00 belongs to the previous evening).
    shift = timedelta(hours=home.quiet_hours[1])
    nights = {
        (local - shift).date()
        for items in by_date.values() for local, e in items
        if e.kind == "contact_open" and home.in_quiet_hours(local)
    }
    return len(nights) / n
