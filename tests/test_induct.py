"""
Tests for drafting goldens from observed runs.

The property that makes this worth having: a golden induced from runs you
liked must (a) accept those runs, and (b) still reject the regressed agent.
A generator that produces goldens nothing can fail is worse than no
generator, because it looks like coverage.
"""

from pathlib import Path

import yaml

from trajgate import runner
from trajgate.config import Config
from trajgate.graders import grade
from trajgate.induct import find_paths, induct, to_yaml
from trajgate.schema import Golden, Step, StepKind, Trajectory

ROOT = Path(__file__).resolve().parents[1]
CATALOGUE = [t.strip() for t in
             (ROOT / "demo_agent" / "tool_catalogue.txt").read_text().split()
             if t.strip()]


def good_runs(task_id, n=3, version="v1.0-good"):
    from demo_agent import run_task
    return [run_task(task_id, version, run_id=f"run-{i+1}") for i in range(n)]


def draft(task_id, runs=None, prompt=None):
    runs = runs or good_runs(task_id)
    golden, bundle = induct(task_id, runs, prompt=prompt,
                            tool_catalogue=CATALOGUE)
    return golden, bundle, runs


# --- path finding ----------------------------------------------------------

def test_finds_a_scalar_in_a_tool_output():
    assert find_paths({"amount": 120.0, "id": "A1"}, 120.0) == ["amount"]


def test_finds_a_value_inside_a_list():
    assert find_paths({"order_ids": ["A1001", "A1002"]}, "A1002") == ["order_ids"]


def test_finds_a_nested_value():
    paths = find_paths({"order": {"total": {"amount": 9.99}}}, 9.99)
    assert "order.total.amount" in paths


def test_does_not_match_across_types_loosely():
    assert find_paths({"count": 2}, "two") == []


# --- inference -------------------------------------------------------------

def test_infers_the_required_path_and_its_order():
    golden, _, _ = draft("refund_happy")
    assert golden.required_tools == ["get_order", "check_refund_eligibility",
                                     "issue_refund", "send_email"]
    assert golden.order_matters is True
    assert golden.ideal_steps == 4


def test_infers_grounded_arguments():
    """The useful inference: which values were retrieved, not invented."""
    _, (rep, arg_quality), _ = draft("refund_happy")
    pairs = {(a["tool"], a["arg"], a["from_tool_output"]) for a in arg_quality}
    assert ("issue_refund", "amount", "get_order.amount") in pairs
    assert any("invented value" in line for line in rep.inferred)


def test_infers_facts_the_answer_always_contains():
    golden, _, _ = draft("refund_happy")
    assert "A1001" in golden.expect.contains_all
    assert "$120.00" in golden.expect.contains_all


def test_retry_allowance_comes_from_what_was_observed():
    """The flaky task legitimately calls one tool three times."""
    golden, _, _ = draft("flaky_order_lookup")
    assert golden.max_retries_per_tool >= 2
    assert golden.ideal_steps == 3


def test_tools_called_in_some_runs_become_optional_not_required():
    runs = good_runs("kb_password_reset", n=1) + good_runs(
        "kb_password_reset", n=1, version="v1.1-regressed")
    golden, _, _ = draft("kb_password_reset", runs=runs)
    assert golden.required_tools == ["search_kb"]
    assert "get_customer" in golden.optional_tools


def test_two_routes_in_the_sample_make_the_extra_tool_optional():
    """These runs disagree about WHICH tools, not their order: one goes
    straight to the order, the other via the customer."""
    runs = good_runs("order_status_two_routes", n=1) + good_runs(
        "order_status_two_routes", n=1, version="v1.2-fixed")
    golden, (rep, _), _ = draft("order_status_two_routes", runs=runs)
    assert golden.required_tools == ["get_order"]
    assert golden.optional_tools == ["get_customer"]
    # One required tool has no order to grade.
    assert golden.order_matters is False


def test_genuinely_disagreeing_order_turns_order_matters_off_and_says_so():
    """Here both runs call the same three tools in different sequences."""
    runs = good_runs("billing_dispute", n=1) + good_runs(
        "billing_dispute", n=1, version="v1.1-regressed")
    golden, (rep, _), _ = draft("billing_dispute", runs=runs)
    assert set(golden.required_tools) == {"get_order", "get_payments",
                                          "escalate_to_human"}
    assert golden.order_matters is False
    assert any("disagreed on order" in line for line in rep.inferred)
    assert any("order_matters was set to false" in line
               for line in rep.needs_review)


