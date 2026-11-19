# 01 — Concepts, from zero

Nine ideas. Each one builds on the last. By the end you will be able to read
the repo's README without looking anything up.

---

## 1. A language model is a text predictor

A large language model (LLM) takes text in and produces text out. That is the
whole of it. It has no memory between calls, no ability to run anything, and
no access to your data. Everything else you have seen built on top of one is
plumbing around that single capability.

Two consequences matter here:

- **It is non-deterministic.** Ask the same question twice and you can get
  two different answers, because the model picks each next word from a
  probability distribution. (You can reduce this with a `temperature` of 0,
  but not eliminate it.)
- **It cannot know facts it was not given.** If you ask about order A1001, it
  will produce something *shaped like* an answer about order A1001 whether or
  not it has ever seen that order. This is what people mean by
  "hallucination" — not lying, but fluent invention.

## 2. A tool is a function the model may ask you to run

You cannot let a model run code. What you can do is describe some functions
to it, and let it ask.

The model is told, in a structured format: *here are eight functions, their
names, what they do and what arguments they take.* When it wants one, it
replies not with prose but with a structured request:

```json
{"name": "get_order", "arguments": {"order_id": "A1001"}}
```

Your code — not the model — then actually runs `get_order("A1001")`, gets a
real answer from a real database, and sends that answer back to the model as
more text. The model continues from there.

In this repo the tool definitions live in `real_agent/schemas.py` and the
functions they map to live in `demo_agent/tools.py`. Go and look at them now;
they are short and they will make this concrete.

## 3. An agent is that loop, repeated

```
         ┌──────────────────────────────────────┐
         │  send the conversation to the model  │
         └──────────────────┬───────────────────┘
                            v
                 does it want a tool?
                   │                │
                  yes               no
                   │                │
                   v                v
         ┌──────────────────┐   ┌──────────────────┐
         │ run the tool,    │   │ its text is the  │
         │ append the result│   │ final answer     │
         └────────┬─────────┘   └──────────────────┘
                  │
                  └──> back to the top
```

That is an agent. There is nothing more to it architecturally — the
intelligence is in the model and the tools, not the loop. The real loop in
this repo is `real_agent/loop.py`, about 100 lines, and you can read the
whole thing.

The loop needs one safety feature: a **step limit**. A model that loses track
can call tools forever, so after N rounds you stop. In this repo that is
`max_steps` (default 12), and when it trips, the agent says it could not
finish rather than inventing an answer.

## 4. A trajectory is the record of one run

Everything the agent did, in order:

```
step 0   thought       "I should look up the order first"
step 1   tool_call     get_order(order_id="A1001")
step 2   tool_result   ok: {"amount": 120.0, "days_since_delivery": 5, ...}
step 3   tool_call     check_refund_eligibility(order_id="A1001")
step 4   tool_result   ok: {"eligible": true, ...}
step 5   tool_call     issue_refund(order_id="A1001", amount=120.0)
step 6   tool_result   ok: {"ref": "RF-A1001"}
step 7   final         "I've approved a refund of $120.00 for order A1001..."
```

Other names for the same thing: a **trace**, a **run**, an **execution path**.
In this repo it is the `Trajectory` class in `trajgate/schema.py`, and
`examples/trace-refund_happy-v1.1-regressed.json` is a real one you can read.

Each step records more than you might expect: not just the tool and its
arguments, but whether it succeeded, how long it took, and how many tokens
it consumed. Those extra fields are what make cost and latency testing
possible later.

## 5. An eval is a test suite that scores instead of asserting

Ordinary unit testing asserts equality:

```python
assert add(2, 2) == 4        # true or false, nothing in between
```

You cannot do that with an agent. "I've approved a refund of $120.00" and
"A refund of $120 has been approved" are both correct, and neither equals a
string you wrote in advance. So instead of asserting, you **score**: you
define what good looks like along several dimensions, measure each one from
0.0 to 1.0, and combine them.

That is an eval. The word is used rather than "test" precisely to signal
"scored, not asserted".

Two kinds matter for this project:

- **Outcome eval** — score the final answer only. Most teams have this.
- **Trajectory eval** — score the steps. Much rarer, and the subject here.

## 6. A golden is the known-good path, written by a human

To score a path you need something to score it against. That is a **golden**
(also "golden path", "golden dataset", "reference trajectory"): one file per
task, written once by a person, saying what should happen.

Here is a real one, trimmed. The full file is `goldens/refund_happy.yaml`:

```yaml
task_id: refund_happy
prompt: "My order A1001 arrived damaged. I'd like a refund please."

required_tools: [get_order, check_refund_eligibility, issue_refund, send_email]
forbidden_tools: [escalate_to_human]
critical_tools: [check_refund_eligibility]
must_precede: [[check_refund_eligibility, issue_refund]]

ideal_steps: 4
max_steps: 6

expect:
  contains_all: ["A1001"]
  contains_any: ["refund", "refunded"]
```

