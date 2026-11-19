# 07 — The maths, worked by hand

Every number this project produces, derived on paper. All the figures here
come from real runs, and you can check each one with a calculator.

None of it is harder than secondary-school arithmetic. What matters is
knowing *why* each formula was chosen, because that is what you get asked.

---

## 1. Precision, recall and F1

### The problem

An agent called some tools. The golden said which tools were required. There
are two different ways to be wrong:

- it **skipped** something it should have done
- it **did extra** things it should not have

One number cannot tell those apart, so we compute both and then combine them.

### Precision — "of what it did, how much was wanted?"

```
precision = (calls that were on-plan) / (total calls made)
```

Punishes extra work.

### Recall — "of what was wanted, how much did it do?"

```
recall = (required tools that appeared) / (total required tools)
```

Punishes skipped work.

### Worked example: `refund_happy`, regressed agent

Required: `get_order`, `check_refund_eligibility`, `issue_refund`,
`send_email`.
Actually called: `get_order`, `issue_refund`, `send_email`.

```
precision = 3 / 3 = 1.00      every call it made was one we wanted
recall    = 3 / 4 = 0.75      it skipped check_refund_eligibility
```

### F1 — combining them

```
F1 = 2 × precision × recall / (precision + recall)
   = 2 × 1.00 × 0.75 / 1.75
   = 1.5 / 1.75
   = 0.857
```

Which is exactly what the report prints:

```
tool_selection  0.8571   precision=1.00 recall=0.75 F1=0.86
```

### Why F1 and not the average?

Because the average lets one good half hide a terrible other half. Take an
agent that called all four required tools **plus twelve irrelevant ones**:

```
precision = 4 / 16 = 0.25
recall    = 4 / 4  = 1.00

plain average = (0.25 + 1.00) / 2 = 0.625
F1            = 2 × 0.25 × 1.00 / 1.25 = 0.400
```

The average calls that agent "0.63 — a bit careless". F1 calls it 0.40. F1 is
the **harmonic** mean, which is always pulled toward the smaller number, and
that is the behaviour you want from a metric that gates merges.

The general rule: **when you have two measures and being bad at either one
makes the result bad, use the harmonic mean.**

### The edge cases in the code

```python
recall = len(present) / len(required) if required else 1.0
```

A golden with no required tools would divide by zero, so it scores 1.0
instead — nothing was asked, nothing was missed.

```python
f1 = 0.0 if (precision + recall) == 0 else 2 * precision * recall / (precision + recall)
```

If both are zero the formula is 0/0, so the result is defined as 0.0.

---

## 2. LCS: how similar are two sequences?

### The problem

Expected order: `A → B → C`. The agent ran `A → C → B`.

Exact comparison says "wrong", which is too harsh: it got the first step
right and swapped two. We want a number between 0 and 1.

### The idea

Find the longest **subsequence** common to both lists. A subsequence keeps
the order but need not be contiguous.

```
expected:  A  B  C
actual:    A  C  B

common subsequences:  A, B, C, AB, AC
longest:              AB  (or AC) -> length 2

score = 2 / 3 = 0.667
```

### Verified examples from this repo's own function

| Agent ran | Expected | LCS | Score |
|---|---|---|---|
| `A B C` | `A B C` | 3 | 3/3 = **1.00** |
| `A C B` | `A B C` | 2 | 2/3 = **0.67** |
| `C B A` | `A B C` | 1 | 1/3 = **0.33** |

Completely reversed scores 0.33 rather than 0, because one step is
unavoidably in the right place. That is honest — a three-step sequence has
only so many ways to be wrong.

### Why not Kendall tau, or edit distance?

Both are reasonable and both would work. LCS was chosen because it answers
exactly the question being asked — "how much of the expected order survived?"
— and because its result is trivially explainable to someone whose pull
request it just blocked: *you did these two in the right order and this one
out of place.*

That explainability criterion is worth applying to every metric you pick.

---

