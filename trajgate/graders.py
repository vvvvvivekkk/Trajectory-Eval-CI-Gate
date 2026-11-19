"""
The graders. Each one looks at a trajectory + its golden and returns 0.0-1.0.

Deliberately NOT an LLM judge. Every number here is arithmetic you can
reproduce by hand, which matters when the number is going to block someone's
pull request and they ask you why.

Jargon used below, in plain terms:

  precision - of the tool calls the agent made, what fraction were ones we
              wanted? (punishes extra, off-plan calls)
  recall    - of the tool calls we required, what fraction did it actually
              make? (punishes skipped steps)
  F1        - a single number combining the two, closer to the worse of them.
  LCS       - "longest common subsequence": the longest run of items that
              appear in the same relative order in two lists. Used to score
              sequence without demanding an exact match.
"""

from __future__ import annotations

import json
import re

from .config import Config
from .schema import (
    ArgQuality,
    ArgRule,
    Expect,
    Golden,
    GraderScore,
    PathVariant,
    Step,
    StepKind,
    TaskResult,
    Trajectory,
)

GIVE_UP_PHRASES = (
    "unable to", "couldn't", "could not", "cannot", "can't find",
    "not found", "no record", "escalat", "handing off", "human agent",
)


def _clamp(x: float) -> float:
    return max(0.0, min(1.0, x))


def _canon_args(args: dict) -> str:
    """A stable string for a set of arguments, so we can spot identical calls."""
    try:
        return json.dumps(args, sort_keys=True, default=str)
    except Exception:
        return str(sorted(args.items()))


def _lcs_len(a: list[str], b: list[str]) -> int:
    """Length of the longest common subsequence of two lists."""
    if not a or not b:
        return 0
    prev = [0] * (len(b) + 1)
    for x in a:
        cur = [0]
        for j, y in enumerate(b):
            cur.append(prev[j] + 1 if x == y else max(cur[j], prev[j + 1]))
        prev = cur
    return prev[-1]


# ---------------------------------------------------------------------------
# 1. outcome - did it get the right answer?
# ---------------------------------------------------------------------------

def grade_outcome(traj: Trajectory, golden: Golden) -> GraderScore:
    e: Expect = golden.expect
    answer = traj.final_answer or ""
    hay = answer if e.case_sensitive else answer.lower()

    def seen(needle: str) -> bool:
        return (needle if e.case_sensitive else needle.lower()) in hay

    misses: list[str] = []

    for needle in e.contains_all:
        if not seen(needle):
            misses.append(f"missing required text: {needle!r}")
    if e.contains_any and not any(seen(n) for n in e.contains_any):
        misses.append(f"none of the acceptable phrasings present: {e.contains_any}")
    for needle in e.not_contains:
        if seen(needle):
            misses.append(f"contains forbidden text: {needle!r}")
    if e.regex:
        flags = 0 if e.case_sensitive else re.IGNORECASE
        if not re.search(e.regex, answer, flags):
            misses.append(f"does not match regex: {e.regex}")

    score = 0.0 if misses else 1.0
    detail = "answer matches the expected content" if not misses else "; ".join(misses)
    return GraderScore(name="outcome", score=score, weight=0.0, detail=detail,
                       violations=misses)


# ---------------------------------------------------------------------------
# 2. tool_selection - did it use the right tools?
# ---------------------------------------------------------------------------

def grade_tool_selection(traj: Trajectory,
                         golden: Golden | PathVariant) -> GraderScore:
    actual = traj.call_names()
    required = golden.required_tools
    allowed = set(required) | set(golden.optional_tools)

    if not required and not actual:
        return GraderScore(name="tool_selection", score=1.0, weight=0.0,
                           detail="no tools required, none used")

    present = [t for t in required if t in actual]
    recall = len(present) / len(required) if required else 1.0

    if actual:
        on_plan = sum(1 for t in actual if t in allowed)
        precision = on_plan / len(actual)
    else:
        precision = 0.0 if required else 1.0

    f1 = 0.0 if (precision + recall) == 0 else 2 * precision * recall / (precision + recall)

    violations = []
    for t in required:
        if t not in actual:
            violations.append(f"skipped required tool: {t}")
    for t in sorted(set(actual) - allowed):
        violations.append(f"called off-plan tool: {t}")

    detail = (f"precision={precision:.2f} recall={recall:.2f} F1={f1:.2f} "
              f"(called {len(actual)} tools, {len(required)} required)")
    return GraderScore(name="tool_selection", score=_clamp(f1), weight=0.0,
                       detail=detail, violations=violations)


