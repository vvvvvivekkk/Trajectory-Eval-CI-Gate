"""
Tests for cost in dollars.

Two separate mechanisms:
  - a RELATIVE check against the baseline (did this change make it dearer?)
  - an ABSOLUTE ceiling per task (max_usd_cost), which needs no baseline and
    survives a route change - the hole that per-route cost comparison leaves.
"""

from pathlib import Path

import pytest

from trajgate import gate, runner
from trajgate.config import Config
from trajgate.graders import grade
from trajgate.pricing import ModelPrice, PricingConfig, UnknownModel, cost_usd, fmt_usd
from trajgate.schema import Expect, Golden, GraderScore, SuiteResult, TaskResult

from tests.test_graders import traj

ROOT = Path(__file__).resolve().parents[1]


def pricing() -> PricingConfig:
    return PricingConfig(models={"m": ModelPrice(usd_per_m_in=3.0,
                                                 usd_per_m_out=15.0)},
                         default_model="m")


# --- the arithmetic --------------------------------------------------------

def test_input_and_output_are_priced_separately():
    # 1M input at $3 + 1M output at $15
    assert cost_usd(1_000_000, 1_000_000, "m", pricing()) == pytest.approx(18.0)


def test_small_runs_are_priced_in_fractions_of_a_cent():
    assert cost_usd(1000, 200, "m", pricing()) == pytest.approx(0.006)


def test_unknown_model_is_loud_by_default():
    with pytest.raises(UnknownModel):
        cost_usd(1000, 100, "some-model-nobody-priced", pricing())


def test_unknown_model_can_be_made_quiet_on_purpose():
    p = pricing()
    p.strict = False
    assert cost_usd(1000, 100, "unpriced", p) == 0.0


def test_money_formatting_is_readable():
    assert fmt_usd(0) == "$0"
    assert fmt_usd(0.000123) == "$0.00012"
    assert fmt_usd(0.4523) == "$0.4523"
    assert fmt_usd(1234.5) == "$1,234.50"


# --- grading ---------------------------------------------------------------

def budget_golden(**kw) -> Golden:
    return Golden(task_id="t", prompt="p", required_tools=["a"], ideal_steps=1,
                  expect=Expect(contains_all=["done"]), **kw)


def priced_cfg() -> Config:
    cfg = Config()
    cfg.pricing = pricing()
    return cfg


def test_the_grader_prices_the_run():
    t = traj([("a", {})], final="done")
    t.model = "m"
    t.steps[0].tokens_in = 1000
    t.steps[0].tokens_out = 200
    res = grade(t, budget_golden(), priced_cfg())
    assert res.usd_cost == pytest.approx(0.006)
    assert res.usd_cost > 0
    assert res.tokens_in + res.tokens_out == res.tokens


def test_an_explicit_cost_on_the_trajectory_wins():
    """Your own harness may know the real cost; don't overwrite it."""
    t = traj([("a", {})], final="done")
    t.model = "m"
    t.usd_cost = 0.42
    assert grade(t, budget_golden(), priced_cfg()).usd_cost == 0.42


def test_an_unpriced_model_is_reported_not_crashed():
    t = traj([("a", {})], final="done")
    t.model = "mystery-model"
    res = grade(t, budget_golden(), priced_cfg())
    assert res.usd_cost == 0.0
    assert any("cost not priced" in n for n in res.notes)


def test_dollar_budget_is_flagged():
    t = traj([("a", {"x": 1})], final="done")
    t.model = "m"
    t.steps[0].tokens_in = 1_000_000
    res = grade(t, budget_golden(max_usd_cost=0.01), priced_cfg())
    assert res.budget_exceeded is True
    assert any("OVER BUDGET" in n for n in res.notes)


def test_token_budget_is_flagged():
    t = traj([("a", {})], final="done")
    t.model = "m"
    t.steps[0].tokens_in = 5000
    res = grade(t, budget_golden(max_tokens=1000), priced_cfg())
    assert res.budget_exceeded is True


