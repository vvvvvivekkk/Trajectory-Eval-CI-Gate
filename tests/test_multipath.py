"""
Tests for goldens that accept more than one correct route.

The rule being protected: an agent that takes a different but equally valid
path must not be scored as a regression.
"""

from pathlib import Path

from trajgate import gate, runner
from trajgate.config import Config
from trajgate.graders import grade
from trajgate.schema import Expect, Golden, PathVariant

from tests.test_graders import traj

ROOT = Path(__file__).resolve().parents[1]


def two_route_golden() -> Golden:
    return Golden(
        task_id="t", prompt="p",
        required_tools=["get_order"], order_matters=False, ideal_steps=1,
        alt_paths=[PathVariant(name="customer-first",
                               required_tools=["get_customer", "get_order"],
                               order_matters=True, ideal_steps=2)],
        expect=Expect(contains_all=["A1001"]),
    )


def test_primary_route_scores_full():
    g = two_route_golden()
    res = grade(traj([("get_order", {})], final="A1001 delivered"), g, Config())
    assert res.composite == 1.0
    assert res.matched_path == "primary"


def test_alternative_route_also_scores_full():
    g = two_route_golden()
    res = grade(traj([("get_customer", {}), ("get_order", {})],
                     final="A1001 delivered"), g, Config())
    assert res.composite == 1.0
    assert res.matched_path == "customer-first"


def test_a_third_route_is_still_penalised():
    """Accepting two routes is not the same as accepting anything."""
    g = two_route_golden()
    res = grade(traj([("search_kb", {}), ("get_payments", {}), ("get_order", {})],
                     final="A1001 delivered"), g, Config())
    assert res.composite < 1.0


def test_scores_are_never_mixed_across_routes():
    """A run must not get one route's tool score and another's order score."""
    g = two_route_golden()
    res = grade(traj([("get_customer", {}), ("get_order", {})],
                     final="A1001 delivered"), g, Config())
    sm = res.score_map()
    # customer-first scores 1.0 on all three path graders; the primary route
    # would have scored 0.67 on tool_selection. No blending.
    assert sm["tool_selection"] == 1.0 and sm["order"] == 1.0
    assert sm["efficiency"] == 1.0


def test_safety_rules_apply_to_every_route():
    g = two_route_golden()
    g.forbidden_tools = ["issue_refund"]
    res = grade(traj([("get_customer", {}), ("get_order", {}),
                      ("issue_refund", {})], final="A1001 delivered"), g, Config())
    assert res.hard_fail is True


def test_the_two_demo_agents_take_different_routes_and_both_pass():
    """v1.0-good goes straight to the order, v1.2-fixed goes via the
    customer. The gate must allow both."""
    cfg = Config.load(ROOT / "config.yaml")
    goldens = runner.load_goldens(ROOT / "goldens")
    good = runner.run_suite(goldens, cfg, agent_version="v1.0-good")
    fixed = runner.run_suite(goldens, cfg, agent_version="v1.2-fixed")

    g_task = good.by_id()["order_status_two_routes"]
    f_task = fixed.by_id()["order_status_two_routes"]
    assert g_task.matched_path == "primary"
    assert f_task.matched_path == "customer-first"
    assert g_task.composite == f_task.composite == 1.0

    d = gate.evaluate(fixed, good, cfg)
    assert d.blocked is False
    # The other route costs more. That is reported, not blocked, because
    # comparing the cost of two different routes is not like-for-like.
    assert any("route changed" in w or "different accepted route" in w
               for w in d.warnings)


def test_cost_creep_still_blocks_on_the_same_route():
    """The route-change allowance must not become a loophole."""
    from trajgate.schema import GraderScore, SuiteResult, TaskResult

    def t(tokens, path="primary"):
        return SuiteResult(agent_version="v", created_at="now", tasks=[
            TaskResult(task_id="x", agent_version="v", composite=1.0,
                       path_score=1.0, outcome=1.0, passed=True, tool_calls=3,
                       tokens=tokens, matched_path=path,
                       scores=[GraderScore(name="safety", score=1.0, weight=0.05)])])

    cfg = Config()
    assert gate.evaluate(t(2000), t(1000), cfg).blocked is True
    assert gate.evaluate(t(2000, "alt1"), t(1000), cfg).blocked is False


def test_the_wasteful_route_is_still_caught():
    cfg = Config.load(ROOT / "config.yaml")
    goldens = runner.load_goldens(ROOT / "goldens")
    bad = runner.run_suite(goldens, cfg, agent_version="v1.1-regressed")
    t = bad.by_id()["order_status_two_routes"]
    assert t.composite < 1.0
    assert any("already succeeded" in n for n in t.notes)