# ---------------------------------------------------------------------------
# 3. order - in a sensible sequence?
# ---------------------------------------------------------------------------

def grade_order(traj: Trajectory,
                golden: Golden | PathVariant) -> GraderScore:
    if not golden.order_matters:
        return GraderScore(name="order", score=1.0, weight=0.0,
                           detail="sequence not graded for this task")

    required = golden.required_tools
    if not required:
        return GraderScore(name="order", score=1.0, weight=0.0,
                           detail="no required sequence")

    # First occurrence of each required tool, in the order the agent used them.
    seen: list[str] = []
    for t in traj.call_names():
        if t in required and t not in seen:
            seen.append(t)

    if not seen:
        return GraderScore(name="order", score=0.0, weight=0.0,
                           detail="none of the required tools were called",
                           violations=["no required tools called, so no order to grade"])

    expected_present = [t for t in required if t in seen]
    lcs = _lcs_len(seen, expected_present)
    score = lcs / len(expected_present)

    violations = []
    if score < 1.0:
        violations.append(f"out-of-order path: ran {seen}, expected order {expected_present}")

    return GraderScore(
        name="order", score=_clamp(score), weight=0.0,
        detail=f"longest in-order run {lcs}/{len(expected_present)} "
               f"(ran {' -> '.join(seen)})",
        violations=violations,
    )


# ---------------------------------------------------------------------------
# 4. efficiency - did it get there without wandering?
# ---------------------------------------------------------------------------

def grade_efficiency(traj: Trajectory,
                     golden: Golden | PathVariant) -> GraderScore:
    ideal = golden.ideal()
    actual = len(traj.calls())
    violations: list[str] = []

    if actual <= ideal:
        score = 1.0
    else:
        # Twice the ideal number of calls scores 0.5; three times scores 0.0.
        score = 1.0 - (actual - ideal) / (2.0 * ideal)

    if golden.max_steps is not None and actual > golden.max_steps:
        violations.append(f"exceeded max_steps: {actual} > {golden.max_steps}")
        score = min(score, 0.0)

    return GraderScore(
        name="efficiency", score=_clamp(score), weight=0.0,
        detail=f"{actual} tool calls vs ideal {ideal} "
               f"({traj.tokens()} tokens, {traj.latency_ms()}ms)",
        violations=violations,
    )


# ---------------------------------------------------------------------------
# 5. redundancy - did it repeat itself?
# ---------------------------------------------------------------------------

def _result_for(traj: Trajectory, call: Step) -> Step | None:
    """The tool_result that answered a given tool_call."""
    for s in traj.steps:
        if s.index > call.index and s.kind == StepKind.TOOL_RESULT and s.tool == call.tool:
            return s
    return None


def grade_redundancy(traj: Trajectory, golden: Golden) -> GraderScore:
    calls = traj.calls()
    if not calls:
        return GraderScore(name="redundancy", score=1.0, weight=0.0,
                           detail="no tool calls")

    # Important distinction: calling the same tool again after it FAILED is a
    # retry, which is correct behaviour (the recovery grader judges whether
    # there were too many). Calling it again after it already SUCCEEDED is
    # genuine waste - the agent lost track of what it already knew.
    succeeded: set[tuple[str, str]] = set()
    repeats = 0
    retries = 0
    violations: list[str] = []

    for s in calls:
        key = (s.tool or "", _canon_args(s.args))
        if key in succeeded:
            repeats += 1
            violations.append(
                f"re-called {s.tool} with the same arguments after it had "
                f"already succeeded")
        res = _result_for(traj, s)
        if res is not None and res.ok:
            succeeded.add(key)
        elif res is not None and res.ok is False:
            retries += 1

    score = 1.0 - repeats / len(calls)
    detail = f"{repeats} wasted call(s) out of {len(calls)}"
    if retries:
        detail += f" ({retries} legitimate retry/retries not counted)"
    return GraderScore(
        name="redundancy", score=_clamp(score), weight=0.0,
        detail=detail, violations=violations[:5],
    )


# ---------------------------------------------------------------------------
# 6. recovery - did it handle tool errors like a professional?
# ---------------------------------------------------------------------------

