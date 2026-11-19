# 09 — How to talk about this

If you built this, or worked through it properly, you can hold a real
conversation about agent evaluation. This doc is about doing that without
overclaiming — which matters, because the fastest way to lose a technical
conversation is to be caught describing work you did not do.

---

## The honest framing

Say what is true:

> *"I built a trajectory eval harness. It grades the path an agent took —
> which tools, in what order, with what arguments — against a human-written
> golden path, compares the result to a committed baseline, and fails CI when
> the agent regresses. The demo agent is scripted so the suite runs offline
> and free in CI; there is also a real LLM agent loop, and the graders don't
> know the difference because they only consume a trajectory."*

Then the detail that shows you understand the problem rather than the code:

> *"The case that motivated it: an agent that skips the refund-eligibility
> check and still gives the customer a perfect answer. Output testing passes.
> My suite fails it as a hard fail and names the broken precondition."*

Do not say "I built an eval platform", do not imply it ran in production, and
do not call ~2,400 lines a framework. The work is defensible on its own; it
does not need inflating.

---

## The thirty-second version

> *"Agents are tested on their final answer, which hides the agent that got
> lucky after six wrong tool calls, and the agent that got quietly worse last
> Tuesday. I grade each step — tool choice, order, argument grounding,
> efficiency, redundancy, error recovery, safety — compare against a
> baseline in git, and exit non-zero so CI blocks the merge. The interesting
> part is the hard rules: skipping a check before moving money isn't a lost
> 0.03, it's a blocked merge."*

Then stop. Let them pick which part to dig into.

---

## Questions you should expect

### "Why not just use an LLM as a judge?"

> *"For the final answer, sometimes you have to — tone has no keyword, and
> there's a judge in here for that. But a grader that blocks merges should be
> free, instant and reproducible, and a model is none of those. So the
> default graders are arithmetic, and the judge is off by default, weightless
> by default, cached on disk, and — this is the part I care about — excluded
> from the score rather than scored 1.0 when it fails. A grader that silently
> passes when it breaks is worse than no grader."*

### "How do you know your goldens are right?"

The honest answer, which is also the strongest:

> *"I don't, fully. A golden encodes intent, and intent can be wrong. Three
> things help: every golden has to justify itself by naming a bug it would
> catch; the `induct` command drafts the observable parts from real runs so I
> am not inventing paths from imagination; and production incidents become
> goldens. But a suite only measures what someone thought to write down, and
> I'd say that out loud rather than pretend otherwise."*

### "What happens when the agent legitimately changes its approach?"

> *"That broke my first version. A golden with one canonical path marks a
> good agent wrong, and an eval that cries wolf gets switched off. So goldens
> accept multiple routes and the path graders score against the best-matching
> one — as a unit, so a run never gets one route's tool score and another's
> order score. The knock-on was interesting: a different route costs a
> different amount, so cost creep only blocks on a like-for-like route, and I
> had to add absolute per-task budgets so that allowance couldn't be used as
> a loophole."*

That answer is strong because it is a decision, a consequence, and a
mitigation — which is what engineering judgement sounds like.

### "Agents are non-deterministic. Isn't this flaky?"

> *"It would be if I reported one run. I run each task N times, report the
> **median** — never the best — and report the spread beside it. A task
> counts as unstable if the spread is too wide *or* the runs disagree about
> pass/fail. That second rule earns its keep: in my demo one task passes 40%
> of runs with a spread of only 0.08, so spread alone would have called it
> stable. And the tolerances have to sit above your measured noise floor, or
> the gate fires on nothing real and someone disables it."*

### "What would you do differently at scale?"

> *"Three things. First, sample real production traces into the suite
> automatically instead of hand-writing every case — induction is the start
> of that. Second, store results in a database rather than one JSON baseline
> in git; the git approach makes moving the goalposts reviewable, which I
> like, but it doesn't give you trend lines over months. Third, per-tool
> latency budgets — I gate on cost and step count but not on the slow tool
> that makes the agent feel broken."*

### "What can't this catch?"

