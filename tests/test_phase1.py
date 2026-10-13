from datetime import date, timedelta

import pytest

from ring_routine_baseline.baseline import compute_baseline
from ring_routine_baseline.rules import evaluate
from simulator.evaluate import SCENARIOS, false_alerts, load_scenario, run_scenario
from simulator.generate_history import HOME, generate_history

DAY = date(2026, 10, 19)  # a Monday


@pytest.mark.parametrize("path", sorted(SCENARIOS.glob("*.yml")), ids=lambda p: p.stem)
def test_scenario_triggers_exactly_expected_rule(path):
    _, expect, _ = load_scenario(path)
    assert sorted(run_scenario(path)) == sorted(expect)


def test_false_alerts_over_28_normal_days():
    alerts = false_alerts(seed=0)
    print(f"\nfalse alerts over 28 normal days: {len(alerts)} {[(str(d), a.rule) for d, a in alerts]}")
    assert len(alerts) <= 1


def test_baseline_learns_routine():
    base = compute_baseline(HOME, generate_history(HOME, DAY, 28, seed=7), DAY)
    fa = base.first_activity
    assert not base.learning
    assert fa.p10 <= fa.p50 <= fa.p90 <= fa.p95 < 10 * 60
    [aide] = [v for v in base.recurring_visits if v.hit_rate >= 0.8]
    assert aide.weekdays == [0, 1, 2, 3, 4]
    assert aide.window_start <= 10 * 60 <= aide.window_end
    assert base.night_door == 0
    assert len(base.hourly_rate) == 7 and all(len(r) == 24 for r in base.hourly_rate)


def test_cold_start_uses_defaults():
    base = compute_baseline(HOME, generate_history(HOME, DAY, 3, seed=7), DAY)
    assert base.learning and base.first_activity.p95 == 600 and base.recurring_visits == []


def test_evaluate_ignores_future_events():
    path = SCENARIOS / "missed_morning.yml"
    day, _, events = load_scenario(path)
    base = compute_baseline(HOME, generate_history(HOME, day, 28, seed=7), day)
    nine_thirty = events[0].ts - timedelta(minutes=34)  # 09:30 local, aide not here yet
    assert [d.rule for d in evaluate(HOME, base, events, nine_thirty)] == ["missed_morning"]