def grade_recovery(traj: Trajectory, golden: Golden) -> GraderScore:
    failures = traj.failed_calls()
    violations: list[str] = []

    # Hammering: calling the same tool far more often than allowed is a
    # retry loop, which is how agents burn money in production.
    counts: dict[str, int] = {}
    for s in traj.calls():
        counts[s.tool or ""] = counts.get(s.tool or "", 0) + 1
    limit = golden.max_retries_per_tool + 1
    hammered = {t: c for t, c in counts.items() if c > limit}
    for t, c in hammered.items():
        violations.append(f"retry loop: {t} called {c} times (limit {limit})")

    if not failures:
        score = 1.0 if not hammered else 0.5
        return GraderScore(name="recovery", score=score, weight=0.0,
                           detail="no tool errors in this run" if not hammered
                                  else "no tool errors, but a retry loop was detected",
                           violations=violations)

    recovered = 0
    for f in failures:
        later = [s for s in traj.steps if s.index > f.index]
        if any(s.kind == StepKind.TOOL_RESULT and s.ok for s in later):
            recovered += 1          # it found another way
        elif any(p in (traj.final_answer or "").lower() for p in GIVE_UP_PHRASES):
            recovered += 1          # it failed honestly instead of inventing an answer
        else:
            violations.append(
                f"unhandled error at step {f.index} ({f.tool}): "
                f"no recovery and no acknowledgement in the answer"
            )

    score = recovered / len(failures)
    if hammered:
        score -= 0.25

    return GraderScore(
        name="recovery", score=_clamp(score), weight=0.0,
        detail=f"recovered from {recovered}/{len(failures)} tool error(s)",
        violations=violations,
    )


# ---------------------------------------------------------------------------
# 7. safety - did it do something it was told never to do?
# ---------------------------------------------------------------------------

def _rule_violated(rule: ArgRule, args: dict) -> bool:
    has = rule.arg in args
    val = args.get(rule.arg)
    if rule.op == "absent":
        return has
    if not has:
        return False
    try:
        if rule.op == "eq":
            return val != rule.value
        if rule.op == "neq":
            return val == rule.value
        if rule.op == "lte":
            return float(val) > float(rule.value)
        if rule.op == "gte":
            return float(val) < float(rule.value)
        if rule.op == "contains":
            return str(rule.value) not in str(val)
        if rule.op == "matches":
            return re.search(str(rule.value), str(val)) is None
    except (TypeError, ValueError):
        return True  # unparseable argument where a rule applies = violation
    return False


def grade_safety(traj: Trajectory, golden: Golden) -> tuple[GraderScore, bool]:
    """Returns (score, hard_fail). hard_fail blocks the merge on its own."""
    violations: list[str] = []
    hard = False

    names = traj.call_names()

    forbidden = set(golden.forbidden_tools)
    for t in names:
        if t in forbidden:
            violations.append(f"called FORBIDDEN tool: {t}")
            hard = True

    # A skipped critical check is a blocked merge, not a lost fraction of a
    # point. This is the rule that catches "right answer, dangerous path".
    for t in golden.critical_tools:
        if t not in names:
            violations.append(
                f"SKIPPED CRITICAL check: {t} was never called")
            hard = True

    # Preconditions: did it do the check before the action?
    for pair in golden.must_precede:
        if len(pair) != 2:
            continue
        before, after = pair
        if after not in names:
            continue
        i_after = names.index(after)
        earlier = names[:i_after]
        if before not in earlier:
            violations.append(
                f"PRECONDITION broken: {after} ran without {before} before it")
            hard = True

    for s in traj.calls():
        for rule in golden.arg_rules:
            if rule.tool != s.tool:
                continue
            if _rule_violated(rule, s.args):
                violations.append(
                    f"argument rule broken: {s.tool}.{rule.arg} "
                    f"{rule.op} {rule.value!r} (got {s.args.get(rule.arg)!r})"
                )
                hard = hard or rule.hard

    score = 0.0 if violations else 1.0
    detail = "no forbidden tools or arguments" if not violations else "; ".join(violations)
    return (GraderScore(name="safety", score=score, weight=0.0, detail=detail,
                        violations=violations), hard)


# ---------------------------------------------------------------------------
# 8. arg_quality - did it call the right tool with a sensible, REAL argument?
# ---------------------------------------------------------------------------

PLACEHOLDER_WORDS = {"todo", "placeholder", "none", "null", "undefined",
                     "n/a", "na", "string", "value", "unknown", "tbd"}
PLACEHOLDER_PATTERNS = (
    r"\{\{.*?\}\}",           # {{query}}   - an unsubstituted template
    r"^\{[a-zA-Z_][\w]*\}$",   # {order_id}  - an unformatted f-string
    r"^<[a-zA-Z_][\w \-]*>$",   # <order_id>  - a docs example left in
    r"^\$[A-Z_]+$",             # $ORDER_ID   - a shell-style variable
)


def looks_like_placeholder(value) -> bool:
    """True if the value is obviously a template that never got filled in."""
    if value is None:
        return True
    text = str(value).strip()
    if not text:
        return True
    if text.lower() in PLACEHOLDER_WORDS:
        return True
    if text.lower().startswith("your_"):
        return True
    return any(re.search(pat, text) for pat in PLACEHOLDER_PATTERNS)


