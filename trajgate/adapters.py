"""
Turning YOUR agent's logs into a Trajectory.

The grader does not care what framework you use. It only needs a Trajectory
object. These adapters cover the two shapes almost every agent already emits.

If you use a tracing tool (Langfuse, LangSmith, Phoenix, OpenTelemetry), the
same idea applies: walk the spans in order, emit a TOOL_CALL step for each
tool span and a TOOL_RESULT step for its outcome.
"""

from __future__ import annotations

import json
from typing import Any, Iterable

from .schema import Step, StepKind, Trajectory


def from_openai_messages(
    task_id: str,
    messages: Iterable[dict[str, Any]],
    agent_version: str = "unknown",
    run_id: str = "run-1",
    usd_cost: float = 0.0,
) -> Trajectory:
    """Convert an OpenAI / Anthropic style message list into a Trajectory.

    Expects the usual shape: assistant messages carrying `tool_calls`, and
    `role: "tool"` messages carrying the results.
    """
    traj = Trajectory(task_id=task_id, agent_version=agent_version,
                      run_id=run_id, usd_cost=usd_cost)
    idx = 0
    last_text = ""

    for m in messages:
        role = m.get("role")
        if role == "assistant":
            for tc in (m.get("tool_calls") or []):
                fn = tc.get("function", tc)
                raw = fn.get("arguments") or "{}"
                args = raw if isinstance(raw, dict) else _safe_json(raw)
                traj.steps.append(Step(index=idx, kind=StepKind.TOOL_CALL,
                                       tool=fn.get("name"), args=args))
                idx += 1
            if m.get("content"):
                last_text = _as_text(m["content"])
                traj.steps.append(Step(index=idx, kind=StepKind.MESSAGE,
                                       text=last_text))
                idx += 1
        elif role == "tool":
            content = _as_text(m.get("content"))
            ok = not _looks_like_error(content)
            traj.steps.append(Step(
                index=idx, kind=StepKind.TOOL_RESULT, tool=m.get("name"),
                ok=ok, error=None if ok else content,
                output=content[:2000],
            ))
            idx += 1

    traj.final_answer = last_text
    traj.steps.append(Step(index=idx, kind=StepKind.FINAL, text=last_text))
    return traj


def from_events(
    task_id: str,
    events: Iterable[dict[str, Any]],
    agent_version: str = "unknown",
    run_id: str = "run-1",
) -> Trajectory:
    """Convert a generic event stream (LangGraph, LlamaIndex, your own loop).

    Each event needs a `type` of tool_call / tool_result / message / final,
    plus the obvious fields. Anything missing is simply left blank.
    """
    traj = Trajectory(task_id=task_id, agent_version=agent_version, run_id=run_id)
    for idx, e in enumerate(events):
        kind = {
            "tool_call": StepKind.TOOL_CALL,
            "tool_result": StepKind.TOOL_RESULT,
            "message": StepKind.MESSAGE,
            "thought": StepKind.THOUGHT,
            "final": StepKind.FINAL,
        }.get(str(e.get("type")), StepKind.MESSAGE)
        traj.steps.append(Step(
            index=idx, kind=kind, tool=e.get("tool"),
            args=e.get("args") or {}, ok=e.get("ok"), error=e.get("error"),
            output=e.get("output"), text=e.get("text"),
            latency_ms=int(e.get("latency_ms") or 0),
            tokens_in=int(e.get("tokens_in") or 0),
            tokens_out=int(e.get("tokens_out") or 0),
        ))
        if kind == StepKind.FINAL and e.get("text"):
            traj.final_answer = e["text"]
    if not traj.final_answer:
        msgs = [s.text for s in traj.steps
                if s.kind == StepKind.MESSAGE and s.text]
        traj.final_answer = msgs[-1] if msgs else ""
    return traj


def write_jsonl(trajectories: Iterable[Trajectory], path: str) -> None:
    """One trajectory per line - the format `trajgate run --traces` reads."""
    with open(path, "w") as fh:
        for t in trajectories:
            fh.write(json.dumps(t.model_dump(), default=str) + "\n")


# --- helpers ---------------------------------------------------------------

def _safe_json(raw: str) -> dict:
    try:
        val = json.loads(raw)
        return val if isinstance(val, dict) else {"_value": val}
    except Exception:
        return {"_raw": raw}


def _as_text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):  # Anthropic-style content blocks
        return "".join(
            b.get("text", "") if isinstance(b, dict) else str(b) for b in content)
    return str(content)


def _looks_like_error(text: str) -> bool:
    low = (text or "").lower()
    return any(k in low for k in ("error", "exception", "traceback", "failed",
                                  "not found", "timeout", "503", "500"))
