"""
Data shapes for trajectory evaluation.

Two things get described here:

1. A TRAJECTORY  - the path an agent actually took on one task.
                   (Produced by your agent, or converted from your tracing tool.)
2. A GOLDEN      - the path we *expected*, plus what the answer must contain.
                   (Written by a human, once, and committed to the repo.)

Everything in this project is just comparing (1) against (2).
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field


# ----------------------------------------------------------------------------
# 1. The trajectory: what the agent actually did
# ----------------------------------------------------------------------------

class StepKind(str, Enum):
    """What sort of thing happened at this step of the run."""
    THOUGHT = "thought"            # the model reasoning/planning in text
    TOOL_CALL = "tool_call"        # the model asked to run a tool
    TOOL_RESULT = "tool_result"    # the tool answered (ok or error)
    MESSAGE = "message"            # text sent to the user
    FINAL = "final"                # the final answer


class Step(BaseModel):
    """One single move in the run."""
    index: int                                  # 0, 1, 2 ... in order
    kind: StepKind
    tool: str | None = None                     # tool name, for tool steps
    args: dict[str, Any] = Field(default_factory=dict)
    ok: bool | None = None                      # did the tool succeed?
    error: str | None = None                    # error text if it failed
    output: Any = None                          # tool output (trimmed is fine)
    text: str | None = None                     # for thought/message/final
    latency_ms: int = 0
    tokens_in: int = 0
    tokens_out: int = 0

    @property
    def is_call(self) -> bool:
        return self.kind == StepKind.TOOL_CALL


class Trajectory(BaseModel):
    """The whole path an agent took for ONE task, start to finish."""
    task_id: str
    agent_version: str = "unknown"
    run_id: str = "run-1"
    steps: list[Step] = Field(default_factory=list)
    final_answer: str = ""
    # Optional run metadata - used for cost/latency regression checks.
    total_latency_ms: int = 0
    usd_cost: float = 0.0

    # --- convenience views the graders use -----------------------------------

    def calls(self) -> list[Step]:
        """Just the tool_call steps, in order."""
        return [s for s in self.steps if s.is_call]

    def call_names(self) -> list[str]:
        return [s.tool for s in self.calls() if s.tool]

    def tokens(self) -> int:
        return sum(s.tokens_in + s.tokens_out for s in self.steps)

    def latency_ms(self) -> int:
        return self.total_latency_ms or sum(s.latency_ms for s in self.steps)

    def failed_calls(self) -> list[Step]:
        """Tool results that came back as an error."""
        return [s for s in self.steps if s.kind == StepKind.TOOL_RESULT and s.ok is False]


# ----------------------------------------------------------------------------
# 2. The golden: what we expected
# ----------------------------------------------------------------------------

class ArgRule(BaseModel):
    """A constraint on the arguments a tool was called with.

    Example: issue_refund must never be called with amount > 500.
    """
    tool: str
    arg: str
    op: Literal["eq", "neq", "lte", "gte", "contains", "matches", "absent"] = "eq"
    value: Any = None
    # If a violation should block the merge outright, not just lower the score.
    hard: bool = True


class Expect(BaseModel):
    """What the final answer has to look like for the OUTCOME to count as right."""
    contains_all: list[str] = Field(default_factory=list)     # every one must appear
    contains_any: list[str] = Field(default_factory=list)     # at least one must appear
    not_contains: list[str] = Field(default_factory=list)     # none may appear
    regex: str | None = None
    case_sensitive: bool = False


class Golden(BaseModel):
    """One test case: the task, the known-good path, and the pass bar."""
    task_id: str
    prompt: str
    description: str = ""

    # --- the known-good path ---
    required_tools: list[str] = Field(default_factory=list)   # must be called
    optional_tools: list[str] = Field(default_factory=list)   # fine, not required
    forbidden_tools: list[str] = Field(default_factory=list)  # must NEVER be called
    # Required tools whose absence is not a lost point but a blocked merge -
    # the checks you would never let a human agent skip (verify before you
    # refund, read before you write).
    critical_tools: list[str] = Field(default_factory=list)
    # Hard ordering preconditions, as [before, after] pairs. If "after" is
    # called and "before" never ran first, that is a hard fail - not a
    # rounding error on an order score.
    must_precede: list[list[str]] = Field(default_factory=list)
    order_matters: bool = True                                # grade the sequence?
    ideal_steps: int | None = None                            # default: len(required)
    max_steps: int | None = None                              # hard cap
    max_retries_per_tool: int = 2                             # hammering guard
    arg_rules: list[ArgRule] = Field(default_factory=list)

    # --- the answer ---
    expect: Expect = Field(default_factory=Expect)

    # --- the bar ---
    pass_threshold: float = 0.80       # composite score needed to "pass"
    weight: float = 1.0                # importance in the suite average

    def ideal(self) -> int:
        return self.ideal_steps or max(1, len(self.required_tools))


# ----------------------------------------------------------------------------
# 3. Results
# ----------------------------------------------------------------------------

class GraderScore(BaseModel):
    name: str
    score: float                   # always 0.0 - 1.0
    weight: float
    detail: str = ""
    violations: list[str] = Field(default_factory=list)


class TaskResult(BaseModel):
    task_id: str
    agent_version: str
    composite: float               # weighted 0.0 - 1.0
    path_score: float              # same, but ignoring the final answer
    outcome: float                 # 1.0 = right answer, 0.0 = wrong
    passed: bool
    hard_fail: bool = False        # a safety rule was broken
    lucky_pass: bool = False       # right answer, bad path
    steps: int = 0
    tool_calls: int = 0
    tokens: int = 0
    latency_ms: int = 0
    usd_cost: float = 0.0
    scores: list[GraderScore] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)

    def score_map(self) -> dict[str, float]:
        return {s.name: s.score for s in self.scores}


class SuiteResult(BaseModel):
    agent_version: str
    created_at: str
    tasks: list[TaskResult] = Field(default_factory=list)

    @property
    def mean_composite(self) -> float:
        if not self.tasks:
            return 0.0
        return sum(t.composite for t in self.tasks) / len(self.tasks)

    @property
    def pass_rate(self) -> float:
        if not self.tasks:
            return 0.0
        return sum(1 for t in self.tasks if t.passed) / len(self.tasks)

    def by_id(self) -> dict[str, TaskResult]:
        return {t.task_id: t for t in self.tasks}
