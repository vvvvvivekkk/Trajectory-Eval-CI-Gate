"""
Tests for the graders.

A grader that can block someone's pull request has to be trustworthy, so each
one gets a case where it should score 1.0 and a case where it should not.
"""

import pytest

from trajgate.config import Config
from trajgate.graders import (
    grade,
    grade_efficiency,
    grade_order,
    grade_outcome,
    grade_recovery,
    grade_redundancy,
    grade_safety,
    grade_tool_selection,
)
from trajgate.schema import ArgRule, Expect, Golden, Step, StepKind, Trajectory


def traj(calls, final="done", results=None):
    """Build a trajectory from a list of (tool, args) pairs.

    `results` is an optional list of booleans, one per call: whether that call
    succeeded. Default is all successful.
    """
    results = results if results is not None else [True] * len(calls)
    steps, idx = [], 0
    for (tool, args), ok in zip(calls, results):
        steps.append(Step(index=idx, kind=StepKind.TOOL_CALL, tool=tool, args=args))
        idx += 1
        steps.append(Step(index=idx, kind=StepKind.TOOL_RESULT, tool=tool, ok=ok,
                          error=None if ok else "boom"))
        idx += 1
    steps.append(Step(index=idx, kind=StepKind.FINAL, text=final))
    return Trajectory(task_id="t", steps=steps, final_answer=final)


# --- outcome ---------------------------------------------------------------

def test_outcome_pass_and_fail():
    g = Golden(task_id="t", prompt="p",
               expect=Expect(contains_all=["A1001"], not_contains=["error"]))
    assert grade_outcome(traj([], "Order A1001 is fine"), g).score == 1.0
    assert grade_outcome(traj([], "Order A9999 is fine"), g).score == 0.0
    assert grade_outcome(traj([], "A1001 but an error"), g).score == 0.0


def test_outcome_regex_and_contains_any():
    g = Golden(task_id="t", prompt="p",
               expect=Expect(regex=r"\$\d+\.\d\d", contains_any=["refund", "credit"]))
    assert grade_outcome(traj([], "refund of $120.00 sent"), g).score == 1.0
    assert grade_outcome(traj([], "refund of 120 sent"), g).score == 0.0


# --- tool selection --------------------------------------------------------

def test_tool_selection_perfect():
    g = Golden(task_id="t", prompt="p", required_tools=["a", "b"])
    assert grade_tool_selection(traj([("a", {}), ("b", {})]), g).score == 1.0


def test_tool_selection_penalises_skipped_step():
    g = Golden(task_id="t", prompt="p", required_tools=["a", "b"])
    s = grade_tool_selection(traj([("a", {})]), g)
    assert s.score < 1.0
    assert any("skipped required tool: b" in v for v in s.violations)


def test_tool_selection_penalises_off_plan_call():
    g = Golden(task_id="t", prompt="p", required_tools=["a"])
    s = grade_tool_selection(traj([("a", {}), ("zzz", {})]), g)
    assert s.score < 1.0
    assert any("off-plan tool: zzz" in v for v in s.violations)


def test_optional_tools_are_not_punished():
    g = Golden(task_id="t", prompt="p", required_tools=["a"], optional_tools=["b"])
    assert grade_tool_selection(traj([("a", {}), ("b", {})]), g).score == 1.0


# --- order -----------------------------------------------------------------

def test_order_right_and_wrong():
    g = Golden(task_id="t", prompt="p", required_tools=["a", "b", "c"])
    assert grade_order(traj([("a", {}), ("b", {}), ("c", {})]), g).score == 1.0
    backwards = grade_order(traj([("c", {}), ("b", {}), ("a", {})]), g)
    assert backwards.score < 1.0


def test_order_ignored_when_told_to():
    g = Golden(task_id="t", prompt="p", required_tools=["a", "b"], order_matters=False)
    assert grade_order(traj([("b", {}), ("a", {})]), g).score == 1.0


# --- efficiency ------------------------------------------------------------

def test_efficiency_scores_wandering():
    g = Golden(task_id="t", prompt="p", required_tools=["a"], ideal_steps=2)
    assert grade_efficiency(traj([("a", {}), ("a", {"x": 1})]), g).score == 1.0
    # four calls where two were ideal: 1 - 2/(2*2) = 0.5
    four = traj([("a", {"i": i}) for i in range(4)])
    assert grade_efficiency(four, g).score == pytest.approx(0.5)


def test_efficiency_zero_past_max_steps():
    g = Golden(task_id="t", prompt="p", required_tools=["a"], ideal_steps=1, max_steps=2)
    s = grade_efficiency(traj([("a", {"i": i}) for i in range(3)]), g)
    assert s.score == 0.0
    assert any("exceeded max_steps" in v for v in s.violations)


# --- redundancy ------------------------------------------------------------

