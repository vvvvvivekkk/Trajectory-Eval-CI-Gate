# 04 — Every file explained

What each file is, why it exists, and whether you need to read it. Line
counts are real, so you can see where the weight actually sits.

```
trajgate/        2,443 lines   the library: schema, graders, gate, reports
tests/           1,679 lines   182 tests
real_agent/        ~500 lines  the real LLM agent
demo_agent/        ~400 lines  the offline scripted agent
goldens/            7 files    the test cases
docs/                          these documents
```

Two things worth noticing before the detail. First, there are nearly as many
lines of tests as of library — for a tool that blocks other people's work,
that ratio is the point, not padding. Second, the whole library is smaller
than most single files in a web app. Trajectory eval is not technically hard;
it is just unglamorous, which is why fewer people have done it.

---

## `trajgate/` — the library

### `schema.py` (327 lines) — read this first

Every data shape in the project, as pydantic models. Pydantic validates data
against typed classes: pass a string where a number is expected and it raises
immediately, with a message naming the field.

Three groups:

**What happened** — `Step` (one move: a thought, a tool call, a tool result,
the final answer) and `Trajectory` (the whole run, plus helper methods like
`call_names()` and `failed_calls()` that the graders use constantly).

**What should have happened** — `Golden` (one test case), plus the pieces it
is built from: `Expect` (answer assertions), `ArgRule` (argument vetoes),
`ArgQuality` (argument scores), `PathVariant` (an alternative route),
`JudgeSpec` (a rubric).

**What came out** — `GraderScore` (one grader's verdict), `TaskResult` (one
task, fully scored), `SuiteResult` (all tasks).

Read this file first because every other file is about moving data between
these shapes. If you understand `Trajectory` and `Golden`, the rest of the
repo is mechanical.

### `graders.py` (735 lines) — the actual work

Eight scoring functions, each taking `(trajectory, golden)` and returning a
`GraderScore` between 0.0 and 1.0: `grade_outcome`, `grade_tool_selection`,
`grade_order`, `grade_efficiency`, `grade_redundancy`, `grade_recovery`,
`grade_safety`, `grade_arg_quality`. Then `grade()` at the bottom runs them
all, scores the path graders against every acceptable route and keeps the
best match, prices the run, derives the flags, and returns a `TaskResult`.

The longest file, and the one worth reading closely. Doc
[05](05-code-explained.md) goes through the interesting parts line by line.

### `gate.py` (219 lines) — the decision

`evaluate(current, baseline, cfg)` compares two `SuiteResult`s and returns a
`GateDecision` with `blocked`, a list of `reasons`, a list of `warnings`, and
a per-task `TaskDelta`. `GateDecision.exit_code` is `1` if blocked else `0`.

The whole file is policy, not computation: *what counts as bad enough to stop
a merge.* That makes it the file most worth arguing about with a colleague,
and the file you would change first on a real team.

### `runner.py` (170 lines) — orchestration

Loads goldens and trace files, runs the suite, handles repeats and the
median-of-N, attaches the stability numbers, saves and loads scorecards. Also
`build_judge()`, which returns `None` when the judge is off — which is the
default, so the common path spends nothing.

### `report.py` (281 lines) — three audiences

The same results rendered three ways: `terminal()` for you, `markdown()` for
the pull-request comment, `html_report()` for a standalone page. Also
`graders_present()`, which decides the table's columns — the judge column
only appears when the judge actually ran, so the report never implies a
grader was involved when it was not.

### `pricing.py` (80 lines) — tokens to money

A configurable table of dollars per million tokens, input and output priced
separately (they often differ about fivefold). `cost_usd()` does the sum,
`fmt_usd()` formats sub-cent amounts readably.

The design decision worth copying: **prices are configuration, not constants
in code**, and an unpriced model raises by default rather than silently
costing $0. A cost gate that quietly stops working is worse than no cost
gate.

### `judge.py` (273 lines) — the optional LLM judge

For qualities with no keyword — tone, whether a refusal was explained.
Contains the client protocol, Anthropic and OpenAI clients over stdlib
`urllib` (no SDK needed), `ScriptedJudge` for tests, `CachedJudge` so an
unchanged pull request costs nothing on re-run, and `parse_verdict()` which
pulls `{"score": .., "reason": ".."}` out of whatever the model says.

The part to study is the failure handling: if the model is unreachable or
replies with nonsense, the judge is marked `excluded` and dropped from the
weighted average rather than scored 1.0. A grader that silently passes when
it breaks is worse than no grader.

### `induct.py` (297 lines) — drafting goldens from logs

`induct()` takes runs you liked and infers the observable parts of a golden:
tools called always versus sometimes, whether every run agreed on order, the
step and retry budgets, the facts in every answer, and — the interesting bit
— which argument values were *grounded* in earlier tool output, by walking
each value back through previous results.

`to_yaml()` writes it as commented YAML headed "DRAFT … REVIEW IT", with the
intent fields left empty and a note saying why. See doc
[02](02-walkthrough.md) step 6.

### `adapters.py` (135 lines) — your agent's logs in

`from_openai_messages()` for an OpenAI/Anthropic-style message list,
`from_events()` for a generic event stream, `write_jsonl()` to save them.
About 60 lines of real logic, and the reason this project is usable on
something other than its own demo.

### `cli.py` (210 lines) — the commands

`run`, `gate`, `baseline`, `trace`, `induct`, built with `argparse`. Also
`_step_summary()`, which appends the markdown report to
`$GITHUB_STEP_SUMMARY` so it appears on the Actions run page — eight lines
that make CI output pleasant to read.

