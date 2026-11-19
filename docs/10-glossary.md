# 10 — Glossary

Every term used in this repo and its docs. Terms you would hear on an AI
engineering team are marked **(industry)**; terms specific to this repo are
marked *(this repo)*.

## Agents and models

**Agent** **(industry)** — a program that gives a language model tools and
lets it decide which to call, in what order, until a task is done.

**Agent harness** **(industry)** — the code around the model that supplies
tools, manages the conversation, enforces limits and handles errors. The loop
in `real_agent/loop.py` is a small harness.

**Context window** **(industry)** — the maximum amount of text a model can
consider at once. When a conversation outgrows it, something has to be
dropped, which is a common cause of an agent "forgetting" a tool result it
already had.

**Hallucination** **(industry)** — the model producing fluent, plausible,
false content. In an agent this often shows up not in the prose but in an
*argument*: a correct-looking order id or amount that no tool ever returned.

**LLM** **(industry)** — large language model. Text in, text out.

**Non-determinism** **(industry)** — the same input can produce different
output, because the model samples randomly. The reason evals score instead of
asserting, and the reason repeats matter.

**System prompt** **(industry)** — the standing instructions given to the
model before the user's message. `real_agent/schemas.py` holds this repo's.

**Temperature** **(industry)** — how much randomness the model uses when
picking words. 0 is the most repeatable setting; it is not fully
deterministic.

**Token** **(industry)** — the unit models read and bill in, roughly 3-4
characters of English. Input and output tokens are usually priced
differently, often about fivefold apart.

**Tool / function calling** **(industry)** — the mechanism by which a model
asks for a named function to be run with specific arguments, and receives the
result as more conversation.

## Evaluation

**Assertion-based grading** — scoring an answer by checking for required
strings, forbidden strings or a regular expression. Cheap, instant,
reproducible; blind to tone and nuance.

**Composite score** *(this repo)* — the weighted average of all grader
scores for one task, 0.0 to 1.0.

**Eval** **(industry)** — a test suite for an AI system that scores rather
than asserts.

**Golden / golden path / golden dataset** **(industry)** — the known-good
reference for a task, written by a human and committed to the repo.

**Grader** *(this repo)* — one scoring function answering one question about
a run. There are eight deterministic ones plus an optional judge.

**Grounded argument** *(this repo)* — an argument value the agent actually
retrieved from a tool output, rather than produced from the conversation.
Checked with `from_tool_output`.

**Hard fail** *(this repo)* — a violation that blocks the merge on its own,
regardless of score: a forbidden tool, a skipped critical check, a broken
precondition, or an argument veto.

**Induction** *(this repo)* — generating a draft golden from runs you already
have (`trajgate induct`).

**LLM-as-judge** **(industry)** — using a model to score another model's
output against a rubric. Useful for qualities with no keyword; slower, paid,
and itself non-deterministic. Off by default here.

**Lucky pass** *(this repo)* — right answer, bad path. The failure this
project exists to surface.

**Outcome eval** **(industry)** — grading only the final answer.

**Path score** *(this repo)* — the composite recomputed without `outcome`:
how good the route was, ignoring where it landed.

**Rubric** **(industry)** — written criteria a judge grades against.

**Trajectory / trace** **(industry)** — the ordered record of one run: every
thought, tool call, tool result and the final answer.

**Trajectory eval** **(industry)** — grading the steps of a run, not just its
output.

## This repo's golden fields

**`alt_paths`** — other acceptable routes through the tools. The path graders
score against the best-matching one.

**`arg_quality`** — soft expectations about argument values: content checks
and grounding checks. Lowers the score.

**`arg_rules`** — hard vetoes on argument values. Blocks the merge.

**`critical_tools`** — required tools whose absence is a hard fail rather
than a lost point.

**`forbidden_tools`** — tools that must never be called for this task.

**`ideal_steps` / `max_steps`** — the efficiency yardstick, and the hard cap.

**`max_retries_per_tool`** — above this, repeated calls are a retry loop.

**`max_usd_cost` / `max_tokens`** — absolute budgets for one run of the task.
They need no baseline and survive a route change.

**`must_precede`** — `[before, after]` ordering preconditions. Hard.

**`pass_threshold`** — the composite a task needs to count as a pass.

## Metrics and maths

**F1** **(industry)** — one number combining precision and recall:
`2PR / (P + R)`. Sits closer to whichever is worse.

**LCS (longest common subsequence)** **(industry)** — the longest run of items
appearing in the same relative order in two lists. Used to score sequence
without demanding an exact match.

**Median** — the middle value when sorted. Used instead of the mean across
repeats because it is not dragged around by one freak run.

**Precision** **(industry)** — of the calls made, the fraction that were
wanted. Punishes extra work.

**Recall** **(industry)** — of the calls required, the fraction actually
made. Punishes skipped work.

**Spread / stability** *(this repo)* — best run minus worst run across
repeats, and `1 - spread`. How much to trust a single score.

**Weighted average** — a mean where some inputs count more. How the composite
is built.

## Software and CI

**Artifact** **(industry)** — a file a CI run saves for you to download
afterwards. The reports here are uploaded as one.

**Baseline** **(industry)** — the saved scorecard new runs are compared
against.

**Branch protection** **(industry)** — the GitHub setting that makes a check
required, so a red check disables the merge button.

**CI (continuous integration)** **(industry)** — a service that runs checks
automatically on every proposed change.

**Exit code** **(industry)** — the number a command returns: 0 success,
non-zero failure. The entire mechanism by which an eval blocks a merge.

**JSONL** **(industry)** — a text file with one JSON object per line. The
trace format here.

**Pull request (PR)** **(industry)** — a proposed change awaiting review.

**Regression** **(industry)** — something that used to work and now works
worse.

**Pydantic** **(industry)** — a Python library that validates data against
typed classes. Every data shape in this repo is a pydantic model.

**YAML** **(industry)** — a human-friendly text format for configuration.
Goldens and `config.yaml` are YAML.
