"""
The real agent loop: model decides, tool runs, repeat, and every step is
recorded as a Trajectory the grader can read.

This is an ordinary tool-calling loop - there is nothing clever in it. That
is the point: the eval in this repo grades whatever this loop produces, so
your own loop needs no changes beyond emitting the same Trajectory.
"""

from __future__ import annotations

import json
import time
from typing import Any

from demo_agent.tools import Backend, ToolError
from trajgate.schema import Step, StepKind, Trajectory

from .client import ModelClient, ModelReply, ToolRequest

MAX_STEPS = 12          # a loop guard; agents that lose the thread spin
KNOWN_TOOLS = {"search_kb", "get_order", "get_customer",
               "check_refund_eligibility", "issue_refund", "get_payments",
               "send_email", "escalate_to_human"}


def _short(value: Any, limit: int = 600) -> str:
    text = value if isinstance(value, str) else json.dumps(value, default=str)
    return text if len(text) <= limit else text[:limit] + "...(truncated)"


def run_task(task_id: str, prompt: str, client: ModelClient,
             backend: Backend | None = None, max_steps: int = MAX_STEPS,
             agent_version: str = "real-v1", run_id: str = "run-1",
             ) -> Trajectory:
    """Run one task with a real model and return the path it took."""
    backend = backend or Backend()
    traj = Trajectory(task_id=task_id, agent_version=agent_version,
                      run_id=run_id, model=getattr(client, "model_name", None))
    messages: list[dict] = [{"role": "user", "content": prompt}]
    idx = 0
    answer = ""

    for _ in range(max_steps):
        started = time.perf_counter()
        reply: ModelReply = client.respond(messages)
        think_ms = int((time.perf_counter() - started) * 1000)

        if reply.text:
            traj.steps.append(Step(
                index=idx, kind=StepKind.THOUGHT, text=reply.text,
                latency_ms=think_ms, tokens_in=reply.tokens_in,
                tokens_out=reply.tokens_out))
            idx += 1
            answer = reply.text
        elif reply.tokens_in or reply.tokens_out:
            # Still bill the turn even when the model only asked for tools.
            traj.steps.append(Step(
                index=idx, kind=StepKind.THOUGHT, text="(tool call only)",
                latency_ms=think_ms, tokens_in=reply.tokens_in,
                tokens_out=reply.tokens_out))
            idx += 1

        if not reply.wants_tools:
            break

        messages.append(client.assistant_message(reply))
        results: list[tuple[ToolRequest, str, bool]] = []

        for req in reply.tool_requests:
            traj.steps.append(Step(index=idx, kind=StepKind.TOOL_CALL,
                                   tool=req.name, args=dict(req.args)))
            idx += 1
            t0 = time.perf_counter()
            if req.name not in KNOWN_TOOLS:
                out, ok = f"no such tool: {req.name}", False
            else:
                try:
                    raw = backend.call(req.name, **req.args)
                    out, ok = _short(raw), True
                except ToolError as e:
                    out, ok = f"error: {e}", False
                except TypeError as e:
                    # The model called a real tool with the wrong arguments.
                    out, ok = f"error: bad arguments for {req.name}: {e}", False
            tool_ms = int((time.perf_counter() - t0) * 1000)

            traj.steps.append(Step(
                index=idx, kind=StepKind.TOOL_RESULT, tool=req.name, ok=ok,
                output=out if ok else None, error=None if ok else out,
                latency_ms=tool_ms))
            idx += 1
            results.append((req, out, ok))

        msg = client.tool_result_message(results)
        if "_multi" in msg:                      # OpenAI wants one each
            messages.extend(msg["_multi"])
        else:
            messages.append(msg)
    else:
        # Fell out of the loop without the model stopping.
        answer = answer or ("I could not finish this within the step limit, "
                            "so I am escalating rather than guess.")
        traj.steps.append(Step(
            index=idx, kind=StepKind.MESSAGE,
            text=f"(stopped after the {max_steps}-step limit)"))
        idx += 1

    traj.final_answer = answer
    traj.steps.append(Step(index=idx, kind=StepKind.FINAL, text=answer))
    traj.total_latency_ms = sum(s.latency_ms for s in traj.steps)
    return traj
