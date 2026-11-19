"""Turns results into something a human reads: terminal text, markdown
(for the GitHub pull-request summary) and a standalone HTML page."""

from __future__ import annotations

import html
from pathlib import Path

from .gate import GateDecision
from .schema import SuiteResult

GRADER_ORDER = ["outcome", "tool_selection", "order", "efficiency",
                "redundancy", "recovery", "safety"]
SHORT = {"outcome": "answer", "tool_selection": "tools", "order": "order",
         "efficiency": "effic", "redundancy": "redund", "recovery": "recov",
         "safety": "safety"}


def _flags(t) -> str:
    f = []
    if t.hard_fail:
        f.append("HARD-FAIL")
    if t.lucky_pass:
        f.append("LUCKY-PASS")
    if not t.passed and not t.hard_fail:
        f.append("FAIL")
    return " ".join(f) or "ok"


def terminal(suite: SuiteResult, decision: GateDecision) -> str:
    lines: list[str] = []
    lines.append("")
    lines.append(f"Trajectory eval - agent {suite.agent_version}")
    lines.append("=" * 72)
    head = f"{'task':<24}{'score':>7}{'delta':>8}{'calls':>7}  flags"
    lines.append(head)
    lines.append("-" * 72)
    dmap = {d.task_id: d for d in decision.deltas}
    for t in suite.tasks:
        d = dmap.get(t.task_id)
        delta = f"{d.delta:+.3f}" if d and d.baseline is not None else "   new"
        lines.append(f"{t.task_id:<24}{t.composite:>7.3f}{delta:>8}"
                     f"{t.tool_calls:>7}  {_flags(t)}")
    lines.append("-" * 72)
    base = (f" (baseline {decision.mean_baseline:.3f}, "
            f"{decision.mean_delta:+.3f})" if decision.mean_baseline is not None else "")
    lines.append(f"mean score {suite.mean_composite:.3f}{base}   "
                 f"pass rate {suite.pass_rate*100:.0f}%")
    lines.append("")
    if decision.blocked:
        lines.append("GATE: BLOCKED - this change would not be merged")
        for r in decision.reasons:
            lines.append(f"  x {r}")
    else:
        lines.append("GATE: PASSED")
    for w in decision.warnings:
        lines.append(f"  ! {w}")
    lines.append("")
    return "\n".join(lines)


def markdown(suite: SuiteResult, decision: GateDecision) -> str:
    verdict = ("### :no_entry: Trajectory gate **BLOCKED**"
               if decision.blocked else "### :white_check_mark: Trajectory gate **passed**")
    out = [verdict, ""]
    base = (f" | baseline `{decision.mean_baseline:.3f}` "
            f"(**{decision.mean_delta:+.3f}**)" if decision.mean_baseline is not None else
            " | _no baseline committed yet_")
    out.append(f"Agent `{suite.agent_version}` | mean score "
               f"`{suite.mean_composite:.3f}`{base} | pass rate "
               f"`{suite.pass_rate*100:.0f}%`")
    out.append("")

    if decision.blocked:
        out.append("**Why it is blocked**")
        out += [f"- {r}" for r in decision.reasons]
        out.append("")

    cols = ["task", "score", "Δ"] + [SHORT[g] for g in GRADER_ORDER] + ["calls", "flags"]
    out.append("| " + " | ".join(cols) + " |")
    out.append("|" + "|".join(["---"] * len(cols)) + "|")
    dmap = {d.task_id: d for d in decision.deltas}
    for t in suite.tasks:
        d = dmap.get(t.task_id)
        delta = f"{d.delta:+.3f}" if d and d.baseline is not None else "new"
        sm = t.score_map()
        row = [f"`{t.task_id}`", f"**{t.composite:.3f}**", delta]
        row += [f"{sm.get(g, 0):.2f}" for g in GRADER_ORDER]
        row += [str(t.tool_calls), _flags(t)]
        out.append("| " + " | ".join(row) + " |")
    out.append("")

    problems = [t for t in suite.tasks if not t.passed or t.lucky_pass or t.hard_fail]
    if problems:
        out.append("<details><summary>What went wrong, step by step</summary>")
        out.append("")
        for t in problems:
            out.append(f"**`{t.task_id}`** - score {t.composite:.3f} "
                       f"(path {t.path_score:.3f}, answer "
                       f"{'right' if t.outcome else 'wrong'})")
            for n in t.notes:
                out.append(f"  - {n}")
            for s in t.scores:
                if s.score < 1.0:
                    out.append(f"  - `{s.name}` {s.score:.2f} - {s.detail}")
            out.append("")
        out.append("</details>")
        out.append("")

    if decision.warnings:
        out.append("<details><summary>Notes</summary>")
        out.append("")
        out += [f"- {w}" for w in decision.warnings]
        out.append("")
        out.append("</details>")
    return "\n".join(out)