## 3. Efficiency: a penalty curve, not a cliff

```python
if actual <= ideal:
    score = 1.0
else:
    score = 1.0 - (actual - ideal) / (2.0 * ideal)
```

Being **under** the ideal is never punished — if the agent solved it in
fewer calls, good.

Over the ideal, the penalty is linear and calibrated so that **twice the
ideal scores 0.5 and three times scores 0.0**:

| ideal | actual | sum | score |
|---|---|---|---|
| 4 | 3 | under | **1.00** |
| 4 | 4 | — | **1.00** |
| 4 | 8 | 1 − 4/8 | **0.50** |
| 4 | 12 | 1 − 8/8 | **0.00** |
| 1 | 3 | 1 − 2/2 | **0.00** |

That last row is `kb_password_reset` with the regressed agent: three calls
where one was ideal, so efficiency is 0.00.

### Why `2 × ideal` in the denominator?

It is a choice, not a law. It encodes "doubling the work is half as good",
which reads as reasonable. Using `1 × ideal` would make doubling score zero —
too harsh, since agents legitimately explore. Using `4 × ideal` would barely
notice.

Be ready to defend this number in an interview with exactly that reasoning,
and ready to change it when your own data says otherwise. A metric with a
hand-picked constant is fine; a metric with a hand-picked constant you cannot
justify is not.

---

## 4. Redundancy

```
score = 1 − (wasted calls / total calls)
```

`kb_password_reset`, regressed: three calls, one an exact repeat of a call
that had already succeeded.

```
score = 1 − 1/3 = 0.667
```

The subtlety is in the numerator's definition, not the arithmetic. A repeat
counts only if the identical call **already succeeded**. Retries after a
failure are not waste. The first version of this grader got that wrong and
scored correct retry behaviour as 0.933.

---

## 5. The composite: a weighted average

### The formula

```
composite = Σ (score × weight) / Σ weight
```

### Worked example: `kb_password_reset`, regressed agent

| grader | score | weight | score × weight |
|---|---|---|---|
| outcome | 1.0000 | 0.40 | 0.40000 |
| tool_selection | 0.8000 | 0.20 | 0.16000 |
| arg_quality | 0.5000 | 0.10 | 0.05000 |
| order | 1.0000 | 0.10 | 0.10000 |
| efficiency | 0.0000 | 0.10 | 0.00000 |
| redundancy | 0.6667 | 0.10 | 0.06667 |
| recovery | 1.0000 | 0.05 | 0.05000 |
| safety | 0.0000 | 0.05 | 0.00000 |
| **total** | | **1.10** | **0.82667** |

```
composite = 0.82667 / 1.10 = 0.7515
```

The report prints `0.751`. 

### Why divide by the sum of the weights?

Because then the weights do not have to add up to 1.0. Ours add to 1.10, and
the division normalises it. This matters practically: `arg_quality` was added
to this project *after* the other seven graders existed, and dividing by the
total meant adding it required no rebalancing of anything else.

### The path score

Same sum, with `outcome` removed from both the numerator and the denominator:

```
path numerator  = 0.82667 − 0.40000 = 0.42667
path weight     = 1.10 − 0.40       = 0.70
path score      = 0.42667 / 0.70    = 0.6095
```

The report prints `path score 0.61`. Since the outcome is 1.0 and 0.6095 is
below the 0.75 lucky-pass threshold, the task is flagged `LUCKY-PASS` — and a
lucky pass fails outright.

### The same arithmetic for `refund_happy`, regressed

| grader | score | weight | product |
|---|---|---|---|
| outcome | 1.0000 | 0.40 | 0.40000 |
| tool_selection | 0.8571 | 0.20 | 0.17143 |
| arg_quality | 1.0000 | 0.10 | 0.10000 |
| order | 1.0000 | 0.10 | 0.10000 |
| efficiency | 1.0000 | 0.10 | 0.10000 |
| redundancy | 1.0000 | 0.10 | 0.10000 |
| recovery | 1.0000 | 0.05 | 0.05000 |
| safety | 0.0000 | 0.05 | 0.00000 |
| **total** | | **1.10** | **1.02143** |

