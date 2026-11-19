"""Tests for the gate: given a baseline and a new run, does it block?"""

from trajgate.config import Config
from trajgate.gate import evaluate
from trajgate.schema import GraderScore, SuiteResult, TaskResult


def task(task_id="t", composite=1.0, passed=True, path=1.0, outcome=1.0,
         hard=False, lucky=False, calls=3, tokens=1000):
    return TaskResult(
        task_id=task_id, agent_version="v", composite=composite,
        path_score=path, outcome=outcome, passed=passed, hard_fail=hard,
        lucky_pass=lucky, steps=calls * 2, tool_calls=calls, tokens=tokens,
        scores=[GraderScore(name="safety", score=0.0 if hard else 1.0, weight=0.05,
                            violations=["called FORBIDDEN tool: issue_refund"]
                            if hard else [])],
    )


def suite(tasks, version="v"):
    return SuiteResult(agent_version=version, created_at="now", tasks=tasks)


def test_identical_run_is_allowed():
    base = suite([task()])
    d = evaluate(suite([task()]), base, Config())
    assert d.blocked is False and d.exit_code == 0


def test_small_dip_is_tolerated():
    # 0.015 is inside both the per-task (0.05) and suite-average (0.02) limits.
    base = suite([task(composite=1.0)])
    d = evaluate(suite([task(composite=0.985)]), base, Config())
    assert d.blocked is False
    assert any("within tolerance" in w for w in d.warnings)


def test_big_drop_blocks():
    base = suite([task(composite=1.0)])
    d = evaluate(suite([task(composite=0.80)]), base, Config())
    assert d.blocked is True and d.exit_code == 1
    assert any("score dropped" in r for r in d.reasons)


def test_pass_to_fail_flip_blocks():
    base = suite([task(composite=0.85, passed=True)])
    d = evaluate(suite([task(composite=0.84, passed=False)]), base, Config())
    assert d.blocked is True
    assert any("passing to failing" in r for r in d.reasons)


def test_hard_fail_blocks_and_quotes_the_rule():
    base = suite([task()])
    d = evaluate(suite([task(hard=True)]), base, Config())
    assert d.blocked is True
    assert any("FORBIDDEN tool: issue_refund" in r for r in d.reasons)


def test_new_lucky_pass_blocks():
    base = suite([task(lucky=False)])
    d = evaluate(suite([task(lucky=True, path=0.5)]), base, Config())
    assert d.blocked is True
    assert any("LUCKY PASS" in r for r in d.reasons)


def test_pre_existing_lucky_pass_only_warns():
    """If the baseline was already lucky, don't block the next PR for it -
    block the thing that got worse, not the thing that was already wrong."""
    base = suite([task(lucky=True, path=0.5)])
    d = evaluate(suite([task(lucky=True, path=0.5)]), base, Config())
    assert d.blocked is False
    assert any("still a lucky pass" in w for w in d.warnings)


def test_cost_creep_blocks():
    base = suite([task(tokens=1000)])
    d = evaluate(suite([task(tokens=2000)]), base, Config())
    assert d.blocked is True
    assert any("token use up" in r for r in d.reasons)


def test_step_creep_blocks():
    base = suite([task(calls=3)])
    d = evaluate(suite([task(calls=6)]), base, Config())
    assert d.blocked is True
    assert any("tool calls up" in r for r in d.reasons)


def test_mean_drop_blocks_even_when_each_task_is_within_tolerance():
    """Death by a thousand cuts: four tasks each dipping 0.04 is fine
    per-task but a 0.04 drop in the suite average, which is not."""
    base = suite([task(f"t{i}", composite=1.0) for i in range(4)])
    cur = suite([task(f"t{i}", composite=0.96) for i in range(4)])
    d = evaluate(cur, base, Config())
    assert d.blocked is True
    assert any("suite average dropped" in r for r in d.reasons)


def test_no_baseline_does_not_block():
    d = evaluate(suite([task()]), None, Config())
    assert d.blocked is False
    assert d.has_baseline is False
    assert any("no baseline" in w for w in d.warnings)


def test_new_task_does_not_block():
    base = suite([task("old")])
    d = evaluate(suite([task("old"), task("brand_new")]), base, Config())
    assert d.blocked is False
    assert any("new task" in w for w in d.warnings)


def test_absolute_floor_can_block():
    cfg = Config()
    cfg.thresholds.min_mean_score = 0.9
    d = evaluate(suite([task(composite=0.5, passed=False)]), None, cfg)
    assert d.blocked is True
    assert any("below the absolute floor" in r for r in d.reasons)