def _bar(v: float) -> str:
    colour = "#16a34a" if v >= 0.8 else "#d97706" if v >= 0.5 else "#dc2626"
    return (f'<div class="bar"><span style="width:{v*100:.0f}%;'
            f'background:{colour}"></span></div>')


def html_report(suite: SuiteResult, decision: GateDecision) -> str:
    rows = []
    dmap = {d.task_id: d for d in decision.deltas}
    for t in suite.tasks:
        d = dmap.get(t.task_id)
        delta = f"{d.delta:+.3f}" if d and d.baseline is not None else "new"
        dcls = "neg" if (d and d.delta < 0) else "pos" if (d and d.delta > 0) else ""
        sm = t.score_map()
        cells = "".join(
            f'<td class="num">{sm.get(g,0):.2f}{_bar(sm.get(g,0))}</td>'
            for g in GRADER_ORDER)
        notes = "<br>".join(html.escape(n) for n in t.notes) or "-"
        rows.append(
            f"<tr><td><code>{html.escape(t.task_id)}</code></td>"
            f'<td class="num big">{t.composite:.3f}</td>'
            f'<td class="num {dcls}">{delta}</td>{cells}'
            f'<td class="num">{t.tool_calls}</td>'
            f'<td><span class="flag {"bad" if (t.hard_fail or not t.passed) else "warn" if t.lucky_pass else "good"}">'
            f'{_flags(t)}</span></td>'
            f'<td class="notes">{notes}</td></tr>')

    reasons = "".join(f"<li>{html.escape(r)}</li>" for r in decision.reasons)
    warns = "".join(f"<li>{html.escape(w)}</li>" for w in decision.warnings)
    heads = "".join(f"<th>{SHORT[g]}</th>" for g in GRADER_ORDER)
    verdict = "BLOCKED" if decision.blocked else "PASSED"
    vclass = "blocked" if decision.blocked else "passed"
    baseline_txt = (f"baseline {decision.mean_baseline:.3f} "
                    f"({decision.mean_delta:+.3f})"
                    if decision.mean_baseline is not None else "no baseline yet")

    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Trajectory eval - {html.escape(suite.agent_version)}</title>
<style>
 :root{{color-scheme:light dark}}
 body{{font:14px/1.5 ui-sans-serif,system-ui,-apple-system,sans-serif;
      margin:0;padding:32px;background:#0b0d10;color:#e6e8eb}}
 h1{{font-size:20px;margin:0 0 4px}}
 .sub{{color:#9aa4af;margin-bottom:20px}}
 .verdict{{display:inline-block;padding:6px 14px;border-radius:6px;
      font-weight:700;letter-spacing:.04em;margin-bottom:18px}}
 .passed{{background:#0f2e1b;color:#4ade80;border:1px solid #1e5633}}
 .blocked{{background:#2e0f14;color:#f87171;border:1px solid #5b1d24}}
 table{{border-collapse:collapse;width:100%;font-size:13px}}
 th,td{{padding:8px 10px;border-bottom:1px solid #1e2430;text-align:left;
      vertical-align:top}}
 th{{color:#9aa4af;font-weight:600;font-size:11px;text-transform:uppercase;
      letter-spacing:.05em}}
 td.num{{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}}
 td.big{{font-weight:700;font-size:15px}}
 .neg{{color:#f87171}} .pos{{color:#4ade80}}
 .bar{{height:3px;background:#1e2430;border-radius:2px;margin-top:4px}}
 .bar span{{display:block;height:3px;border-radius:2px}}
 .flag{{font-size:11px;padding:2px 6px;border-radius:4px;white-space:nowrap}}
 .flag.good{{background:#0f2e1b;color:#4ade80}}
 .flag.warn{{background:#33280c;color:#fbbf24}}
 .flag.bad{{background:#2e0f14;color:#f87171}}
 .notes{{color:#9aa4af;font-size:12px;max-width:380px}}
 ul{{margin:0 0 18px 18px;padding:0}} li{{margin:3px 0}}
 code{{font-family:ui-monospace,SFMono-Regular,Menlo,monospace}}
</style></head><body>
<h1>Trajectory eval &mdash; <code>{html.escape(suite.agent_version)}</code></h1>
<div class="sub">{suite.created_at} &middot; mean score
 <b>{suite.mean_composite:.3f}</b> &middot; {baseline_txt} &middot;
 pass rate {suite.pass_rate*100:.0f}%</div>
<div class="verdict {vclass}">GATE {verdict}</div>
{f"<ul>{reasons}</ul>" if reasons else ""}
<table><thead><tr><th>task</th><th>score</th><th>&Delta;</th>{heads}
<th>calls</th><th>flags</th><th>notes</th></tr></thead>
<tbody>{"".join(rows)}</tbody></table>
{f"<h1 style='font-size:14px;margin-top:24px'>Notes</h1><ul>{warns}</ul>" if warns else ""}
</body></html>"""


def write(path: str | Path, text: str) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    return p
