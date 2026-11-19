"""
A small, deterministic stand-in for a real agent.

Why fake and not a real LLM? Because this repo is about the EVAL, and an eval
you can run in CI for free, offline, with the same answer every time is the
only kind anyone keeps running. The three "versions" below stand for three
commits of a real agent:

    v1.0-good       the agent as it works today  (the baseline)
    v1.1-regressed  someone "improved the prompt" and quietly broke the paths
    v1.2-fixed      the regression is fixed and the gate goes green again

To grade YOUR real agent instead, see trajgate/adapters.py - you only have to
produce a Trajectory object; nothing in the grader cares where it came from.
"""

from __future__ import annotations

from trajgate.schema import Step, StepKind, Trajectory

from .tools import Backend, ToolError

# Fake per-token prices, only so the gate has a cost number to watch.
USD_PER_1K_IN = 0.003
USD_PER_1K_OUT = 0.015

TOKENS_IN_PER_CALL = 350
TOKENS_OUT_PER_CALL = 60

# Which tool misbehaves, and how many times, per task. This is the
# ENVIRONMENT, so it is identical for every agent version - otherwise you'd be
# comparing two agents on two different worlds.
FLAKY: dict[str, tuple[str, int]] = {
    "flaky_order_lookup": ("get_order", 2),
}

POLICIES: dict[str, dict] = {
    "v1.0-good": {
        "max_retries": 2,
        "skip_eligibility": False,
        "refund_when_ineligible": False,
        "redundant_search": False,
        "extra_customer_lookup": False,
        "recall_after_success": False,
        "payments_before_order": False,
    },
    "v1.1-regressed": {
        "max_retries": 2,
        "skip_eligibility": True,          # dropped a check, answer still right
        "refund_when_ineligible": True,    # refunds an out-of-window order
        "redundant_search": True,          # searches the same thing twice
        "extra_customer_lookup": True,     # pulls customer data it doesn't need
        "recall_after_success": True,      # forgot it already had the order
        "payments_before_order": True,     # does the steps back to front
    },
    "v1.2-fixed": {
        "max_retries": 2,
        "skip_eligibility": False,
        "refund_when_ineligible": False,
        "redundant_search": False,
        "extra_customer_lookup": False,
        "recall_after_success": False,
        "payments_before_order": False,
    },
}

Action = tuple[str, dict]


def plan(task_id: str, p: dict) -> list[Action]:
    """The agent's plan for a task. A real agent produces this one step at a
    time from the model; here it is scripted so results are reproducible."""

    if task_id == "refund_happy":
        steps: list[Action] = [("get_order", {"order_id": "A1001"})]
        if not p["skip_eligibility"]:
            steps.append(("check_refund_eligibility", {"order_id": "A1001"}))
        steps.append(("issue_refund", {"order_id": "A1001", "amount": 120.0}))
        steps.append(("send_email", {"to": "priya@example.com",
                                     "body": "Your refund is on its way."}))
        return steps

    if task_id == "refund_ineligible":
        steps = [("get_order", {"order_id": "A1002"})]
        if not p["skip_eligibility"]:
            steps.append(("check_refund_eligibility", {"order_id": "A1002"}))
        if p["refund_when_ineligible"]:
            steps.append(("issue_refund", {"order_id": "A1002", "amount": 90.0}))
            steps.append(("send_email", {"to": "sam@example.com",
                                         "body": "Refund issued."}))
        else:
            steps.append(("escalate_to_human",
                          {"reason": "refund requested outside the 30 day window"}))
        return steps

    if task_id == "kb_password_reset":
        steps = [("search_kb", {"query": "password reset"})]
        if p["redundant_search"]:
            steps.append(("search_kb", {"query": "password reset"}))
        if p["extra_customer_lookup"]:
            steps.append(("get_customer", {"email": "priya@example.com"}))
        return steps

    if task_id == "order_lookup_recovery":
        return [("get_customer", {"email": "priya@example.com"}),
                ("get_order", {"order_id": "A1001"})]

    if task_id == "flaky_order_lookup":
        steps = [("get_order", {"order_id": "A1003"})]
        if p["recall_after_success"]:
            steps.append(("get_order", {"order_id": "A1003"}))
        return steps

    if task_id == "billing_dispute":
        if p["payments_before_order"]:
            steps = [("get_payments", {"order_id": "A1003"}),
                     ("get_order", {"order_id": "A1003"})]
        else:
            steps = [("get_order", {"order_id": "A1003"}),
                     ("get_payments", {"order_id": "A1003"})]
        steps.append(("escalate_to_human",
                      {"reason": "customer charged twice for order A1003"}))
        return steps

    raise ValueError(f"demo agent has no plan for task {task_id!r}")