### `config.py` (82 lines) and `__main__.py` (5 lines)

`Weights`, `GateThresholds`, `JudgeConfig`, `PricingConfig`, `Config.load()`.
And the five lines that make `python -m trajgate` work.

---

## `goldens/` — the test cases

One YAML per task. These, not the code, are what actually determine whether
the eval is any good — and each was chosen to exercise a different failure.

| File | What it tests |
|---|---|
| `refund_happy.yaml` | The common case, with a safety gate: check before you move money. Where the headline example lives. |
| `refund_ineligible.yaml` | The agent must **refuse and escalate**. The case output testing scores worst, because a confident wrong answer reads better than a correct refusal. |
| `kb_password_reset.yaml` | A pure how-to question: one tool, no customer data, a real money ceiling. Catches the unfilled `{{query}}` template. |
| `order_lookup_recovery.yaml` | Two-step lookup by email, with grounding: the order fetched must be one the customer lookup returned. |
| `flaky_order_lookup.yaml` | A tool that fails twice. Retrying is **correct**; calling it again after success is not. |
| `billing_dispute.yaml` | Order matters, escalation required, refunding forbidden — plus the only LLM-judge rubric in the suite. |
| `order_status_two_routes.yaml` | Two equally valid routes, so the eval cannot insist on one. |

If you build your own suite, copy this *distribution* rather than this
content: a couple of happy paths, every money-or-data-touching case, the
refusals, one per failable tool, and every bug you have already fixed.

---

## `demo_agent/` — the offline agent

`tools.py` is a fake support backend: three orders, three customers, a tiny
knowledge base, and a tool that can be told to fail N times. `agent.py` holds
four scripted policies — `v1.0-good`, `v1.1-regressed`, `v1.2-fixed`,
`v1.3-flaky` — standing in for four commits of a real agent.
`tool_catalogue.txt` lists the eight tool names, so `induct` can suggest
which unused ones might be worth forbidding.

Why scripted: a CI demo that needs an API key, costs money and returns
different numbers every run is one nobody keeps running. Being able to
*choose* the bug is also what makes the tests possible — you cannot ask a
real model to reliably skip a safety check so you can prove the gate catches
it.

---

## `real_agent/` — the real LLM agent

`schemas.py` — the eight tools described in both the Anthropic and the OpenAI
formats, plus the system prompt stating the rules the goldens grade.

`client.py` — `AnthropicClient` and `OpenAIClient` over stdlib `urllib`, plus
`ScriptedClient` for tests. Also the per-API message shapes, which are
genuinely different: Anthropic takes tool results as content blocks in one
user message, OpenAI wants one message per result.

`loop.py` — the agent loop. Real dispatch, real error handling (unknown tool,
bad arguments, tool failure), a step limit that escalates rather than lying,
and token accounting from the API's own usage figures.

`run.py` — run the golden suite against a real model and write a `.jsonl`
you then grade with `trajgate run --traces`.

Read `loop.py` even if you never run it with a key. It is about 100 lines and
it is what every agent framework is doing underneath.

---

## `tests/` — 182 tests

| File | Tests |
|---|---|
| `test_graders.py` | Each grader: a case that should score 1.0 and a case that should not |
| `test_gate.py` | Each blocking rule, and each rule that only warns |
| `test_arg_quality.py` | Placeholders, grounding, hallucinated arguments, JSON-string outputs |
| `test_judge.py` | Parsing, caching, and that a broken judge is excluded rather than passed |
| `test_pricing.py` | The arithmetic, budgets, and that route changes don't become a loophole |
| `test_flakiness.py` | Spread, pass-disagreement, median-not-best |
| `test_multipath.py` | Alternative routes, and that scores are never mixed across them |
| `test_induct.py` | Inference, and that drafts accept their own runs yet still catch the regression |
| `test_real_agent.py` | The real loop: dispatch, errors, step limit, end-to-end grading |
| `test_end_to_end.py` | Green → blocked → fixed, and that every golden has a baseline entry |

Worth reading as documentation. Each test name is a sentence about a decision,
and several encode a mistake that was made and fixed — `test_redundancy_
allows_retry_after_failure` exists because the first version scored correct
retry behaviour as waste.

---

## Configuration and CI

**`config.yaml`** — weights, thresholds, prices, judge settings. Changing a
weight here changes every score, so changing it is a deliberate act with a
diff.

**`baselines/baseline.json`** — the committed scorecard new runs are judged
against. In git on purpose: moving the goalposts should be reviewable.

**`AGENT_VERSION`** — one line naming which demo agent CI grades. A pull
request editing this line is how the repo demonstrates a blocked merge.

**`.github/workflows/trajectory-gate.yml`** — the gate: install, run the
grader's own tests, grade the agent, write the run summary, upload the
report, comment on the PR, and fail the job on a non-zero exit.

**`Makefile`** — `make demo`, `test`, `flaky`, `induct`, `real`, `baseline`,
`green`, `red`. A Makefile here is just a list of named commands; `make demo`
runs the lines under `demo:`.

---

## Reading order, if you want one

1. `goldens/refund_happy.yaml` — what "good" means, in English
2. `trajgate/schema.py` — the shapes everything moves between
3. `demo_agent/agent.py` — what an agent run looks like
4. `trajgate/graders.py` — how the gap is scored
5. `trajgate/gate.py` — what counts as bad enough to block
6. `real_agent/loop.py` — what a real agent loop is
7. `tests/test_graders.py` — the decisions, as assertions

Doc [05](05-code-explained.md) walks through 4 and 6 in detail.
