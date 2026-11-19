"""
The gate: compares this run against the committed baseline and decides
whether to let the pull request through.

Exit code 0 = merge allowed. Exit code 1 = blocked. That exit code is the
entire mechanism by which CI blocks a merge; everything else is reporting.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from .config import Config
from .pricing import fmt_usd
from .schema import SuiteResult, TaskResult


class TaskDelta(BaseModel):
    task_id: str
    baseline: float | None
    current: float
    delta: float                      # current - baseline
    was_passing: bool | None
    now_passing: bool
    new_lucky_pass: bool = False
    new_flaky: bool = False
    hard_fail: bool = False
    steps_delta_pct: float | None = None
    cost_delta_pct: float | None = None
    usd_delta_pct: float | None = None
    usd_cost: float = 0.0
    route_changed: bool = False
    budget_exceeded: bool = False
    blocking_reasons: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class GateDecision(BaseModel):
    blocked: bool
    suite_usd: float = 0.0
    baseline_usd: float | None = None
    reasons: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    deltas: list[TaskDelta] = Field(default_factory=list)
    mean_current: float = 0.0
    mean_baseline: float | None = None
    mean_delta: float = 0.0
    has_baseline: bool = True

    @property
    def exit_code(self) -> int:
        return 1 if self.blocked else 0


def _pct_change(new: float, old: float) -> float | None:
    if old == 0:
        return None if new == 0 else 100.0
    return (new - old) / old * 100.0


def evaluate(current: SuiteResult, baseline: SuiteResult | None,
             cfg: Config) -> GateDecision:
    th = cfg.thresholds
    base_map: dict[str, TaskResult] = baseline.by_id() if baseline else {}
    decision = GateDecision(
        blocked=False,
        mean_current=round(current.mean_composite, 4),
        mean_baseline=round(baseline.mean_composite, 4) if baseline else None,
        has_baseline=baseline is not None,
    )

    for cur in current.tasks:
        b = base_map.get(cur.task_id)
        d = TaskDelta(
            task_id=cur.task_id,
            baseline=b.composite if b else None,
            current=cur.composite,
            delta=round(cur.composite - b.composite, 4) if b else 0.0,
            was_passing=b.passed if b else None,
            now_passing=cur.passed,
            new_lucky_pass=cur.lucky_pass and not (b.lucky_pass if b else False),
            new_flaky=cur.flaky and not (b.flaky if b else False),
            hard_fail=cur.hard_fail,
        )
        d.usd_cost = cur.usd_cost
        d.budget_exceeded = cur.budget_exceeded
        if b:
            d.steps_delta_pct = _pct_change(cur.tool_calls, b.tool_calls)
            d.cost_delta_pct = _pct_change(cur.tokens, b.tokens)
            d.usd_delta_pct = _pct_change(cur.usd_cost, b.usd_cost)

        # --- the blocking rules ---------------------------------------------
        if th.block_on_hard_fail and cur.hard_fail:
            # Quote the actual rule that was broken, so nobody has to open the
            # report to find out why their pull request is red.
            safety = next((s for s in cur.scores if s.name == "safety"), None)
            detail = "; ".join(safety.violations) if safety and safety.violations \
                else "a safety rule was broken"
            d.blocking_reasons.append(f"safety hard fail - {detail}")
        if b and th.block_on_pass_flip and b.passed and not cur.passed:
            d.blocking_reasons.append(
                f"went from passing to failing ({b.composite:.2f} -> {cur.composite:.2f})"
            )
        if b and d.delta < -th.max_task_score_drop:
            d.blocking_reasons.append(
                f"score dropped {abs(d.delta):.3f}, more than the allowed "
                f"{th.max_task_score_drop}"
            )
        if th.block_on_new_flaky and d.new_flaky:
            d.blocking_reasons.append(
                f"became UNSTABLE: score spread {cur.spread:.2f} across "
                f"{cur.runs} runs (limit {th.max_run_spread}), "
                f"{cur.run_pass_rate*100:.0f}% of runs passed")
        if th.block_on_new_lucky_pass and d.new_lucky_pass:
            d.blocking_reasons.append(
                "new LUCKY PASS: right answer reached by a bad path "
                f"(path score {cur.path_score:.2f})"
            )
        # Cost and step creep are only a like-for-like comparison when the
        # run took the same route as the baseline. If the golden accepts
        # several routes and the agent switched to another allowed one, a
        # different cost is expected, not a regression - so it is reported
        # rather than blocked. Absolute budgets (max_tokens, max_usd_cost on
        # the golden) are what guard against an expensive route.
        same_route = bool(b) and cur.matched_path == b.matched_path
        d.route_changed = bool(b) and not same_route
        cost_over = (d.cost_delta_pct is not None
                     and d.cost_delta_pct > th.max_cost_increase_pct)
        steps_over = (d.steps_delta_pct is not None
                      and d.steps_delta_pct > th.max_steps_increase_pct)

        if b and th.block_on_cost_regression and cost_over:
            msg = (f"token use up {d.cost_delta_pct:.0f}% (limit "
                   f"{th.max_cost_increase_pct:.0f}%)")
            if same_route:
                d.blocking_reasons.append(msg)
            else:
                d.warnings.append(
                    f"{msg}, but the route changed from "
                    f"'{b.matched_path}' to '{cur.matched_path}' - not a "
                    f"like-for-like comparison, so not blocking")
        # Money. Tokens and dollars can disagree - a cheaper model called
        # twice as often is a token saving and a bigger bill - so both are
        # checked and the dollar figure is the one quoted to humans.
        usd_over = (d.usd_delta_pct is not None
                    and d.usd_delta_pct > th.max_usd_increase_pct)
        if b and th.block_on_cost_regression and usd_over:
            msg = (f"cost up {d.usd_delta_pct:.0f}% "
                   f"({fmt_usd(b.usd_cost)} -> {fmt_usd(cur.usd_cost)}, limit "
                   f"{th.max_usd_increase_pct:.0f}%)")
            if same_route:
                d.blocking_reasons.append(msg)
            else:
                d.warnings.append(f"{msg}, on a different accepted route")

        # An absolute ceiling needs no baseline and survives a route change.
        if th.block_on_budget_exceeded and cur.budget_exceeded:
            over = [n for n in cur.notes if n.startswith("OVER BUDGET")]
            d.blocking_reasons.append(
                over[0] if over else "exceeded the task's absolute budget")

        if b and th.block_on_cost_regression and steps_over:
            msg = (f"tool calls up {d.steps_delta_pct:.0f}% (limit "
                   f"{th.max_steps_increase_pct:.0f}%)")
            if same_route:
                d.blocking_reasons.append(msg)
            else:
                d.warnings.append(
                    f"{msg}, but on a different accepted route "
                    f"('{b.matched_path}' -> '{cur.matched_path}')")

        # --- things worth saying but not blocking ---------------------------
        if not b:
            d.warnings.append("new task with no baseline - not blocking, "
                              "run `baseline` to record it")
        if b and 0 < -d.delta <= th.max_task_score_drop:
            d.warnings.append(f"small dip of {abs(d.delta):.3f}, within tolerance")
        if cur.lucky_pass and not d.new_lucky_pass:
            d.warnings.append("still a lucky pass (was already in the baseline)")
        if cur.flaky and not d.new_flaky:
            d.warnings.append(
                f"still unstable (spread {cur.spread:.2f}) - was already "
                f"unstable in the baseline, so not blocking this change")

        decision.deltas.append(d)
        decision.reasons += [f"{d.task_id}: {r}" for r in d.blocking_reasons]
        decision.warnings += [f"{d.task_id}: {w}" for w in d.warnings]

    # --- suite-wide rules ---------------------------------------------------
    if baseline:
        decision.mean_delta = round(
            current.mean_composite - baseline.mean_composite, 4)
        if decision.mean_delta < -th.max_mean_score_drop:
            decision.reasons.append(
                f"suite average dropped {abs(decision.mean_delta):.3f}, more than "
                f"the allowed {th.max_mean_score_drop}"
            )
    else:
        decision.warnings.append(
            "no baseline found - nothing to compare against, so nothing is "
            "blocked. Commit a baseline so the next pull request is gated."
        )

    decision.suite_usd = round(sum(t.usd_cost for t in current.tasks), 8)
    if baseline:
        decision.baseline_usd = round(sum(t.usd_cost for t in baseline.tasks), 8)
    if th.max_suite_usd is not None and decision.suite_usd > th.max_suite_usd:
        decision.reasons.append(
            f"the whole suite cost {fmt_usd(decision.suite_usd)}, over the "
            f"configured ceiling of {fmt_usd(th.max_suite_usd)}")

    if th.min_mean_score > 0 and current.mean_composite < th.min_mean_score:
        decision.reasons.append(
            f"suite average {current.mean_composite:.3f} is below the absolute "
            f"floor of {th.min_mean_score}"
        )

    decision.blocked = bool(decision.reasons)
    return decision
