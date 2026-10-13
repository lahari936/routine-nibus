# ring-routine-baseline

Learn a household's normal daily rhythm from Ring events (motion, doorbell presses, door contact sensors) and flag meaningful deviations, each with a one-sentence reason a family member can read. No video, no machine-learning black box: every rule is a plain function you can read and test.

It is the core of [Routine Guardian](https://github.com/lahari936/routine-nibus), a caregiver service for older people who live alone.

```bash
pip install ring-routine-baseline
```

## What's inside

| Module | What it does |
|---|---|
| `models` | Pydantic `Event`, `Home`, `Baseline`, `Deviation`, `Alert` |
| `baseline` | `compute_baseline(home, events, end)`: first-activity percentiles, hourly rates per weekday, recurring visits (e.g. an aide every weekday at 10:00), night door-open rate. Uses the last 28 days, in the home's time zone. |
| `rules` | `evaluate(home, baseline, events, now)` runs five rules: `missed_morning`, `missed_visit`, `night_door`, `unanswered_doorbell`, `device_offline` |
| `explain` | Reasons such as "No activity yet today. Usually first active by 8:10 AM (9 of 10 days)." |
| `facts` | Structured day and baseline summaries to hand to an LLM, so it narrates facts and never decides alerts |
| `webhook` | Ring webhook (payload v1.1) HMAC verification and normalization to `Event`, with idempotent handling |
| `ring_client` | Small stdlib client for the Ring (Amazon Vision) API: OAuth tokens with rotating refresh, devices, event history, snapshots |
| `linking` | One-way account-linking nonce and freshness checks |

## Quick start

```python
from datetime import date, datetime, timezone
from ring_routine_baseline.baseline import compute_baseline
from ring_routine_baseline.models import Event, Home
from ring_routine_baseline.rules import evaluate

home = Home(id="home-1", name="Mom's house", timezone="America/New_York")
events: list[Event] = load_events()  # your storage; Event.indoor marks sensors that mean someone is home

baseline = compute_baseline(home, events, end=date(2026, 10, 19))  # learns from Sep 21 to Oct 18
for deviation in evaluate(home, baseline, events, now=datetime.now(timezone.utc)):
    print(deviation.rule, deviation.severity, deviation.reason)
```

With fewer than 7 days of history the baseline is in "learning" mode: it expects first activity by 10:00 AM and doesn't infer recurring visits.

Verifying a Ring webhook (always against the raw request body):

```python
from ring_routine_baseline.webhook import verify_signature, normalize

if not verify_signature(hmac_key, raw_body, headers.get("X-Signature")):
    return 401
event = normalize(json.loads(raw_body), home_id="home-1", outdoor_devices={"front-doorbell-id"})
```

## How well it works

On the bundled simulator (28 days of synthetic history with realistic jitter, weekday aide visits and different weekends), every scenario triggers exactly its expected rule. Over 280 normal synthetic days it raised one false alert: a late Sunday sleep-in. See `simulator/` in the source repo.

## License

MIT