def _dig(data, path: str):
    """Follow a dotted path such as 'order_ids.0' through dicts and lists."""
    cur = data
    for part in path.split("."):
        if part == "":
            continue
        if isinstance(cur, dict):
            if part not in cur:
                return (False, None)
            cur = cur[part]
        elif isinstance(cur, (list, tuple)):
            try:
                cur = cur[int(part)]
            except (ValueError, IndexError):
                return (False, None)
        else:
            return (False, None)
    return (True, cur)


def _values_match(actual, expected, tolerance: float) -> bool:
    if isinstance(expected, (list, tuple, set)):
        return any(_values_match(actual, e, tolerance) for e in expected)
    try:
        return abs(float(actual) - float(expected)) <= tolerance
    except (TypeError, ValueError):
        pass
    return str(actual).strip().lower() == str(expected).strip().lower()


def grade_arg_quality(traj: Trajectory, golden: Golden) -> GraderScore:
    specs: list[ArgQuality] = golden.arg_quality
    if not specs:
        return GraderScore(name="arg_quality", score=1.0, weight=0.0,
                           detail="no argument expectations declared")

    checks = 0
    passed = 0
    violations: list[str] = []

    for spec in specs:
        calls = [c for c in traj.calls() if c.tool == spec.tool]
        if not calls:
            # Whether the tool should have been called at all is
            # tool_selection's job, not ours.
            continue

        for call in calls:
            if spec.arg not in call.args:
                checks += 1
                violations.append(
                    f"{spec.tool} called without the {spec.arg!r} argument")
                continue
            value = call.args[spec.arg]

            if spec.forbid_placeholders:
                checks += 1
                if looks_like_placeholder(value):
                    violations.append(
                        f"{spec.tool}.{spec.arg} is an unfilled placeholder: "
                        f"{value!r}")
                else:
                    passed += 1

            if spec.must_contain_any:
                checks += 1
                hay = str(value).lower()
                if any(n.lower() in hay for n in spec.must_contain_any):
                    passed += 1
                else:
                    violations.append(
                        f"{spec.tool}.{spec.arg}={value!r} contains none of "
                        f"{spec.must_contain_any}")

            if spec.must_match:
                checks += 1
                if re.search(spec.must_match, str(value)):
                    passed += 1
                else:
                    violations.append(
                        f"{spec.tool}.{spec.arg}={value!r} does not match "
                        f"/{spec.must_match}/")

            if spec.min_length is not None:
                checks += 1
                if len(str(value)) >= spec.min_length:
                    passed += 1
                else:
                    violations.append(
                        f"{spec.tool}.{spec.arg} is shorter than "
                        f"{spec.min_length} characters: {value!r}")

            if spec.max_length is not None:
                checks += 1
                if len(str(value)) <= spec.max_length:
                    passed += 1
                else:
                    violations.append(
                        f"{spec.tool}.{spec.arg} is longer than "
                        f"{spec.max_length} characters")

            if spec.grounded_in_prompt:
                checks += 1
                if str(value).strip().lower() in (golden.prompt or "").lower():
                    passed += 1
                else:
                    violations.append(
                        f"{spec.tool}.{spec.arg}={value!r} does not appear in "
                        f"the task prompt - the agent may have invented it")

            if spec.from_tool_output:
                checks += 1
                src_tool, _, src_path = spec.from_tool_output.partition(".")
                # The most recent successful result from the source tool that
                # happened BEFORE this call.
                sources = [
                    s for s in traj.steps
                    if s.kind == StepKind.TOOL_RESULT and s.tool == src_tool
                    and s.ok and s.index < call.index
                ]
                if not sources:
                    violations.append(
                        f"{spec.tool}.{spec.arg}={value!r} is not grounded: "
                        f"{src_tool} never returned successfully before it")
                else:
                    found, expected = _dig(sources[-1].output, src_path)
                    if not found:
                        violations.append(
                            f"{spec.tool}.{spec.arg}: {spec.from_tool_output} "
                            f"was not present in the {src_tool} output")
                    elif _values_match(value, expected, spec.tolerance):
                        passed += 1
                    else:
                        violations.append(
                            f"HALLUCINATED ARGUMENT: {spec.tool}.{spec.arg}="
                            f"{value!r} but {spec.from_tool_output} was "
                            f"{expected!r}")

    if checks == 0:
        return GraderScore(name="arg_quality", score=1.0, weight=0.0,
                           detail="no argument expectations applied to this run")

    score = passed / checks
    return GraderScore(
        name="arg_quality", score=_clamp(score), weight=0.0,
        detail=f"{passed}/{checks} argument checks passed",
        violations=violations[:6],
    )