def test_a_run_inside_budget_is_clean():
    t = traj([("a", {})], final="done")
    t.model = "m"
    t.steps[0].tokens_in = 100
    res = grade(t, budget_golden(max_usd_cost=1.0, max_tokens=10_000), priced_cfg())
    assert res.budget_exceeded is False and res.composite == 1.0


# --- the gate --------------------------------------------------------------

def result(usd=0.01, path="primary", over=False, tokens=1000):
    return TaskResult(task_id="x", agent_version="v", composite=1.0,
                      path_score=1.0, outcome=1.0, passed=True, tool_calls=3,
                      tokens=tokens, usd_cost=usd, matched_path=path,
                      budget_exceeded=over,
                      notes=["OVER BUDGET: this run cost $0.02"] if over else [],
                      scores=[GraderScore(name="safety", score=1.0, weight=0.05)])


def suite(*tasks):
    return SuiteResult(agent_version="v", created_at="now", tasks=list(tasks))


def test_dollar_regression_blocks():
    d = gate.evaluate(suite(result(usd=0.02)), suite(result(usd=0.01)), Config())
    assert d.blocked is True
    assert any("cost up" in r for r in d.reasons)


def test_dollar_regression_on_another_route_only_warns():
    d = gate.evaluate(suite(result(usd=0.02, path="alt1")),
                      suite(result(usd=0.01)), Config())
    assert d.blocked is False
    assert any("different accepted route" in w for w in d.warnings)


def test_absolute_budget_blocks_even_on_another_route():
    """The ceiling is what stops the route allowance being a loophole."""
    d = gate.evaluate(suite(result(usd=0.02, path="alt1", over=True)),
                      suite(result(usd=0.01)), Config())
    assert d.blocked is True
    assert any("OVER BUDGET" in r for r in d.reasons)


def test_absolute_budget_blocks_with_no_baseline_at_all():
    d = gate.evaluate(suite(result(over=True)), None, Config())
    assert d.blocked is True


def test_suite_spend_ceiling():
    cfg = Config()
    cfg.thresholds.max_suite_usd = 0.015
    d = gate.evaluate(suite(result(usd=0.01), result(usd=0.01)), None, cfg)
    assert d.blocked is True
    assert any("whole suite cost" in r for r in d.reasons)


def test_suite_cost_is_reported():
    d = gate.evaluate(suite(result(usd=0.01), result(usd=0.02)),
                      suite(result(usd=0.01), result(usd=0.01)), Config())
    assert d.suite_usd == pytest.approx(0.03)
    assert d.baseline_usd == pytest.approx(0.02)


# --- end to end ------------------------------------------------------------

def test_the_regressed_agent_blows_the_kb_budget():
    cfg = Config.load(ROOT / "config.yaml")
    goldens = runner.load_goldens(ROOT / "goldens")
    bad = runner.run_suite(goldens, cfg, agent_version="v1.1-regressed")
    kb = bad.by_id()["kb_password_reset"]
    assert kb.budget_exceeded is True

    good = runner.run_suite(goldens, cfg, agent_version="v1.0-good")
    assert good.by_id()["kb_password_reset"].budget_exceeded is False

    d = gate.evaluate(bad, good, cfg)
    assert any("OVER BUDGET" in r for r in d.reasons)


def test_the_demo_model_is_priced_in_the_committed_config():
    cfg = Config.load(ROOT / "config.yaml")
    assert "demo-model-v1" in cfg.pricing.models
    assert cfg.pricing.strict is True
    goldens = runner.load_goldens(ROOT / "goldens")
    good = runner.run_suite(goldens, cfg, agent_version="v1.0-good")
    assert all(t.usd_cost > 0 for t in good.tasks)
    assert all(t.model == "demo-model-v1" for t in good.tasks)