> *"Anything nobody wrote a golden for, which is most of the interesting
> failures. It also can't tell a wrong-but-real argument from a right one — if
> the agent refunds the correct amount for the wrong order and both numbers
> came from a tool, my grounding check is satisfied. Cross-field consistency
> needs either a specific rule or a judge. And it measures the agent, not the
> product: a perfectly scored agent can still be solving the wrong problem."*

### "Did this ever catch a real bug?"

Be precise about scope:

> *"It caught three while I was building it, which is the honest answer. My
> first redundancy grader scored correct retry behaviour as waste, so the
> eval was telling agents to give up. My cost rule blocked a legitimate route
> change because comparing two different routes isn't like-for-like. And
> grounding checks silently did nothing on real traces — the demo agent stores
> tool outputs as dicts and the real loop stores them as JSON strings, so the
> check passed in tests and measured nothing in reality. That last one is the
> bug class I'd worry about most in any metrics code: it still reports a
> number."*

Three found bugs in your own measurement code is a better answer than a
vague claim about production.

---

## What to show, in order

1. **The one-screen output.** The blocked run, with `refund_happy` scoring
   1.00 on the answer and failing anyway. That is the whole pitch.
2. **The golden file.** `refund_happy.yaml` reads as English and shows you
   thought about intent, not just metrics.
3. **One grader.** `grade_redundancy`, with the retry distinction, because it
   shows a decision rather than a formula.
4. **A test.** `test_recovery_punishes_inventing_an_answer` — it names a real
   agent failure mode in its title.

Four artefacts, two minutes. Do not walk anyone through the file tree.

---

## Where a reasonable engineer would disagree with you

Know these, because being able to argue the other side is the actual signal.
Each of these is a genuine choice, not a settled fact.

| Decision here | The case against |
|---|---|
| Deterministic graders, judge optional | For open-ended assistants, a judge may be the only thing that correlates with user satisfaction, and string assertions measure the measurable rather than the important. |
| Baseline in git | Doesn't scale to hundreds of tasks or give trends; a results database with dashboards is what most teams end up with. |
| Block regressions, not absolute badness | An agent can rot slowly in increments that each pass. The `min_mean_score` floor exists for this and is off by default. |
| Hand-written golden paths | Expensive, and encodes one person's opinion of the right path. Some teams prefer scoring outcomes plus invariants ("never refund without checking") and leaving the path free. |
| Step-level grading at all | If your agent's path changes every release by design, path grading is friction. Then invariants plus outcomes is the better trade. |
| The efficiency constant (`2 × ideal`) | Entirely hand-picked. Honest answer: it should be calibrated against your own distribution of step counts. |

If someone raises one of these, agree that it is a real trade-off and say
what would make you switch. That is a much better conversation than defending
your repo.

---

## Three things not to say

**"It's production-ready."** It is a well-tested demonstration of a technique.
Say that instead.

**"This catches all agent regressions."** It catches regressions on the cases
you encoded, in the dimensions you measured. The gap between those two
sentences is where the interesting work is.

**"I used AI to build it."** Not because it is shameful — it is normal — but
because the question that follows is *"so what do you understand about it?"*,
and the answer has to be everything. If you can derive the composite by hand
(doc [07](07-the-maths.md)), explain why F1 rather than an average, and name
the three bugs the eval found in itself, nobody will ask how it was typed.
That is the actual bar, and it is reachable: work through doc
[08](08-extend-it.md) and you will clear it.

---

## A short written version, for a CV bullet or a README

> **Trajectory Eval CI Gate** — step-level evaluation harness for LLM agents.
> Grades tool choice, sequence, argument grounding, efficiency, error
> recovery and safety against human-written golden paths; detects
> "lucky passes" where a correct answer is reached by an unsafe path;
> compares against a committed baseline and blocks pull requests on
> regression via GitHub Actions. Includes cost gating in dollars, stability
> measurement across repeated runs, and a command that drafts goldens from
> observed traces. Python, pydantic, 182 tests.

One claim per clause, all of them checkable in the repo. That is the standard
worth holding yourself to.