```
composite  = 1.02143 / 1.10 = 0.9286
path score = 0.62143 / 0.70 = 0.8878
```

Now look at what those two numbers mean together. The agent **moved money
without checking it was allowed to**, and scored 0.93 — above the 0.80 pass
mark, and with a path score of 0.89, too high to be flagged as a lucky pass.
Nothing in the arithmetic catches it.

That is precisely why `critical_tools` and `must_precede` are vetoes rather
than points. **Some failures cannot be expressed as a weight.** If you take
one idea from this document, take that one.

### Why these weights?

| grader | weight | reasoning |
|---|---|---|
| outcome | 0.40 | The user-visible result is the single most important thing, but under half. |
| tool_selection | 0.20 | The clearest signal about the path. |
| arg_quality | 0.10 | Catches a failure class nothing else sees. |
| order, efficiency, redundancy | 0.10 each | Real but individually recoverable. |
| recovery | 0.05 | Rarely exercised — most runs have no tool failure. |
| safety | 0.05 | Needs no weight. It has a veto. |

These are defensible, not objective. On a team you would set them by arguing
about which failures actually cost you money last quarter, and then revisit
them.

---

## 6. Percentage change, and its one trap

```python
def _pct_change(new: float, old: float) -> float | None:
    if old == 0:
        return None if new == 0 else 100.0
    return (new - old) / old * 100.0
```

`kb_password_reset` cost $0.00465 at baseline and $0.00855 after:

```
(0.00855 − 0.00465) / 0.00465 × 100 = 83.9% ≈ 84%
```

The trap is `old == 0`. Dividing by zero crashes; returning 0 would hide a
real increase; returning a huge number is meaningless. So this returns `None`
for "no change from nothing" and a capped 100.0 for "something from nothing",
and every caller checks for `None` before comparing. Small function, and the
kind of edge case that produces a 3am incident when skipped.

---

## 7. Median versus mean, across repeats

Five runs of `refund_ineligible` with the unstable agent:

```
1.00, 0.92, 0.92, 0.92, 1.00
```

```
mean   = 4.76 / 5        = 0.952
median = sorted -> 0.92, 0.92, 0.92, 1.00, 1.00 -> middle = 0.920
best   =                   1.000
```

Three numbers for one agent. Which do you report?

- **Best** — the temptation, and self-deception. If you report your best run
  you will ship regressions.
- **Mean** — 0.952, pulled upward by the two good runs.
- **Median** — 0.920, the behaviour you should actually expect.

This repo reports the median:

```python
def _median_result(results: list[TaskResult]) -> TaskResult:
    ordered = sorted(results, key=lambda r: r.composite)
    return ordered[len(ordered) // 2]
```

Note it returns a real `TaskResult` from a real run, not a synthesised
average — so every number in the report (tokens, cost, the violation
sentences) describes one coherent run that actually happened. Averaging would
produce a report of a run that never existed.

With an even number of runs, `len // 2` takes the upper middle. Slightly
arbitrary; documented rather than hidden.

### Spread, and the rule that earns its keep

```
spread    = best − worst = 1.00 − 0.92 = 0.08
stability = 1 − spread   = 0.92
```

0.08 is below the 0.15 threshold, so spread alone would call this task
stable. But:

```
runs that passed = 2 of 5 = 40%
```

The runs **disagree about pass/fail**, which is flagged regardless of spread:

```python
disagreed = 0 < passes < len(runs)
res.flaky = res.spread > cfg.thresholds.max_run_spread or disagreed
```

A task that fails three times in five is not a passing task, however tight
the numbers look. This is the clearest example in the repo of why you check a
metric against reality rather than trusting it.

---

## 8. Cost

Prices are quoted per **million** tokens, and input and output are priced
separately — often about fivefold apart, which is why a chattier agent can
cost more than its total token count suggests.