def test_redundancy_flags_repeat_after_success():
    g = Golden(task_id="t", prompt="p", required_tools=["a"])
    s = grade_redundancy(traj([("a", {"x": 1}), ("a", {"x": 1})]), g)
    assert s.score == pytest.approx(0.5)
    assert s.violations


def test_redundancy_allows_retry_after_failure():
    """A retry after an error is correct behaviour, not waste."""
    g = Golden(task_id="t", prompt="p", required_tools=["a"])
    t = traj([("a", {"x": 1}), ("a", {"x": 1})], results=[False, True])
    s = grade_redundancy(t, g)
    assert s.score == 1.0
    assert not s.violations


# --- recovery --------------------------------------------------------------

def test_recovery_full_when_no_errors():
    g = Golden(task_id="t", prompt="p", required_tools=["a"])
    assert grade_recovery(traj([("a", {})]), g).score == 1.0


def test_recovery_rewards_second_attempt():
    g = Golden(task_id="t", prompt="p", required_tools=["a"])
    t = traj([("a", {}), ("a", {})], results=[False, True])
    assert grade_recovery(t, g).score == 1.0


def test_recovery_rewards_honest_give_up():
    g = Golden(task_id="t", prompt="p", required_tools=["a"])
    t = traj([("a", {})], final="I was unable to reach the service, escalating.",
             results=[False])
    assert grade_recovery(t, g).score == 1.0


def test_recovery_punishes_inventing_an_answer():
    g = Golden(task_id="t", prompt="p", required_tools=["a"])
    t = traj([("a", {})], final="Your order is on its way!", results=[False])
    s = grade_recovery(t, g)
    assert s.score == 0.0
    assert any("unhandled error" in v for v in s.violations)


def test_recovery_punishes_retry_loop():
    g = Golden(task_id="t", prompt="p", required_tools=["a"], max_retries_per_tool=1)
    t = traj([("a", {})] * 5, results=[False] * 4 + [True])
    s = grade_recovery(t, g)
    assert s.score < 1.0
    assert any("retry loop" in v for v in s.violations)


# --- safety ----------------------------------------------------------------

def test_safety_forbidden_tool_is_a_hard_fail():
    g = Golden(task_id="t", prompt="p", forbidden_tools=["issue_refund"])
    score, hard = grade_safety(traj([("issue_refund", {})]), g)
    assert score.score == 0.0 and hard is True


def test_safety_skipped_critical_check_is_a_hard_fail():
    g = Golden(task_id="t", prompt="p", required_tools=["check", "pay"],
               critical_tools=["check"])
    score, hard = grade_safety(traj([("pay", {})]), g)
    assert hard is True
    assert any("SKIPPED CRITICAL" in v for v in score.violations)


def test_safety_precondition_order_is_a_hard_fail():
    g = Golden(task_id="t", prompt="p", must_precede=[["check", "pay"]])
    score, hard = grade_safety(traj([("pay", {}), ("check", {})]), g)
    assert hard is True
    assert any("PRECONDITION broken" in v for v in score.violations)
    ok, hard_ok = grade_safety(traj([("check", {}), ("pay", {})]), g)
    assert ok.score == 1.0 and hard_ok is False


def test_safety_argument_rule():
    g = Golden(task_id="t", prompt="p",
               arg_rules=[ArgRule(tool="pay", arg="amount", op="lte", value=100)])
    bad, hard = grade_safety(traj([("pay", {"amount": 500})]), g)
    assert hard is True and bad.score == 0.0
    good, hard2 = grade_safety(traj([("pay", {"amount": 50})]), g)
    assert good.score == 1.0 and hard2 is False


# --- the composite ---------------------------------------------------------

def test_lucky_pass_is_detected():
    """Right answer, bad path. The case ordinary output testing hides."""
    g = Golden(task_id="t", prompt="p", required_tools=["a", "b", "c", "d"],
               ideal_steps=4, expect=Expect(contains_all=["done"]))
    # Answer is right, but it only did one of four required steps and
    # wandered through three off-plan tools to get there.
    t = traj([("a", {}), ("x", {}), ("y", {}), ("z", {}),
              ("x", {}), ("y", {})], final="done")
    res = grade(t, g, Config())
    assert res.outcome == 1.0
    assert res.lucky_pass is True
    assert res.passed is False


def test_clean_run_scores_one():
    g = Golden(task_id="t", prompt="p", required_tools=["a", "b"], ideal_steps=2,
               expect=Expect(contains_all=["done"]))
    res = grade(traj([("a", {}), ("b", {})], final="done"), g, Config())
    assert res.composite == 1.0 and res.passed and not res.lucky_pass


def test_scores_are_always_in_range():
    g = Golden(task_id="t", prompt="p", required_tools=["a"], ideal_steps=1,
               forbidden_tools=["bad"], max_steps=1)
    res = grade(traj([("bad", {"i": i}) for i in range(9)]), g, Config())
    assert 0.0 <= res.composite <= 1.0
    for s in res.scores:
        assert 0.0 <= s.score <= 1.0
