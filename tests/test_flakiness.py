"""
Tests for stability across repeats.

The point: an agent that scores 0.95 once and 0.55 the next time is not a
0.75 agent. Reporting the median without reporting the spread hides the only
number that tells you whether to trust the verdict.
"""

from pathlib import Path

import pytest

from trajgate import gate, runner
from trajgate.config import Config
from trajgate.schema import GraderScore, SuiteResult, TaskResult

ROOT = Path(__file__).resolve().parents[1]


def task(task_id="x", composite=1.0, runs=1, run_scores=None, spread=0.0,
         flaky=False, passed=True, pass_rate=1.0):
    return TaskResult(
        task_id=task_id, agent_version="v", composite=composite,
        path_score=1.0, outcome=1.0, passed=passed, tool_calls=3, tokens=1000,
        usd_cost=0.01, runs=runs, run_scores=run_scores or [composite],
        spread=spread, stability=1 - spread, run_pass_rate=pass_rate,
        flaky=flaky,
        scores=[GraderScore(name="safety", score=1.0, weight=0.05)])


def suite(*tasks):
    return SuiteResult(agent_version="v", created_at="now", tasks=list(tasks))


# --- measuring --------------------------------------------------------------

def test_single_run_has_no_spread():
    cfg = Config.load(ROOT / "config.yaml")
    cfg.repeats = 1
    goldens = runner.load_goldens(ROOT / "goldens")
    s = runner.run_suite(goldens, cfg, agent_version="v1.0-good")
    assert s.repeats == 1
    assert all(t.spread == 0.0 and t.flaky is False for t in s.tasks)


def test_a_stable_agent_repeated_is_still_stable():
    cfg = Config.load(ROOT / "config.yaml")
    cfg.repeats = 4
    goldens = runner.load_goldens(ROOT / "goldens")
    s = runner.run_suite(goldens, cfg, agent_version="v1.0-good")
    assert s.repeats == 4
    for t in s.tasks:
        assert t.spread == 0.0
        assert t.stability == 1.0
        assert t.flaky is False
        assert len(t.run_scores) == 4


def test_the_unstable_agent_is_detected():
    cfg = Config.load(ROOT / "config.yaml")
    cfg.repeats = 5
    goldens = runner.load_goldens(ROOT / "goldens")
    s = runner.run_suite(goldens, cfg, agent_version="v1.3-flaky")
    assert s.flaky_tasks, "an agent that behaves differently per run must show up"
    for t in s.flaky_tasks:
        assert t.spread > 0 or t.run_pass_rate not in (0.0, 1.0)
        assert any("UNSTABLE" in n for n in t.notes)


def test_the_median_is_reported_not_the_best():
    """Reporting your best run is how teams lie to themselves.

    The median can legitimately equal the best run (when most runs are
    clean), so the property to assert is that the reported score IS the
    middle one - not that it is lower than the maximum.
    """
    cfg = Config.load(ROOT / "config.yaml")
    cfg.repeats = 5
    goldens = runner.load_goldens(ROOT / "goldens")
    s = runner.run_suite(goldens, cfg, agent_version="v1.3-flaky")
    for t in s.tasks:
        expected = sorted(t.run_scores)[len(t.run_scores) // 2]
        assert t.composite == pytest.approx(expected), t.task_id

    # And across the suite, at least one task must be reported below its own
    # best run - otherwise "median" is doing no work at all.
    assert any(t.composite < max(t.run_scores) for t in s.tasks)


def test_different_routes_across_runs_are_reported():
    cfg = Config.load(ROOT / "config.yaml")
    cfg.repeats = 6
    goldens = runner.load_goldens(ROOT / "goldens")
    s = runner.run_suite(goldens, cfg, agent_version="v1.3-flaky")
    two_routes = s.by_id()["order_status_two_routes"]
    assert len(two_routes.routes_seen) == 2
    assert any("different accepted routes" in n for n in two_routes.notes)


def test_disagreeing_runs_count_as_unstable_even_with_a_small_spread():
    """If some runs pass and some fail, the spread size is beside the point."""
    cfg = Config()
    cfg.thresholds.max_run_spread = 0.9      # deliberately generous
    res = task(composite=0.81)
    runs = [task(composite=0.81, passed=True), task(composite=0.79, passed=False),
            task(composite=0.80, passed=True)]
    runner._attach_stability(res, runs, cfg)
    assert res.flaky is True
    assert res.run_pass_rate == pytest.approx(2 / 3, abs=1e-3)
    assert any("passed 2 of 3 runs" in n for n in res.notes)


# --- gating -----------------------------------------------------------------

def test_a_task_that_became_unstable_blocks():
    base = suite(task(runs=5, run_scores=[1.0] * 5))
    cur = suite(task(runs=5, run_scores=[1.0, 0.6, 1.0, 0.6, 1.0], spread=0.4,
                     flaky=True))
    d = gate.evaluate(cur, base, Config())
    assert d.blocked is True
    assert any("became UNSTABLE" in r for r in d.reasons)


def test_an_already_unstable_task_only_warns():
    """Block what got worse, not what was already wrong."""
    base = suite(task(runs=5, spread=0.4, flaky=True))
    cur = suite(task(runs=5, spread=0.4, flaky=True))
    d = gate.evaluate(cur, base, Config())
    assert d.blocked is False
    assert any("still unstable" in w for w in d.warnings)


def test_stability_gating_can_be_switched_off():
    cfg = Config()
    cfg.thresholds.block_on_new_flaky = False
    base = suite(task(runs=5))
    cur = suite(task(runs=5, spread=0.5, flaky=True))
    assert gate.evaluate(cur, base, cfg).blocked is False


# --- reporting --------------------------------------------------------------

def test_reports_include_the_stability_table():
    from trajgate.report import html_report, markdown, terminal
    cfg = Config.load(ROOT / "config.yaml")
    cfg.repeats = 3
    goldens = runner.load_goldens(ROOT / "goldens")
    s = runner.run_suite(goldens, cfg, agent_version="v1.3-flaky")
    base = runner.run_suite(goldens, Config.load(ROOT / "config.yaml"),
                            agent_version="v1.0-good")
    d = gate.evaluate(s, base, cfg)
    assert "stability across 3 runs per task" in terminal(s, d)
    assert "Stability across 3 runs" in markdown(s, d)
    assert "unstable task" in terminal(s, d)
    html_report(s, d)  # must not raise


def test_flaky_tasks_are_flagged_in_the_table():
    from trajgate.report import _flags
    assert "FLAKY" in _flags(task(flaky=True))
    assert "FLAKY" not in _flags(task())
