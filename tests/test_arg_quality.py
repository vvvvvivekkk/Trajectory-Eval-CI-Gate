"""
Tests for argument grading.

Two different failures live here:
  - a useless argument (an unsubstituted "{{query}}" template)
  - an INVENTED argument (a number the model produced rather than retrieved)

Both call exactly the right tool, so neither is visible to tool-name grading.
"""

from pathlib import Path

import pytest

from trajgate import runner
from trajgate.config import Config
from trajgate.graders import grade_arg_quality, looks_like_placeholder
from trajgate.schema import ArgQuality, Golden, Step, StepKind, Trajectory

ROOT = Path(__file__).resolve().parents[1]


def run(calls_with_outputs, final="done"):
    """calls_with_outputs: list of (tool, args, output_or_None_for_error)."""
    steps, idx = [], 0
    for tool, args, out in calls_with_outputs:
        steps.append(Step(index=idx, kind=StepKind.TOOL_CALL, tool=tool, args=args))
        idx += 1
        steps.append(Step(index=idx, kind=StepKind.TOOL_RESULT, tool=tool,
                          ok=out is not None, output=out,
                          error=None if out is not None else "boom"))
        idx += 1
    return Trajectory(task_id="t", steps=steps, final_answer=final)


# --- placeholder detection -------------------------------------------------

@pytest.mark.parametrize("value", [
    "{{query}}", "{order_id}", "<order_id>", "$ORDER_ID", "TODO", "placeholder",
    "null", "undefined", "n/a", "your_api_key", "", "   ", None,
])
def test_placeholders_are_recognised(value):
    assert looks_like_placeholder(value) is True


@pytest.mark.parametrize("value", [
    "password reset", "A1001", "priya@example.com", 120.0, "how do I log in",
])
def test_real_values_are_not_flagged(value):
    assert looks_like_placeholder(value) is False


# --- the grader ------------------------------------------------------------

def test_no_expectations_is_a_no_op():
    g = Golden(task_id="t", prompt="p")
    assert grade_arg_quality(run([("search_kb", {"query": "x"}, "ok")]), g).score == 1.0


def test_unsubstituted_template_is_caught():
    g = Golden(task_id="t", prompt="how do I reset my password?",
               arg_quality=[ArgQuality(tool="search_kb", arg="query",
                                       must_contain_any=["password"])])
    s = grade_arg_quality(run([("search_kb", {"query": "{{query}}"}, "ok")]), g)
    assert s.score < 1.0
    assert any("placeholder" in v for v in s.violations)


def test_good_query_scores_full():
    g = Golden(task_id="t", prompt="how do I reset my password?",
               arg_quality=[ArgQuality(tool="search_kb", arg="query",
                                       must_contain_any=["password"],
                                       min_length=4)])
    s = grade_arg_quality(run([("search_kb", {"query": "password reset"}, "ok")]), g)
    assert s.score == 1.0


def test_missing_argument_is_caught():
    g = Golden(task_id="t", prompt="p",
               arg_quality=[ArgQuality(tool="search_kb", arg="query")])
    s = grade_arg_quality(run([("search_kb", {}, "ok")]), g)
    assert s.score == 0.0
    assert any("without the 'query' argument" in v for v in s.violations)


def test_regex_and_length_checks():
    g = Golden(task_id="t", prompt="p", arg_quality=[
        ArgQuality(tool="get_order", arg="order_id", must_match=r"^A[0-9]{4}$")])
    assert grade_arg_quality(run([("get_order", {"order_id": "A1001"}, {})]), g).score == 1.0
    bad = grade_arg_quality(run([("get_order", {"order_id": "order one"}, {})]), g)
    assert bad.score < 1.0


def test_grounded_in_prompt():
    g = Golden(task_id="t", prompt="check order A1001 for me", arg_quality=[
        ArgQuality(tool="get_order", arg="order_id", grounded_in_prompt=True)])
    assert grade_arg_quality(run([("get_order", {"order_id": "A1001"}, {})]), g).score == 1.0
    invented = grade_arg_quality(run([("get_order", {"order_id": "A9999"}, {})]), g)
    assert invented.score < 1.0
    assert any("may have invented it" in v for v in invented.violations)


# --- the important one: hallucinated arguments ------------------------------

def test_argument_taken_from_a_previous_tool_output_is_grounded():
    g = Golden(task_id="t", prompt="p", arg_quality=[
        ArgQuality(tool="issue_refund", arg="amount",
                   from_tool_output="get_order.amount", tolerance=0.01)])
    t = run([("get_order", {"order_id": "A1001"}, {"amount": 120.0}),
             ("issue_refund", {"amount": 120.0}, {"ok": True})])
    assert grade_arg_quality(t, g).score == 1.0


