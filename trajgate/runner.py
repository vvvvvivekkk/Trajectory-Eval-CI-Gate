"""
Runs the suite: for each golden, get a trajectory, grade it, collect results.

Trajectories can come from two places:
  1. the built-in demo agent (default, offline, deterministic)
  2. a .jsonl file your real agent wrote (one Trajectory per line)
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import yaml

from .config import Config
from .graders import grade
from .schema import Golden, SuiteResult, TaskResult, Trajectory


def load_goldens(folder: str | Path) -> list[Golden]:
    folder = Path(folder)
    files = sorted(list(folder.glob("*.yaml")) + list(folder.glob("*.yml")))
    if not files:
        raise SystemExit(f"no golden files found in {folder}")
    out: list[Golden] = []
    for f in files:
        data = yaml.safe_load(f.read_text())
        if not data:
            continue
        out.append(Golden.model_validate(data))
    return out


def load_trajectories(path: str | Path) -> dict[str, list[Trajectory]]:
    """Read a .jsonl file of trajectories, grouped by task_id."""
    grouped: dict[str, list[Trajectory]] = {}
    for line in Path(path).read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        t = Trajectory.model_validate_json(line)
        grouped.setdefault(t.task_id, []).append(t)
    return grouped


def _median_result(results: list[TaskResult]) -> TaskResult:
    """With several runs of the same task, report the middle one.

    Not the best run - reporting your best run is how teams lie to themselves
    about non-deterministic agents.
    """
    ordered = sorted(results, key=lambda r: r.composite)
    return ordered[len(ordered) // 2]


def build_judge(cfg: Config):
    """The judge client, or None when it is switched off (the default)."""
    if not cfg.judge.enabled:
        return None
    from .judge import build_client
    return build_client(cfg.judge.provider, cfg.judge.model,
                        cfg.judge.cache_path)


def _attach_stability(res: TaskResult, runs: list[TaskResult],
                      cfg: Config) -> None:
    """Record how much the score moved across repeats of the same task.

    A task whose score swings 0.4 between identical runs is not a 0.8 task;
    it is an unstable task, and that is a finding about the agent rather than
    a problem with the eval. Flagging it stops the gate becoming a coin flip.
    """
    res.runs = len(runs)
    res.run_scores = [round(r.composite, 4) for r in runs]
    res.routes_seen = sorted({r.matched_path for r in runs})
    if len(runs) < 2:
        return

    res.spread = round(max(res.run_scores) - min(res.run_scores), 4)
    res.stability = round(max(0.0, 1.0 - res.spread), 4)
    passes = sum(1 for r in runs if r.passed)
    res.run_pass_rate = round(passes / len(runs), 4)

    disagreed = 0 < passes < len(runs)
    res.flaky = res.spread > cfg.thresholds.max_run_spread or disagreed

    res.notes.append(
        f"{len(runs)} runs: median reported, spread {res.spread:.2f}, "
        f"{passes}/{len(runs)} passed")
    if disagreed:
        res.notes.append(
            f"UNSTABLE: the same task passed {passes} of {len(runs)} runs - "
            f"the single-run verdict here is luck, not a measurement")
    elif res.flaky:
        res.notes.append(
            f"UNSTABLE: score moved {res.spread:.2f} across identical runs "
            f"(limit {cfg.thresholds.max_run_spread})")
    if len(res.routes_seen) > 1:
        res.notes.append(
            f"took different accepted routes across runs: "
            f"{', '.join(res.routes_seen)}")


def run_suite(
    goldens: list[Golden],
    cfg: Config,
    agent_version: str = "v1.0-good",
    traces: dict[str, list[Trajectory]] | None = None,
    judge_client=None,
) -> SuiteResult:
    from demo_agent import run_task  # imported lazily so traces mode needs no agent

    if judge_client is None:
        judge_client = build_judge(cfg)

    tasks: list[TaskResult] = []
    for g in goldens:
        runs: list[TaskResult] = []
        if traces is not None:
            found = traces.get(g.task_id, [])
            if not found:
                raise SystemExit(
                    f"no trajectory in the trace file for task {g.task_id!r}"
                )
            for t in found:
                runs.append(grade(t, g, cfg, judge_client))
        else:
            for i in range(max(1, cfg.repeats)):
                traj = run_task(g.task_id, agent_version, run_id=f"run-{i+1}")
                runs.append(grade(traj, g, cfg, judge_client))
        res = _median_result(runs)
        _attach_stability(res, runs, cfg)
        tasks.append(res)

    version = agent_version
    if traces is not None:
        versions = {t.agent_version for lst in traces.values() for t in lst}
        version = ", ".join(sorted(versions)) or "from-traces"

    return SuiteResult(
        agent_version=version,
        created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        tasks=tasks,
    )


def save_suite(suite: SuiteResult, path: str | Path) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(suite.model_dump(), indent=2))
    return p


def load_suite(path: str | Path) -> SuiteResult:
    return SuiteResult.model_validate(json.loads(Path(path).read_text()))


def dump_trajectories(agent_version: str, goldens: list[Golden],
                      path: str | Path) -> Path:
    """Write the raw paths to a .jsonl file - useful for eyeballing a failure."""
    from demo_agent import run_task
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w") as fh:
        for g in goldens:
            t = run_task(g.task_id, agent_version)
            fh.write(json.dumps(t.model_dump(), default=str) + "\n")
    return p