def compose_answer(task_id: str, obs: dict, failed: list[str], p: dict) -> str:
    """Write the user-facing reply from whatever the tools returned."""

    if task_id == "refund_happy":
        if "issue_refund" in obs:
            return ("I've approved a refund of $120.00 for order A1001. "
                    "It should appear on your card in 3-5 business days, "
                    "reference RF-A1001.")
        return "I couldn't process that refund."

    if task_id == "refund_ineligible":
        if "issue_refund" in obs:
            # The regressed agent gives a confidently wrong answer.
            return "I've refunded $90.00 for order A1002. You'll see it shortly."
        return ("Order A1002 was delivered 90 days ago, so it is not eligible "
                "for a refund under the 30 day window. I've escalated it to a "
                "human agent (ticket T-5567) to see what else can be done.")

    if task_id == "kb_password_reset":
        return ("To reset your password, open Settings > Security and choose "
                "'Reset password'. The emailed link is valid for 30 minutes.")

    if task_id == "order_lookup_recovery":
        o = obs.get("get_order") or {}
        if o:
            return (f"Found it - order {o['order_id']} for $120.00 is marked "
                    f"delivered, {o['days_since_delivery']} days ago.")
        return "I couldn't find an order for that email address."

    if task_id == "flaky_order_lookup":
        o = obs.get("get_order") or {}
        if o:
            return (f"Order {o['order_id']} is delivered. The lookup service "
                    f"timed out twice before it answered, so this took a moment.")
        return ("I was unable to reach the order service after several tries. "
                "I'm escalating this rather than guess.")

    if task_id == "billing_dispute":
        pay = obs.get("get_payments") or {}
        n = pay.get("charge_count", "?")
        return (f"You're right - I can see {n} charges of $500.00 on order "
                f"A1003. I've escalated this to a human agent (ticket T-5567) "
                f"to reverse the duplicate charge.")

    return "Done."


def run_task(task_id: str, version: str = "v1.0-good", run_id: str = "run-1") -> Trajectory:
    """Execute one task and return the trajectory (the path it took)."""
    p = POLICIES[version]
    flaky_tool, flaky_times = FLAKY.get(task_id, (None, 0))
    backend = Backend(flaky_tool=flaky_tool, flaky_times=flaky_times)

    traj = Trajectory(task_id=task_id, agent_version=version, run_id=run_id)
    idx = 0
    obs: dict[str, object] = {}
    failed: list[str] = []

    for tool, args in plan(task_id, p):
        attempts = 0
        while True:
            attempts += 1
            traj.steps.append(Step(
                index=idx, kind=StepKind.TOOL_CALL, tool=tool, args=dict(args),
                tokens_in=TOKENS_IN_PER_CALL, tokens_out=TOKENS_OUT_PER_CALL,
            ))
            idx += 1
            try:
                out = backend.call(tool, **args)
                traj.steps.append(Step(
                    index=idx, kind=StepKind.TOOL_RESULT, tool=tool, ok=True,
                    output=out, latency_ms=_latency(tool),
                ))
                idx += 1
                obs[tool] = out
                break
            except ToolError as e:
                traj.steps.append(Step(
                    index=idx, kind=StepKind.TOOL_RESULT, tool=tool, ok=False,
                    error=str(e), latency_ms=_latency(tool),
                ))
                idx += 1
                if attempts > p["max_retries"]:
                    failed.append(tool)
                    break

    answer = compose_answer(task_id, obs, failed, p)
    traj.steps.append(Step(index=idx, kind=StepKind.FINAL, text=answer,
                           tokens_in=300, tokens_out=120))
    traj.final_answer = answer

    tokens_in = sum(s.tokens_in for s in traj.steps)
    tokens_out = sum(s.tokens_out for s in traj.steps)
    traj.usd_cost = (tokens_in / 1000 * USD_PER_1K_IN
                     + tokens_out / 1000 * USD_PER_1K_OUT)
    traj.total_latency_ms = sum(s.latency_ms for s in traj.steps)
    return traj


def _latency(tool: str) -> int:
    from .tools import LATENCY_MS
    return LATENCY_MS.get(tool, 100)
