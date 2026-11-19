"""
Tests for the optional LLM judge.

No network and no API key: a ScriptedJudge stands in for the model, which is
also how you would test your own rubrics before paying for a real run.

The property that matters most here: when the judge breaks, it must be
EXCLUDED from the score, never scored as a pass.
"""

import json
from pathlib import Path

from trajgate import runner
from trajgate.config import Config
from trajgate.graders import grade
from trajgate.judge import (
    CachedJudge,
    ScriptedJudge,
    build_client,
    build_prompt,
    grade_judge,
    parse_verdict,
)
from trajgate.schema import Expect, Golden, JudgeSpec

from tests.test_graders import traj

ROOT = Path(__file__).resolve().parents[1]


def judged_golden(**kw) -> Golden:
    return Golden(
        task_id="t", prompt="I was charged twice for A1003.",
        required_tools=["get_order"], ideal_steps=1,
        expect=Expect(contains_all=["A1003"]),
        judge=JudgeSpec(rubric="The reply must acknowledge the duplicate charge.",
                        criteria=["Does not promise a refund."], **kw),
    )


def reply(score, reason="fine"):
    return json.dumps({"score": score, "reason": reason})


# --- parsing ---------------------------------------------------------------

def test_parses_clean_json():
    assert parse_verdict(reply(0.9, "good")) == (0.9, "good")


def test_parses_json_wrapped_in_chatter():
    score, reason = parse_verdict('Sure!\n{"score": 0.5, "reason": "meh"}\nHope that helps')
    assert score == 0.5 and reason == "meh"


def test_rescales_a_ten_point_score():
    score, _ = parse_verdict('{"score": 7, "reason": "seven out of ten"}')
    assert score == 0.7


def test_unusable_replies_return_none():
    for bad in ["", "no json here", '{"reason": "forgot the score"}',
                '{"score": "excellent"}']:
        score, why = parse_verdict(bad)
        assert score is None and why


# --- the grader ------------------------------------------------------------

def test_no_rubric_means_no_judge():
    g = Golden(task_id="t", prompt="p")
    assert grade_judge(traj([]), g, ScriptedJudge(reply(1.0))) is None


def test_no_client_means_no_judge():
    assert grade_judge(traj([]), judged_golden(), None) is None


def test_judge_score_is_used():
    gs = grade_judge(traj([]), judged_golden(), ScriptedJudge(reply(0.85, "ok")))
    assert gs is not None and gs.score == 0.85 and gs.excluded is False


def test_judge_below_min_score_is_a_violation():
    gs = grade_judge(traj([]), judged_golden(min_score=0.8),
                     ScriptedJudge(reply(0.4, "promised a refund")))
    assert gs.violations and "judge scored 0.40" in gs.violations[0]


def test_broken_judge_is_excluded_not_passed():
    """The whole point: a judge that cannot run must not grade 1.0."""
    class Exploding:
        def __call__(self, prompt):
            raise RuntimeError("401 unauthorized")

    gs = grade_judge(traj([]), judged_golden(), Exploding())
    assert gs.excluded is True
    assert gs.score == 0.0
    assert "401 unauthorized" in gs.detail


def test_nonsense_judge_is_excluded():
    gs = grade_judge(traj([]), judged_golden(), ScriptedJudge("I think it's nice"))
    assert gs.excluded is True


def test_prompt_contains_rubric_path_and_answer():
    g = judged_golden()
    p = build_prompt(g, traj([("get_order", {})], final="Found 2 charges."))
    assert "acknowledge the duplicate charge" in p
    assert "Does not promise a refund." in p
    assert "get_order" in p
    assert "Found 2 charges." in p
    assert "JSON only" in p


# --- weighting -------------------------------------------------------------

def test_excluded_judge_does_not_change_the_composite():
    class Exploding:
        def __call__(self, prompt):
            raise RuntimeError("no network")

    cfg = Config()
    cfg.weights.judge = 0.3
    g = judged_golden()
    t = traj([("get_order", {})], final="A1003 has two charges")
    without = grade(t, g, cfg).composite
    with_broken = grade(t, g, cfg, Exploding()).composite
    assert with_broken == without
    res = grade(t, g, cfg, Exploding())
    assert any("JUDGE EXCLUDED" in n for n in res.notes)


def test_a_weighted_judge_moves_the_score():
    cfg = Config()
    cfg.weights.judge = 0.5
    g = judged_golden()
    t = traj([("get_order", {})], final="A1003 has two charges")
    good = grade(t, g, cfg, ScriptedJudge(reply(1.0))).composite
    bad = grade(t, g, cfg, ScriptedJudge(reply(0.0))).composite
    assert good > bad


def test_judge_is_weightless_by_default():
    g = judged_golden()
    t = traj([("get_order", {})], final="A1003 has two charges")
    assert (grade(t, g, Config(), ScriptedJudge(reply(0.0))).composite
            == grade(t, g, Config(), ScriptedJudge(reply(1.0))).composite)


# --- caching ---------------------------------------------------------------

def test_cache_stops_the_second_call(tmp_path):
    inner = ScriptedJudge(reply(0.9))
    cached = CachedJudge(inner, tmp_path / "cache.json")
    cached("same prompt")
    cached("same prompt")
    assert len(inner.calls) == 1
    assert cached.hits == 1 and cached.misses == 1
    assert (tmp_path / "cache.json").exists()


def test_cache_persists_across_runs(tmp_path):
    path = tmp_path / "cache.json"
    CachedJudge(ScriptedJudge(reply(0.9)), path)("p")
    inner = ScriptedJudge(reply(0.1))
    second = CachedJudge(inner, path)
    assert "0.9" in second("p")
    assert inner.calls == []


def test_build_client_rejects_unknown_providers():
    try:
        build_client("llama-at-home")
    except ValueError as e:
        assert "unknown judge provider" in str(e)
    else:
        raise AssertionError("should have refused")


# --- the suite -------------------------------------------------------------

def test_suite_runs_with_a_scripted_judge():
    cfg = Config.load(ROOT / "config.yaml")
    cfg.weights.judge = 0.2
    goldens = runner.load_goldens(ROOT / "goldens")
    suite = runner.run_suite(goldens, cfg, agent_version="v1.0-good",
                             judge_client=ScriptedJudge(reply(1.0, "meets rubric")))
    judged = [t for t in suite.tasks if "judge" in t.score_map()]
    assert judged, "billing_dispute declares a rubric, so it should be judged"
    assert all(t.score_map()["judge"] == 1.0 for t in judged)


def test_judge_is_off_in_the_committed_config():
    """CI must not need an API key or spend money by default."""
    cfg = Config.load(ROOT / "config.yaml")
    assert cfg.judge.enabled is False
    assert cfg.weights.judge == 0.0
    assert runner.build_judge(cfg) is None
