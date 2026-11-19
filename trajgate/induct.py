"""
Writing the first draft of a golden for you, from runs you already have.

The honest limitation of trajectory eval is that somebody has to write the
goldens. This does not remove that work, it removes the boring nine tenths of
it: point it at a few runs you were happy with and it infers the path, the
step budget, the retry allowance, the facts the answer always contains, and -
the useful part - which arguments were GROUNDED in earlier tool output.

What it will never infer is intent. "This tool was never called" is not the
same as "this tool must never be called", and only a human knows that
check_refund_eligibility is a safety gate rather than one step among four. So
those are emitted as commented-out suggestions for you to accept or delete.

Read the draft. It is a starting point, not a specification.
"""

from __future__ import annotations

import re
import statistics
from collections import Counter
from typing import Any, Iterable

from .schema import Golden, StepKind, Trajectory

# Things worth asserting on if every answer contains them: ids, money,
# percentages, dates. Deliberately narrow - a word that happens to appear in
# three answers is not a fact about the task.
ID_PATTERN = re.compile(r"\b[A-Z]{1,4}[-_]?\d{3,}\b")
MONEY_PATTERN = re.compile(r"\$\s?\d[\d,]*(?:\.\d{2})?")


class InductionReport:
    """What was inferred, and what a human still has to decide."""

    def __init__(self) -> None:
        self.inferred: list[str] = []
        self.needs_review: list[str] = []
        self.suggestions: list[str] = []


def _call_names(t: Trajectory) -> list[str]:
    return t.call_names()


def _first_order(names: list[str], of: Iterable[str]) -> list[str]:
    seen: list[str] = []
    for n in names:
        if n in of and n not in seen:
            seen.append(n)
    return seen


def find_paths(output: Any, value: Any, prefix: str = "") -> list[str]:
    """Dotted paths inside a tool output where `value` appears.

    A list containing the value counts: get_customer returning
    {"order_ids": ["A1001"]} grounds get_order(order_id="A1001").
    """
    found: list[str] = []
    if _same(output, value):
        found.append(prefix)
    if isinstance(output, dict):
        for k, v in output.items():
            found += find_paths(v, value, f"{prefix}.{k}" if prefix else str(k))
    elif isinstance(output, (list, tuple)):
        if any(_same(v, value) for v in output):
            found.append(prefix)
    return [p for p in found if p != ""]


def _same(a: Any, b: Any) -> bool:
    if isinstance(a, (dict, list, tuple)) or isinstance(b, (dict, list, tuple)):
        return False
    try:
        return abs(float(a) - float(b)) < 1e-9
    except (TypeError, ValueError):
        pass
    return str(a).strip().lower() == str(b).strip().lower()


def _grounded_args(t: Trajectory) -> dict[tuple[str, str], set[str]]:
    """For each (tool, arg), the earlier-output paths its value came from."""
    out: dict[tuple[str, str], set[str]] = {}
    for call in t.calls():
        earlier = [s for s in t.steps
                   if s.kind == StepKind.TOOL_RESULT and s.ok
                   and s.index < call.index]
        for arg, value in call.args.items():
            paths: set[str] = set()
            for src in earlier:
                for p in find_paths(src.output, value):
                    paths.add(f"{src.tool}.{p}")
            if paths:
                out.setdefault((call.tool or "", arg), set()).update(paths)
    return out


def _common_answer_facts(answers: list[str]) -> list[str]:
    """Identifiers and amounts that appear in EVERY answer."""
    if not answers:
        return []
    sets = []
    for a in answers:
        sets.append(set(ID_PATTERN.findall(a)) |
                    {m.replace(" ", "") for m in MONEY_PATTERN.findall(a)})
    common = set.intersection(*sets) if sets else set()
    return sorted(common)


