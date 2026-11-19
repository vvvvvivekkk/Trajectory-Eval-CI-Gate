"""
Run the real agent over the golden suite and write a trace file.

    export ANTHROPIC_API_KEY=...
    python -m real_agent.run --provider anthropic --out out/real-traces.jsonl
    python -m trajgate run --traces out/real-traces.jsonl

Nothing about the graders changes: they read the same Trajectory either way.
"""

from __future__ import annotations

import argparse
import sys

from trajgate.adapters import write_jsonl
from trajgate.runner import load_goldens

from .client import build_client
from .loop import run_task


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="real_agent.run")
    p.add_argument("--provider", default="anthropic",
                   choices=["anthropic", "openai"])
    p.add_argument("--model", default=None)
    p.add_argument("--goldens", default="goldens")
    p.add_argument("--task-id", default=None, help="just one task")
    p.add_argument("--repeats", type=int, default=1,
                   help="runs per task; real agents vary, so 3-5 is wiser")
    p.add_argument("--max-steps", type=int, default=12)
    p.add_argument("--agent-version", default="real-v1",
                   help="label this run, e.g. the git sha or prompt version")
    p.add_argument("--out", default="out/real-traces.jsonl")
    a = p.parse_args(argv)

    goldens = load_goldens(a.goldens)
    if a.task_id:
        goldens = [g for g in goldens if g.task_id == a.task_id]
        if not goldens:
            raise SystemExit(f"no golden with task_id {a.task_id!r}")

    client = build_client(a.provider, a.model)
    trajectories = []
    for g in goldens:
        for i in range(max(1, a.repeats)):
            print(f"running {g.task_id} ({i + 1}/{a.repeats}) on "
                  f"{client.model_name}...", flush=True)
            t = run_task(g.task_id, g.prompt, client,
                         max_steps=a.max_steps,
                         agent_version=a.agent_version,
                         run_id=f"run-{i + 1}")
            print(f"  path: {' -> '.join(t.call_names()) or '(no tools)'}")
            trajectories.append(t)

    write_jsonl(trajectories, a.out)
    print(f"\nwrote {len(trajectories)} trajectories to {a.out}")
    print(f"now grade them:  python -m trajgate run --traces {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