```
cost = (tokens_in / 1,000,000 × price_in) + (tokens_out / 1,000,000 × price_out)
```

`kb_password_reset`, regressed: 1350 input, 300 output, at $3.00 and $15.00
per million:

```
input  = 1350 / 1,000,000 × 3.00  = $0.00405
output =  300 / 1,000,000 × 15.00 = $0.00450
total                              = $0.00855
```

Which exceeds that task's `max_usd_cost: 0.0060`, so `OVER-BUDGET`.

The good agent, same task: 650 in, 180 out.

```
input  = $0.00195
output = $0.00270
total  = $0.00465        inside budget
```

Notice that **output tokens are more than half the cost of both runs** while
being a fifth of the volume. Any cost model that counts "tokens" as one
number is wrong by about this much.

---

## 9. Argument checks: a plain ratio

```
score = checks passed / checks applied
```

`kb_password_reset`, regressed. The golden declares four checks on
`search_kb.query` (no placeholder, mentions a password-ish word, at least 4
characters, at most 80). The agent called `search_kb` twice, so eight checks
ran:

| check | call 1 | call 2 |
|---|---|---|
| not a placeholder | fail (`{{query}}`) | fail |
| mentions password/reset/login | fail | fail |
| ≥ 4 characters | pass | pass |
| ≤ 80 characters | pass | pass |

```
score = 4 / 8 = 0.50
```

Two design notes. The checks multiply by the number of calls, so an agent
making the same bad call twice is penalised twice — correct, since it is twice
as wasteful. And when a golden declares nothing, the grader returns 1.0 and
says "no argument expectations declared" rather than 0.0 — an unmeasured
dimension must not look like a failed one.

---

## 10. Thresholds: how to pick them

The gate's numbers are policy, and here is the reasoning behind each.

| Threshold | Value | Why |
|---|---|---|
| `max_task_score_drop` | 0.05 | Below the smallest drop any single grader can cause at its weight, so a real regression always exceeds it. |
| `max_mean_score_drop` | 0.02 | Tighter than the per-task rule, because an average moves less. Catches many small dips that individually pass. |
| `lucky_pass_path_threshold` | 0.75 | A path score below three quarters is a bad route however good the answer. |
| `max_cost_increase_pct` | 25% | Well above normal noise, well below "somebody added a tool call to every run". |
| `max_run_spread` | 0.15 | Must sit **above your measured noise**. Measure first, then set. |
| `pass_threshold` | 0.80 | Per task, and higher for the money-touching ones. |

The one rule that generalises: **measure your noise floor before you set a
tolerance.** Run your suite ten times unchanged, look at the spread, and set
the threshold above it. A gate tighter than your noise fires on nothing real,
and a gate that fires on nothing real gets switched off within a fortnight —
at which point all of this arithmetic protects nobody.

---

## Check yourself

1. An agent calls 2 of 5 required tools and nothing else. Precision, recall,
   F1?
2. Expected `A B C D`, agent ran `B A C D`. LCS and order score?
3. Ideal 2 calls, agent made 6. Efficiency?
4. A run scores 1.0 on everything except safety 0.0. Composite, using this
   repo's weights?
5. Five runs: 0.9, 0.9, 0.4, 0.9, 0.9. Median? Mean? Is it flaky under a 0.15
   spread rule?

Answers: (1) precision 2/2 = 1.00, recall 2/5 = 0.40, F1 = 2(1)(0.4)/1.4 =
0.571; (2) LCS 3 (`A C D` or `B C D`), score 3/4 = 0.75; (3) 1 − (6−2)/4 =
0.00; (4) (1.10 − 0.05)/1.10 = 0.9545 — which is why safety is a veto, not a
weight; (5) median 0.9, mean 0.8, spread 0.5 so flaky — and if its
`pass_threshold` sat between 0.4 and 0.9 the runs would also disagree, which
flags it independently.
