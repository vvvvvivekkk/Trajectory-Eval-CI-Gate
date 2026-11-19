"""Knobs for how strict the grader and the gate are. Loaded from config.yaml."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field


class Weights(BaseModel):
    """How much each grader counts toward the final score for a task.

    They do not have to add up to 1.0 - the code normalises them.
    """
    outcome: float = 0.40        # did it get the right answer?
    tool_selection: float = 0.20  # did it use the right tools?
    arg_quality: float = 0.10    # with sensible, real arguments?
    order: float = 0.10          # in a sensible sequence?
    efficiency: float = 0.10     # without wandering?
    redundancy: float = 0.10     # without repeating itself?
    recovery: float = 0.05       # did it handle errors well?
    safety: float = 0.05         # did it avoid forbidden actions?
    # The LLM judge is weightless unless you give it weight on purpose. Even
    # then, keep it below the deterministic graders: it is the only grader
    # here that can change its mind.
    judge: float = 0.0


class GateThresholds(BaseModel):
    """What counts as a regression bad enough to block a pull request."""
    max_task_score_drop: float = 0.05    # any single task dropping this much = block
    max_mean_score_drop: float = 0.02    # the suite average dropping this much = block
    block_on_pass_flip: bool = True      # a task going pass -> fail = block
    block_on_new_lucky_pass: bool = True  # right answer via a bad path = block
    block_on_hard_fail: bool = True      # forbidden tool / arg violation = block
    max_cost_increase_pct: float = 25.0  # tokens/$ creeping up this much = block
    max_steps_increase_pct: float = 30.0  # step count creeping up this much = block
    block_on_cost_regression: bool = True
    min_mean_score: float = 0.0          # absolute floor, 0 = disabled


class JudgeConfig(BaseModel):
    """The optional LLM judge. Off by default - see trajgate/judge.py."""
    enabled: bool = False
    provider: str = "anthropic"            # or "openai"
    model: str | None = None
    # Caching is what makes a judge affordable and repeatable in CI. Commit
    # the cache file if you want pull-request runs to cost nothing at all.
    cache_path: str | None = ".trajgate/judge-cache.json"


class Config(BaseModel):
    weights: Weights = Field(default_factory=Weights)
    judge: JudgeConfig = Field(default_factory=JudgeConfig)
    thresholds: GateThresholds = Field(default_factory=GateThresholds)
    # A task whose answer is right but whose path scores below this is a
    # "lucky pass" - the most useful thing this whole project detects.
    lucky_pass_path_threshold: float = 0.75
    # How many times to run each task (agents are non-deterministic; the median
    # of several runs is a fairer number than a single lucky or unlucky run).
    repeats: int = 1

    @classmethod
    def load(cls, path: str | Path | None = None) -> "Config":
        if path is None:
            return cls()
        p = Path(path)
        if not p.exists():
            return cls()
        data = yaml.safe_load(p.read_text()) or {}
        return cls.model_validate(data)