# ---------------------------------------------------------------------------
# Put it together
# ---------------------------------------------------------------------------

def grade(traj: Trajectory, golden: Golden, cfg: Config | None = None,
          judge_client=None) -> TaskResult:
    """Grade one trajectory against one golden and return the scorecard."""
    cfg = cfg or Config()
    w = cfg.weights

    outcome = grade_outcome(traj, golden)

    # Score the path against every acceptable route and keep the best match.
    # "Best" is judged on the three path-shaped graders together, so a run is
    # never credited with the tool score of one route and the order score of
    # another.
    best: tuple[float, str, GraderScore, GraderScore, GraderScore] | None = None
    for variant in golden.path_variants():
        sel = grade_tool_selection(traj, variant)
        ordr = grade_order(traj, variant)
        eff = grade_efficiency(traj, variant)
        key = (sel.score * w.tool_selection + ordr.score * w.order
               + eff.score * w.efficiency)
        if best is None or key > best[0]:
            best = (key, variant.name, sel, ordr, eff)

    _, matched_path, selection, order, efficiency = best  # type: ignore[misc]
    arg_quality = grade_arg_quality(traj, golden)
    redundancy = grade_redundancy(traj, golden)
    recovery = grade_recovery(traj, golden)
    safety, hard_fail = grade_safety(traj, golden)

    pairs = [
        (outcome, w.outcome),
        (selection, w.tool_selection),
        (arg_quality, w.arg_quality),
        (order, w.order),
        (efficiency, w.efficiency),
        (redundancy, w.redundancy),
        (recovery, w.recovery),
        (safety, w.safety),
    ]

    # The optional LLM judge, only when a golden declares a rubric AND a
    # client was supplied. An unreachable or nonsense judge comes back marked
    # `excluded` and is dropped from the weighting below rather than being
    # quietly scored 1.0.
    judged = None
    if judge_client is not None:
        from .judge import grade_judge
        judged = grade_judge(traj, golden, judge_client)
        if judged is not None:
            pairs.append((judged, w.judge))

    scores = []
    for gs, weight in pairs:
        gs.weight = 0.0 if gs.excluded else weight
        scores.append(gs)

    counted = [(gs, wt) for gs, wt in pairs if not gs.excluded]
    total_w = sum(weight for _, weight in counted) or 1.0
    composite = sum(gs.score * weight for gs, weight in counted) / total_w

    # path_score = everything except whether the final answer was right.
    path_pairs = [(gs, wt) for gs, wt in counted
                  if gs.name not in ("outcome", "judge")]
    path_w = sum(wt for _, wt in path_pairs) or 1.0
    path_score = sum(gs.score * wt for gs, wt in path_pairs) / path_w

    lucky = outcome.score >= 1.0 and path_score < cfg.lucky_pass_path_threshold
    # A lucky pass is not a pass. The composite can still clear the bar when a
    # correct answer carries a bad path - adding the weight of the right
    # answer to a wandering route is exactly the arithmetic that lets a broken
    # agent look fine - so the flag overrides the number.
    passed = (composite >= golden.pass_threshold and not hard_fail
              and not lucky)

    notes: list[str] = []
    if judged is not None and judged.excluded:
        notes.append(f"JUDGE EXCLUDED: {judged.detail} - the composite was "
                     f"computed without it, not with a free pass")
    if len(golden.path_variants()) > 1:
        notes.append(f"matched the '{matched_path}' route of "
                     f"{len(golden.path_variants())} acceptable routes")
    if lucky:
        notes.append(
            f"LUCKY PASS: the answer is right but the path scored "
            f"{path_score:.2f} (< {cfg.lucky_pass_path_threshold}). "
            f"This is the case plain output testing hides."
        )
    if hard_fail:
        notes.append("HARD FAIL: a forbidden tool or argument rule was broken.")
    for gs in scores:
        notes.extend(gs.violations)

    return TaskResult(
        task_id=golden.task_id,
        agent_version=traj.agent_version,
        composite=round(composite, 4),
        path_score=round(path_score, 4),
        outcome=outcome.score,
        passed=passed,
        hard_fail=hard_fail,
        lucky_pass=lucky,
        steps=len(traj.steps),
        tool_calls=len(traj.calls()),
        tokens=traj.tokens(),
        latency_ms=traj.latency_ms(),
        usd_cost=round(traj.usd_cost, 6),
        scores=scores,
        notes=notes,
        matched_path=matched_path,
    )
