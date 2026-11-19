"""
Command line entry point.

  python -m trajgate run                     # grade + gate (what CI runs)
  python -m trajgate run --agent v1.1-regressed
  python -m trajgate run --traces out/traces.jsonl    # grade a real agent
  python -m trajgate baseline --report out/report.json
  python -m trajgate gate --report out/report.json
  python -m trajgate trace --agent v1.0-good --out out/traces.jsonl
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from . import gate as gate_mod
from . import report as report_mod
from . import runner
from .config import Config

DEF_GOLDENS = "goldens"
DEF_BASELINE = "baselines/baseline.json"
DEF_REPORT = "out/report.json"
DEF_CONFIG = "config.yaml"


def _step_summary(md: str) -> None:
    """GitHub Actions shows whatever is written here on the run's page."""
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a") as fh:
            fh.write(md + "\n")


def cmd_run(a: argparse.Namespace) -> int:
    cfg = Config.load(a.config)
    if a.repeats:
        cfg.repeats = a.repeats
    if a.judge:
        cfg.judge.enabled = True
    if a.judge_provider:
        cfg.judge.provider = a.judge_provider
    if a.judge_model:
        cfg.judge.model = a.judge_model
    goldens = runner.load_goldens(a.goldens)

    traces = runner.load_trajectories(a.traces) if a.traces else None
    suite = runner.run_suite(goldens, cfg, agent_version=a.agent, traces=traces)
    runner.save_suite(suite, a.out)

    baseline = None
    bpath = Path(a.baseline)
    if bpath.exists():
        baseline = runner.load_suite(bpath)

    decision = gate_mod.evaluate(suite, baseline, cfg)

    print(report_mod.terminal(suite, decision))
    md = report_mod.markdown(suite, decision)
    out_dir = Path(a.out).parent
    report_mod.write(out_dir / "report.md", md)
    report_mod.write(out_dir / "report.html",
                     report_mod.html_report(suite, decision))
    _step_summary(md)
    print(f"wrote {a.out}, {out_dir/'report.md'}, {out_dir/'report.html'}")

    if a.no_gate:
        return 0
    return decision.exit_code


def cmd_gate(a: argparse.Namespace) -> int:
    cfg = Config.load(a.config)
    suite = runner.load_suite(a.report)
    baseline = runner.load_suite(a.baseline) if Path(a.baseline).exists() else None
    decision = gate_mod.evaluate(suite, baseline, cfg)
    print(report_mod.terminal(suite, decision))
    _step_summary(report_mod.markdown(suite, decision))
    return decision.exit_code


def cmd_baseline(a: argparse.Namespace) -> int:
    suite = runner.load_suite(a.report)
    runner.save_suite(suite, a.out)
    print(f"baseline updated from {a.report} -> {a.out}")
    print(f"  agent {suite.agent_version}, mean score {suite.mean_composite:.3f}, "
          f"{len(suite.tasks)} tasks")
    print("  commit this file: it is what future pull requests are judged against.")
    return 0


def cmd_trace(a: argparse.Namespace) -> int:
    goldens = runner.load_goldens(a.goldens)
    p = runner.dump_trajectories(a.agent, goldens, a.out)
    print(f"wrote raw trajectories to {p}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="trajgate",
        description="Grade an agent's path, not just its answer, and block "
                    "pull requests that make the path worse.")
    sub = p.add_subparsers(dest="cmd", required=True)

    def common(sp):
        sp.add_argument("--goldens", default=DEF_GOLDENS)
        sp.add_argument("--config", default=DEF_CONFIG)

    r = sub.add_parser("run", help="run the suite, grade it, apply the gate")
    common(r)
    r.add_argument("--agent", default="v1.0-good",
                   help="demo agent version to run")
    r.add_argument("--traces", default=None,
                   help="grade trajectories from a .jsonl file instead")
    r.add_argument("--out", default=DEF_REPORT)
    r.add_argument("--baseline", default=DEF_BASELINE)
    r.add_argument("--repeats", type=int, default=None,
                   help="runs per task; the median is reported")
    r.add_argument("--no-gate", action="store_true",
                   help="report but always exit 0")
    r.add_argument("--judge", action="store_true",
                   help="enable the optional LLM judge (needs an API key)")
    r.add_argument("--judge-provider", default=None,
                   choices=["anthropic", "openai"])
    r.add_argument("--judge-model", default=None)
    r.set_defaults(fn=cmd_run)

    g = sub.add_parser("gate", help="apply the gate to an existing report")
    common(g)
    g.add_argument("--report", default=DEF_REPORT)
    g.add_argument("--baseline", default=DEF_BASELINE)
    g.set_defaults(fn=cmd_gate)

    b = sub.add_parser("baseline", help="record a report as the new baseline")
    b.add_argument("--report", default=DEF_REPORT)
    b.add_argument("--out", default=DEF_BASELINE)
    b.set_defaults(fn=cmd_baseline)

    t = sub.add_parser("trace", help="dump raw trajectories for inspection")
    common(t)
    t.add_argument("--agent", default="v1.0-good")
    t.add_argument("--out", default="out/traces.jsonl")
    t.set_defaults(fn=cmd_trace)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
