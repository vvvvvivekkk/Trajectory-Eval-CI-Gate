"""
The demo as a test: the good agent passes the gate, the regressed one is
blocked, and the fixed one is allowed through again.

This is the test that proves the whole repo does what the README claims.
"""

import json
from pathlib import Path

from trajgate import gate, runner
from trajgate.config import Config
from trajgate.report import html_report, markdown, terminal

ROOT = Path(__file__).resolve().parents[1]
GOLDENS = ROOT / "goldens"


def _suite(version: str):
    cfg = Config.load(ROOT / "config.yaml")
    goldens = runner.load_goldens(GOLDENS)
    return runner.run_suite(goldens, cfg, agent_version=version), cfg


def test_good_agent_is_clean():
    suite, _ = _suite("v1.0-good")
    assert suite.mean_composite == 1.0
    assert suite.pass_rate == 1.0
    assert not any(t.hard_fail or t.lucky_pass for t in suite.tasks)


def test_good_agent_passes_the_gate():
    base, cfg = _suite("v1.0-good")
    cur, _ = _suite("v1.0-good")
    d = gate.evaluate(cur, base, cfg)
    assert d.blocked is False and d.exit_code == 0


def test_regressed_agent_is_blocked():
    base, cfg = _suite("v1.0-good")
    cur, _ = _suite("v1.1-regressed")
    d = gate.evaluate(cur, base, cfg)
    assert d.blocked is True and d.exit_code == 1
    # and it says why, naming tasks
    blamed = {r.split(":")[0] for r in d.reasons}
    assert "refund_ineligible" in blamed
    assert "refund_happy" in blamed


def test_the_regression_is_caught_even_where_the_answer_is_right():
    """The headline claim: an agent that answers correctly but skips a
    money-touching check still fails."""
    cur, cfg = _suite("v1.1-regressed")
    by_id = cur.by_id()
    refund = by_id["refund_happy"]
    assert refund.outcome == 1.0        # the user-visible answer is correct
    assert refund.hard_fail is True     # the path is not
    assert refund.passed is False

    kb = by_id["kb_password_reset"]
    assert kb.outcome == 1.0            # right answer again
    assert kb.lucky_pass is True        # reached by a wasteful, nosy path


def test_fixed_agent_passes_again():
    base, cfg = _suite("v1.0-good")
    cur, _ = _suite("v1.2-fixed")
    d = gate.evaluate(cur, base, cfg)
    assert d.blocked is False and d.exit_code == 0


def test_committed_baseline_covers_every_golden():
    """A golden with no baseline entry is silently ungated, which is the
    quiet way this kind of system rots."""
    baseline = runner.load_suite(ROOT / "baselines" / "baseline.json")
    golden_ids = {g.task_id for g in runner.load_goldens(GOLDENS)}
    assert golden_ids <= set(baseline.by_id())


def test_reports_render():
    suite, cfg = _suite("v1.1-regressed")
    base, _ = _suite("v1.0-good")
    d = gate.evaluate(suite, base, cfg)
    assert "GATE: BLOCKED" in terminal(suite, d)
    md = markdown(suite, d)
    assert "BLOCKED" in md and "refund_ineligible" in md
    html = html_report(suite, d)
    assert html.startswith("<!DOCTYPE html>") and "GATE BLOCKED" in html


def test_trajectories_round_trip_through_jsonl(tmp_path):
    """Grading from a trace file must give the same answer as grading live -
    this is the path a real agent's logs take."""
    goldens = runner.load_goldens(GOLDENS)
    cfg = Config.load(ROOT / "config.yaml")
    p = tmp_path / "traces.jsonl"
    runner.dump_trajectories("v1.1-regressed", goldens, p)
    loaded = runner.load_trajectories(p)
    from_file = runner.run_suite(goldens, cfg, traces=loaded)
    live, _ = _suite("v1.1-regressed")
    assert from_file.mean_composite == live.mean_composite
    assert json.loads(p.read_text().splitlines()[0])["task_id"]