def test_intent_is_never_invented():
    """Observation cannot tell a safety gate from an ordinary step."""
    golden, (rep, _), _ = draft("refund_happy")
    assert golden.forbidden_tools == []
    assert golden.critical_tools == []
    assert golden.must_precede == []
    assert golden.arg_rules == []
    assert any("nothing here encodes intent" in line
               for line in rep.needs_review)


def test_unused_tools_are_suggested_not_forbidden():
    golden, (rep, _), _ = draft("refund_happy")
    suggestion = " ".join(rep.suggestions)
    assert "escalate_to_human" in suggestion      # never called in this task
    assert "escalate_to_human" not in golden.forbidden_tools


def test_empty_input_is_refused():
    try:
        induct("nothing", [])
    except ValueError as e:
        assert "no trajectories" in str(e)
    else:
        raise AssertionError("should have refused")


def test_an_answer_with_no_facts_is_flagged_for_review():
    t = Trajectory(task_id="t", agent_version="v",
                   steps=[Step(index=0, kind=StepKind.FINAL, text="All done!")],
                   final_answer="All done!")
    golden, (rep, _) = induct("t", [t])
    assert golden.expect.contains_all == []
    assert any("cannot fail anything" in line for line in rep.needs_review)


# --- the YAML --------------------------------------------------------------

def test_yaml_is_valid_and_loads_as_a_golden():
    golden, bundle, runs = draft("refund_happy", prompt="refund A1001 please")
    text = to_yaml(golden, bundle, agent_version="v1.0-good",
                   run_count=len(runs))
    data = yaml.safe_load(text)
    reloaded = Golden.model_validate(data)
    assert reloaded.task_id == "refund_happy"
    assert reloaded.required_tools == golden.required_tools
    assert reloaded.arg_quality, "grounding rules must survive the round trip"


def test_yaml_says_it_is_a_draft_and_what_is_missing():
    golden, bundle, runs = draft("refund_happy")
    text = to_yaml(golden, bundle, run_count=len(runs))
    assert "DRAFT golden" in text and "REVIEW IT" in text
    assert "What still needs a human" in text
    assert "forbidden_tools: []" in text
    assert "TODO" in text


# --- the property that matters ---------------------------------------------

def test_an_induced_golden_accepts_the_runs_it_came_from():
    cfg = Config.load(ROOT / "config.yaml")
    for task_id in ["refund_happy", "billing_dispute", "order_lookup_recovery",
                    "flaky_order_lookup", "kb_password_reset"]:
        golden, bundle, runs = draft(task_id)
        reloaded = Golden.model_validate(
            yaml.safe_load(to_yaml(golden, bundle, run_count=len(runs))))
        for t in runs:
            res = grade(t, reloaded, cfg)
            assert res.composite == 1.0, f"{task_id}: {res.notes}"
            assert res.passed is True


def test_an_induced_golden_still_catches_the_regression():
    """Without any hand-written intent, the draft must not be toothless."""
    from demo_agent import run_task
    cfg = Config.load(ROOT / "config.yaml")
    caught = []
    for task_id in ["refund_happy", "kb_password_reset", "billing_dispute",
                    "flaky_order_lookup"]:
        golden, bundle, runs = draft(task_id)
        reloaded = Golden.model_validate(
            yaml.safe_load(to_yaml(golden, bundle, run_count=len(runs))))
        bad = grade(run_task(task_id, "v1.1-regressed"), reloaded, cfg)
        if bad.composite < 1.0:
            caught.append(task_id)
    assert len(caught) >= 3, f"drafts only caught {caught}"


def test_hand_written_goldens_still_score_the_good_agent_at_one():
    """The inductor must not have disturbed the committed suite."""
    cfg = Config.load(ROOT / "config.yaml")
    goldens = runner.load_goldens(ROOT / "goldens")
    s = runner.run_suite(goldens, cfg, agent_version="v1.0-good")
    assert s.mean_composite == 1.0