def test_invented_amount_is_caught_even_though_the_tools_were_right():
    """Every tool call here is the one we wanted. Only the value is wrong."""
    g = Golden(task_id="t", prompt="p", arg_quality=[
        ArgQuality(tool="issue_refund", arg="amount",
                   from_tool_output="get_order.amount", tolerance=0.01)])
    t = run([("get_order", {"order_id": "A1001"}, {"amount": 120.0}),
             ("issue_refund", {"amount": 150.0}, {"ok": True})])
    s = grade_arg_quality(t, g)
    assert s.score < 1.0
    assert any("HALLUCINATED ARGUMENT" in v for v in s.violations)


def test_a_single_failing_check_scores_zero():
    """With one check declared and nothing else to pass, the score is 0."""
    g = Golden(task_id="t", prompt="p", arg_quality=[
        ArgQuality(tool="issue_refund", arg="amount", forbid_placeholders=False,
                   from_tool_output="get_order.amount")])
    t = run([("get_order", {"order_id": "A1001"}, {"amount": 120.0}),
             ("issue_refund", {"amount": 150.0}, {"ok": True})])
    assert grade_arg_quality(t, g).score == 0.0


def test_ungrounded_when_the_source_tool_never_ran():
    g = Golden(task_id="t", prompt="p", arg_quality=[
        ArgQuality(tool="issue_refund", arg="amount",
                   from_tool_output="get_order.amount")])
    s = grade_arg_quality(run([("issue_refund", {"amount": 120.0}, {})]), g)
    assert s.score < 1.0
    assert any("not grounded" in v for v in s.violations)


def test_source_must_have_run_before_the_call():
    """Reading the order AFTER you refunded it does not ground the amount."""
    g = Golden(task_id="t", prompt="p", arg_quality=[
        ArgQuality(tool="issue_refund", arg="amount",
                   from_tool_output="get_order.amount")])
    t = run([("issue_refund", {"amount": 120.0}, {}),
             ("get_order", {"order_id": "A1001"}, {"amount": 120.0})])
    s = grade_arg_quality(t, g)
    assert s.score < 1.0
    assert any("not grounded" in v for v in s.violations)


def test_a_list_output_grounds_any_of_its_members():
    g = Golden(task_id="t", prompt="p", arg_quality=[
        ArgQuality(tool="get_order", arg="order_id",
                   from_tool_output="get_customer.order_ids")])
    t = run([("get_customer", {"email": "a@b.c"}, {"order_ids": ["A1001", "A1002"]}),
             ("get_order", {"order_id": "A1002"}, {})])
    assert grade_arg_quality(t, g).score == 1.0
    t2 = run([("get_customer", {"email": "a@b.c"}, {"order_ids": ["A1001"]}),
              ("get_order", {"order_id": "A4242"}, {})])
    s2 = grade_arg_quality(t2, g)
    assert s2.score < 1.0
    assert any("HALLUCINATED ARGUMENT" in v for v in s2.violations)


def test_failed_source_call_does_not_ground_anything():
    g = Golden(task_id="t", prompt="p", arg_quality=[
        ArgQuality(tool="issue_refund", arg="amount",
                   from_tool_output="get_order.amount")])
    t = run([("get_order", {"order_id": "A1001"}, None),
             ("issue_refund", {"amount": 120.0}, {})])
    s = grade_arg_quality(t, g)
    assert s.score < 1.0
    assert any("not grounded" in v for v in s.violations)


# --- end to end ------------------------------------------------------------

def test_the_demo_suite_catches_the_broken_template():
    cfg = Config.load(ROOT / "config.yaml")
    goldens = runner.load_goldens(ROOT / "goldens")
    bad = runner.run_suite(goldens, cfg, agent_version="v1.1-regressed")
    kb = bad.by_id()["kb_password_reset"]
    assert kb.outcome == 1.0                      # the answer is still right
    assert kb.score_map()["arg_quality"] < 1.0    # the call was garbage
    assert kb.passed is False


def test_the_good_agent_grounds_every_argument():
    cfg = Config.load(ROOT / "config.yaml")
    goldens = runner.load_goldens(ROOT / "goldens")
    good = runner.run_suite(goldens, cfg, agent_version="v1.0-good")
    for t in good.tasks:
        assert t.score_map()["arg_quality"] == 1.0, t.task_id