Read that as English: *for this task, those four tools must be called, in that
order; escalating is wrong; skipping the eligibility check is not a lost point
but a blocked merge; it must happen before the refund; four tool calls is
ideal and six is the limit; and the answer must mention A1001 and the word
refund.*

The important and slightly uncomfortable fact: **a human has to write this.**
No tool can tell you that `check_refund_eligibility` is a safety gate rather
than one step among four, because that is a fact about your business, not
about your logs. The repo has a command (`trajgate induct`) that drafts
everything *observable* from real runs and deliberately refuses to guess the
intent. Doc [04](04-file-by-file.md) covers it.

## 7. Scoring: eight dimensions, not one

This is the heart of the repo. Rather than one "is it good" number, each run
is scored on eight separate questions, each 0.0 to 1.0:

| Grader | The question | Weight |
|---|---|---|
| `outcome` | Was the final answer right? | 0.40 |
| `tool_selection` | Did it use the right tools? | 0.20 |
| `arg_quality` | With real, sensible argument values? | 0.10 |
| `order` | In a sensible sequence? | 0.10 |
| `efficiency` | Without wandering? | 0.10 |
| `redundancy` | Without repeating itself? | 0.10 |
| `recovery` | Did it handle tool errors properly? | 0.05 |
| `safety` | Did it avoid forbidden actions? | 0.05 |

Those are combined into one **composite** score by weighted average. Keeping
them separate first is the point: "0.93" tells you nothing, but "answer 1.00,
tools 0.86, safety 0.00" tells you exactly what happened.

Two derived numbers matter as much as the composite:

- **path score** — the same weighted average *excluding* `outcome`. How good
  was the route, ignoring whether it happened to land on the right answer.
- **lucky pass** — a flag raised when the outcome is 1.0 and the path score
  is poor. Right answer, bad path. The single most useful thing this project
  produces, and the thing plain output testing structurally cannot see.

Some rules are not worth points at all — they are vetoes. Calling a forbidden
tool, skipping a critical check, or breaking an ordering precondition sets a
**hard fail**, which blocks the merge whatever the score says. The reasoning:
when the regressed agent first skipped the eligibility check it lost 0.029
points, a rounding error, for moving money without permission. Some things
are not a dial.

## 8. A baseline is "how good it was before"

A score on its own answers very little. Is 0.87 good? Nobody knows.

What you can answer is: **is it worse than it was yesterday?** So you save a
scorecard from a run you were happy with, commit that file to the repo, and
compare every future run against it. That file is the **baseline**
(`baselines/baseline.json` here).

This reframes the whole problem usefully. You are not trying to certify an
agent as good — an unsolved problem. You are trying to detect that it got
worse — a tractable one.

When a change is a genuine improvement, you update the baseline in the same
pull request. Because the baseline is a file in git, moving the goalposts is
a diff somebody has to review, rather than a button somebody clicks.

## 9. CI and the gate: how a number stops a merge

**CI** (continuous integration) is a service that runs commands
automatically whenever someone proposes a change. GitHub's version is GitHub
Actions, configured by a YAML file in `.github/workflows/`.

The mechanism is almost disappointingly simple. Every command-line program
ends by returning an **exit code**: `0` for success, anything else for
failure. CI runs your command and looks at that single number. If it is not
zero, the check goes red. If you have marked that check as *required* in the
repository settings, a red check means the merge button is disabled.

So the whole gate is this line in `trajgate/cli.py`:

```python
return decision.exit_code        # 0 = merge allowed, 1 = blocked
```

Everything else in the repo exists to decide what that number should be, and
to explain the decision to the human whose pull request just went red.

```
agent change ──> eval runs ──> compared to baseline ──> exit 0  ──> merge allowed
                                                    └──> exit 1  ──> MERGE BLOCKED
                                                                     + a comment
                                                                       saying why
```

---

## Check yourself

If you can answer these without scrolling up, move on to doc
[02](02-walkthrough.md).

1. Why can't you test an agent with `assert output == expected`?
2. What is the difference between an outcome eval and a trajectory eval?
3. What is a lucky pass, and why can't output testing detect one?
4. Why is `safety` weighted only 0.05 when it is the most important grader?
5. Why compare against a baseline instead of a fixed pass mark?
6. What single thing does CI actually look at to decide to block a merge?

Answers: (1) the right answer has many valid wordings, so you score rather
than assert; (2) outcome scores the final answer, trajectory scores the
steps; (3) right answer reached by a bad path — invisible to output testing
because the output is fine; (4) because it does not need weight, it has a
veto — any violation is a hard fail; (5) because "is this agent good" is
unanswerable while "did this change make it worse" is answerable; (6) the
exit code of the command it ran.