def induct(task_id: str, runs: list[Trajectory], prompt: str | None = None,
           tool_catalogue: list[str] | None = None
           ) -> tuple[Golden, InductionReport]:
    """Infer a draft golden from runs you consider good."""
    if not runs:
        raise ValueError(f"no trajectories for task {task_id!r}")
    rep = InductionReport()

    per_run = [_call_names(t) for t in runs]
    all_tools = {n for names in per_run for n in names}
    always = sorted(t for t in all_tools
                    if all(t in names for names in per_run))
    sometimes = sorted(all_tools - set(always))

    # Order: only claim it matters if every run agrees on the sequence.
    orders = {tuple(_first_order(names, always)) for names in per_run}
    consistent = len(orders) == 1
    required = list(next(iter(orders))) if consistent else always

    counts = [len(names) for names in per_run]
    ideal = int(statistics.median(counts))
    rep.inferred.append(
        f"{len(runs)} run(s): {len(always)} tool(s) called every time, "
        f"{len(sometimes)} sometimes")
    if consistent:
        rep.inferred.append(f"all runs used the same order: "
                            f"{' -> '.join(required) or '(none)'}")
    else:
        rep.inferred.append("runs disagreed on order, so order_matters is "
                            "false - check whether it should be")
        rep.needs_review.append(
            "order_matters was set to false because the sample disagreed. If "
            "the sequence does matter, set it true and list the right order.")

    # Retries: the most any single tool was called in one run.
    busiest = max((Counter(names).most_common(1)[0][1] for names in per_run
                   if names), default=1)
    retries = max(2, busiest - 1)

    facts = _common_answer_facts([t.final_answer or "" for t in runs])
    if facts:
        rep.inferred.append(f"every answer contained: {', '.join(facts)}")
    else:
        rep.needs_review.append(
            "no identifier or amount appeared in every answer, so `expect` is "
            "empty - add what the answer must say, or the outcome grader "
            "cannot fail anything.")

    # Grounded arguments: the genuinely useful inference.
    grounded_per_run = [_grounded_args(t) for t in runs]
    keys = set(grounded_per_run[0]) if grounded_per_run else set()
    for g in grounded_per_run[1:]:
        keys &= set(g)
    arg_quality = []
    for tool, arg in sorted(keys):
        paths: set[str] = set.intersection(
            *[g[(tool, arg)] for g in grounded_per_run])
        if not paths:
            continue
        path = sorted(paths, key=lambda p: (p.count("."), len(p)))[0]
        arg_quality.append({"tool": tool, "arg": arg, "from_tool_output": path})
        rep.inferred.append(
            f"{tool}.{arg} was taken from {path} in every run - asserting it "
            f"stays that way catches an invented value")

    golden = Golden(
        task_id=task_id,
        prompt=prompt or "TODO: the user's message for this task",
        required_tools=required,
        optional_tools=sometimes,
        order_matters=consistent and len(required) > 1,
        ideal_steps=ideal,
        max_steps=max(counts) + 1,
        max_retries_per_tool=retries,
    )
    golden.expect.contains_all = facts

    # Suggestions a human has to accept, not observations.
    never = sorted(set(tool_catalogue or []) - all_tools)
    if never:
        rep.suggestions.append(
            "tools never called in these runs - forbid the ones that would be "
            "wrong, delete the rest: " + ", ".join(never))
    if consistent and len(required) >= 2:
        rep.suggestions.append(
            "if any step here is a check that must happen before the action "
            "after it, add it to critical_tools and must_precede - this is "
            "the difference between a 0.03 score dip and a blocked merge")
    rep.needs_review.append(
        "nothing here encodes intent. Observation cannot tell a safety gate "
        "from an ordinary step, so critical_tools, forbidden_tools and "
        "arg_rules are left to you.")
    return golden, (rep, arg_quality)  # type: ignore[return-value]


def to_yaml(golden: Golden, bundle, agent_version: str = "unknown",
            run_count: int = 0) -> str:
    """A commented YAML draft - deliberately readable, not yaml.dump output."""
    rep, arg_quality = bundle
    L: list[str] = []
    L.append("# ---------------------------------------------------------------")
    L.append("# DRAFT golden, generated by `trajgate induct`. REVIEW IT.")
    L.append(f"# Inferred from {run_count} run(s) of agent {agent_version}.")
    L.append("#")
    L.append("# What was inferred from the runs:")
    for line in rep.inferred:
        L.append(f"#   - {line}")
    L.append("#")
    L.append("# What still needs a human:")
    for line in rep.needs_review:
        L.append(f"#   ! {line}")
    L.append("# ---------------------------------------------------------------")
    L.append(f"task_id: {golden.task_id}")
    L.append(f"description: >")
    L.append(f"  TODO: say what this task is testing and why it matters.")
    L.append(f"prompt: {_q(golden.prompt)}")
    L.append("")
    L.append("required_tools:" if golden.required_tools else "required_tools: []")
    for t in golden.required_tools:
        L.append(f"  - {t}")
    if golden.optional_tools:
        L.append("# called in some runs but not all - required would be wrong")
        L.append("optional_tools:")
        for t in golden.optional_tools:
            L.append(f"  - {t}")
    else:
        L.append("optional_tools: []")
    L.append("forbidden_tools: []   # intent, not observation - see the notes above")
    L.append("critical_tools: []    # the checks you would never let anyone skip")
    L.append("must_precede: []      # e.g. [[check_eligibility, issue_refund]]")
    L.append(f"order_matters: {str(golden.order_matters).lower()}")
    L.append(f"ideal_steps: {golden.ideal_steps}")
    L.append(f"max_steps: {golden.max_steps}")
    L.append(f"max_retries_per_tool: {golden.max_retries_per_tool}")
    L.append("")
    if arg_quality:
        L.append("# Values the agent RETRIEVED rather than invented. These are")
        L.append("# observations, so they are enabled.")
        L.append("arg_quality:")
        for spec in arg_quality:
            L.append(f"  - tool: {spec['tool']}")
            L.append(f"    arg: {spec['arg']}")
            L.append(f"    from_tool_output: {spec['from_tool_output']}")
        L.append("")
    L.append("expect:")
    if golden.expect.contains_all:
        L.append("  contains_all:")
        for f in golden.expect.contains_all:
            L.append(f"    - {_q(f)}")
    else:
        L.append("  contains_all: []   # TODO: what must the answer say?")
    L.append("  contains_any: []     # TODO: acceptable phrasings")
    L.append("  not_contains: []     # TODO: what must it never say?")
    L.append("")
    L.append("# judge:               # optional LLM judge, for qualities with")
    L.append("#   rubric: >          # no keyword to assert on")
    L.append("#     TODO")
    L.append("")
    L.append(f"pass_threshold: {golden.pass_threshold}")
    L.append(f"weight: {golden.weight}")
    if rep.suggestions:
        L.append("")
        L.append("# Suggestions, for you to accept or delete:")
        for sug in rep.suggestions:
            L.append(f"#   ? {sug}")
    return "\n".join(L) + "\n"


def _q(text: str) -> str:
    text = str(text).replace('"', '\\"')
    return f'"{text}"'
