# 06 — The Python you need

Every Python feature this repo uses, taught from the repo's own code. If you
have been building with AI help and can follow code but not quite write it,
this is the doc that closes that gap.

Work through it with `python3` open in another terminal. Type the examples.

---

## 1. Type hints

```python
def cost_usd(tokens_in: int, tokens_out: int, model: str | None,
             pricing: PricingConfig | None = None) -> float:
```

- `tokens_in: int` — this parameter should be a whole number.
- `model: str | None` — a string, or nothing. The `|` means "or".
- `= None` — has a default, so it can be omitted.
- `-> float` — returns a decimal number.

**Python does not enforce these.** Passing a string where an `int` is hinted
runs happily until something breaks. They exist for three other reasons: your
editor can autocomplete and warn, a reader knows the shape without reading
the body, and pydantic (below) *does* enforce them.

Collections:

```python
list[Step]                 # a list of Step objects
dict[str, float]           # keys are strings, values are decimals
tuple[GraderScore, bool]   # exactly two items, in that order
set[tuple[str, str]]       # a set of two-string pairs
list[str] | None           # a list of strings, or nothing
Any                        # anything; used when a tool's output could be
                           # a dict, a list, a string or a number
```

One gotcha you will hit: `str | None` and `list[str]` are only valid as
*runtime* syntax in Python 3.10+. Older code writes `Optional[str]` and
`List[str]` from the `typing` module. You will also see this at the top of
most files here:

```python
from __future__ import annotations
```

That tells Python to treat all annotations as text and not evaluate them,
which makes the modern syntax safe even on older versions and avoids
problems with classes that refer to themselves.

---

## 2. Pydantic: classes that validate themselves

The single most useful library in this repo.

```python
from pydantic import BaseModel, Field

class ModelPrice(BaseModel):
    usd_per_m_in: float
    usd_per_m_out: float
    note: str = ""
```

```python
>>> ModelPrice(usd_per_m_in=3.0, usd_per_m_out=15.0)
ModelPrice(usd_per_m_in=3.0, usd_per_m_out=15.0, note='')

>>> ModelPrice(usd_per_m_in="three", usd_per_m_out=15.0)
ValidationError: Input should be a valid number [type=float_parsing]

>>> ModelPrice(usd_per_m_in="3.0", usd_per_m_out=15)      # sensible coercion
ModelPrice(usd_per_m_in=3.0, usd_per_m_out=15.0, note='')
```

What you get for free:

| Need | How |
|---|---|
| Validate on creation | automatic from the hints |
| Read a YAML file into objects | `Golden.model_validate(yaml.safe_load(text))` |
| Read JSON | `Trajectory.model_validate_json(line)` |
| Write JSON | `json.dumps(suite.model_dump())` |
| Default to a fresh dict/list | `Field(default_factory=dict)` |
| A copy to modify safely | `variant.model_copy()` |

**Why `Field(default_factory=dict)` and not `= {}`:** a default value is
created once, when the function is defined, and then shared by every
instance. With `args: dict = {}`, adding to one `Step`'s args would appear in
every other `Step`. `default_factory` calls `dict()` fresh each time. This is
one of the top three Python bugs and it bites everybody once.

Where this repo uses pydantic to real effect: a malformed golden fails at
load time with a message naming the field, rather than producing a
mysterious score an hour later.

---

## 3. Enums: a fixed set of choices

```python
class StepKind(str, Enum):
    THOUGHT = "thought"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    MESSAGE = "message"
    FINAL = "final"
```

Now `StepKind.TOOL_CALL` is the only way to say "tool call". Typing
`StepKind.TOOLCALL` is an immediate `AttributeError`, whereas the string
`"toolcall"` would just silently never match.

Inheriting from `str` as well as `Enum` means it behaves like a string when
written to JSON, which saves conversion code at the edges.

---

## 4. List and dict comprehensions

```python
[s for s in self.steps if s.is_call]
```

Read right to left: for every `s` in `self.steps`, keep it if `s.is_call`,
collect into a list. The long form:

```python
result = []
for s in self.steps:
    if s.is_call:
        result.append(s)
```

Variations in this repo:

```python
[s.tool for s in self.calls() if s.tool]          # transform + filter
{t.task_id: t for t in self.tasks}                 # dict comprehension
{s.name for t in suite.tasks for s in t.scores}    # set, two loops
sum(1 for t in actual if t in allowed)             # count without a list
```

That last one is a **generator expression**: no list is built, the values are
produced one at a time and added up. For counting, prefer it.

A rule of thumb: one `for` and one `if` is clearer as a comprehension; two
`for`s and two `if`s is clearer as a loop.

---

## 5. f-strings, including the formatting bits

```python
f"score dropped {abs(d.delta):.3f}, more than the allowed {th.max_task_score_drop}"
```

`f"..."` lets you put expressions in `{}`. The part after `:` is formatting:

```python
f"{0.7515:.3f}"      # '0.751'   three decimal places
f"{0.151:+.3f}"      # '+0.151'  always show the sign
f"{84.2:.0f}%"       # '84%'     no decimals
f"{1234.5:,.2f}"     # '1,234.50' thousands separator
f"{'task':<24}"      # left-align in 24 characters
f"{t.composite:>7.3f}"  # right-align in 7, three decimals
f"{value!r}"         # repr(), so strings show their quotes
```

The `<` and `>` alignment forms are how `report.py` draws a table with no
library. `!r` matters in error messages: `query='{{query}}'` is unambiguous
where `query={{query}}` is not.

---

## 6. Functions, defaults and `**kwargs`

```python
def run_task(task_id: str, version: str = "v1.0-good",
             run_id: str = "run-1") -> Trajectory:
```

Called as `run_task("refund_happy")` or
`run_task("refund_happy", version="v1.1-regressed")`. Naming arguments at the
call site is worth the extra typing — `run_task(t, v, r)` is unreadable six
months later.

```python
backend.call(req.name, **req.args)
```

`**` unpacks a dict into keyword arguments. If `req.args` is
`{"order_id": "A1001"}` this becomes `backend.call("get_order", order_id="A1001")`.
It is how the loop can call any tool without knowing its signature — and it
is why a wrong argument name raises `TypeError`, which the loop catches and
reports back to the model.

```python
def call(name: str, *args, **kwargs) -> ToolRequest:
```

`*args` collects extra positional arguments into a tuple, `**kwargs` collects
extra keyword arguments into a dict. Used in test helpers that must accept
anything.

---

## 7. Dataclasses: pydantic's lightweight cousin

```python
from dataclasses import dataclass, field

@dataclass
class ModelReply:
    text: str = ""
    tool_requests: list[ToolRequest] = field(default_factory=list)
    tokens_in: int = 0
```

A `@decorator` is a function that modifies the thing below it. `@dataclass`
writes `__init__`, `__repr__` and `__eq__` for you from the annotations.

Dataclass or pydantic model? Dataclasses are standard library and fast but do
**not** validate. This repo uses pydantic where data crosses a boundary
(files, JSON, YAML — where bad data actually arrives) and dataclasses for
internal shapes that never leave the process.

---

## 8. Properties: a method that looks like an attribute

```python
    @property
    def exit_code(self) -> int:
        return 1 if self.blocked else 0
```

Written `decision.exit_code`, with no brackets. Use it for something cheap
and derived; if it does real work, a normal method is honest about that.

Also note `1 if self.blocked else 0` — Python's conditional expression, the
same idea as `cond ? a : b` in other languages.

---

## 9. Protocols: duck typing you can check

```python
from typing import Protocol

class JudgeClient(Protocol):
    def __call__(self, prompt: str) -> str: ...
```

This says: *anything callable with a string that returns a string counts as a
JudgeClient.* No inheritance needed. `AnthropicJudge`, `OpenAIJudge`,
`CachedJudge` and `ScriptedJudge` all satisfy it without any of them
mentioning it.

This is what makes the judge and the model client swappable, and it is why
the test suite can run the real agent loop with no API key: `ScriptedClient`
satisfies the same protocol as `AnthropicClient`.

`...` is `Ellipsis`, used as "no body here".

---

## 10. Exceptions, and the two patterns here

Defining one:

```python
class ToolError(Exception):
    pass

class UnknownModel(KeyError):
    pass
```

Raising and catching:

```python
try:
    raw = backend.call(req.name, **req.args)
    out, ok = _short(raw), True
except ToolError as e:
    out, ok = f"error: {e}", False
except TypeError as e:
    out, ok = f"error: bad arguments for {req.name}: {e}", False
```

Two patterns, used deliberately differently:

**Convert to data** (the loop above). A tool failing is not a bug, it is an
observation the agent must handle — so it becomes a recorded step, not a
crash.

**Let it fly** (`cost_usd` raising `UnknownModel`). An unpriced model means
the cost gate cannot do its job, and silence there would be worse than a
crash.

One thing to avoid, which the repo does in exactly one place on purpose:

```python
        except Exception:
            pass  # a cache that cannot be written is a slow judge,
                  # not a broken one
```

Bare `except Exception: pass` hides bugs. It is defensible when the failure
is genuinely irrelevant to correctness, and then it deserves the comment
explaining why.

---

## 11. `pathlib` instead of string paths

```python
from pathlib import Path

p = Path("goldens")
files = sorted(p.glob("*.yaml"))          # find files by pattern
text = Path("config.yaml").read_text()     # read a whole file
(out_dir / "report.md").write_text(md)     # join with /, then write
p.parent.mkdir(parents=True, exist_ok=True)  # make folders safely
ROOT = Path(__file__).resolve().parents[1]   # the repo root, from a file
```

`/` joins paths and works on every operating system. `read_text()` and
`write_text()` handle opening and closing for you. `exist_ok=True` means "do
not complain if it already exists".

That `ROOT` line appears in every test file: `__file__` is the current file's
path, `.resolve()` makes it absolute, `.parents[1]` goes up two levels.

---

## 12. JSON and YAML

```python
import json, yaml

json.dumps(obj, indent=2, sort_keys=True, default=str)
json.loads(text)
yaml.safe_load(Path("config.yaml").read_text())
```

- `indent=2` pretty-prints, which matters when the output goes into git and
  you want a readable diff.
- `sort_keys=True` makes the output stable, so the same data always produces
  the same bytes. `_canon_args` relies on this.
- `default=str` tells `json.dumps` to stringify anything it cannot serialise,
  instead of raising.
- Always `yaml.safe_load`, never `yaml.load`. Plain `load` can construct
  arbitrary Python objects from a file, which is a security hole.

**JSONL** is "JSON lines": one object per line, no enclosing array. It
streams, it appends, and you can `grep` it. The trace format here.

---

## 13. `argparse`: building a CLI

```python
p = argparse.ArgumentParser(prog="trajgate", description="...")
sub = p.add_subparsers(dest="cmd", required=True)

r = sub.add_parser("run", help="run the suite, grade it, apply the gate")
r.add_argument("--agent", default="v1.0-good")
r.add_argument("--repeats", type=int, default=None)
r.add_argument("--no-gate", action="store_true")
r.set_defaults(fn=cmd_run)

args = p.parse_args(argv)
return args.fn(args)
```

- **Subparsers** give you `trajgate run`, `trajgate gate`, `trajgate induct`.
- **`type=int`** converts and validates.
- **`action="store_true"`** makes a flag: present means `True`.
- **`set_defaults(fn=...)`** attaches the handler to the parsed arguments, so
  the last two lines dispatch any command without a chain of `if`s.
- `--no-gate` becomes `args.no_gate`; argparse turns dashes into underscores.

You get `--help` for free, which is most of why to use it.

---

## 14. `__main__.py` and `python -m`

```python
# trajgate/__main__.py
import sys
from .cli import main
sys.exit(main())
```

`python -m trajgate` makes Python find the package and run this file.
`sys.exit(n)` ends the process with exit code `n` — and that integer is what
CI reads to decide whether to block the merge. The whole gate hangs on this
line.

`from .cli import main` is a **relative import**: the dot means "this
package". It keeps working if the package is renamed.

---

## 15. pytest

```python
def test_order_right_and_wrong():
    g = Golden(task_id="t", prompt="p", required_tools=["a", "b", "c"])
    assert grade_order(traj([("a", {}), ("b", {}), ("c", {})]), g).score == 1.0
```

Any function named `test_*` in a file named `test_*.py` is a test. Plain
`assert`. Run them all with `pytest -q`.

Four features this repo leans on:

```python
# 1. floating-point comparison
assert res.usd_cost == pytest.approx(0.006)

# 2. assert that something raises
with pytest.raises(UnknownModel):
    cost_usd(1000, 100, "unpriced-model", pricing())

# 3. the same test over many inputs
@pytest.mark.parametrize("value", ["{{query}}", "<order_id>", "TODO", ""])
def test_placeholders_are_recognised(value):
    assert looks_like_placeholder(value) is True

# 4. a temporary directory, cleaned up for you
def test_cache_persists_across_runs(tmp_path):
    path = tmp_path / "cache.json"
```

`pytest.approx` exists because `0.1 + 0.2 != 0.3` in binary floating point.
Any test comparing computed decimals needs it.

Why `== pytest.approx(...)` and not `round(x, 4) == y`: the first expresses
"close enough", the second hides how close.

---

## 16. Small things you will meet in this code

```python
from collections import Counter
Counter(["a", "b", "a"]).most_common(1)     # [('a', 2)]

import statistics
statistics.median([1.0, 0.88, 0.88])         # 0.88

value if cond else other                      # conditional expression
x = a or 1.0                                  # fall back when a is falsy/zero
_, matched, sel, ordr, eff = best             # tuple unpacking, _ = discard
for t, arg in sorted(keys):                   # unpack while looping
src, _, path = text.partition(".")            # split on the FIRST dot only
set(a) | set(b)                               # union
set(a) - set(b)                               # difference
sorted(set(x))                                # deduplicate and order
isinstance(v, (dict, list, tuple))            # type check against several
getattr(t, "flaky", False)                    # read an attribute that may
                                              # not exist, with a fallback
abs(float(a) - float(b)) < 1e-9               # compare decimals safely
"".join(parts)                                # build a string from a list
text[:limit] + "...(truncated)"               # slice
names[:i_after]                               # everything before an index
```

Two worth dwelling on:

**`str.partition(".")`** returns three pieces and splits on the *first*
occurrence, which is why `"get_customer.order_ids.0"` becomes
`("get_customer", ".", "order_ids.0")` — exactly what a dotted path needs.

**`for ... else`** — the `else` runs only when the loop was not `break`ed out
of. In `loop.py` it detects "the model never finished on its own", which is
the step-limit case. Rare, genuinely useful, and worth recognising.

---

## 17. What to practise, in order

1. Write a pydantic model for something you know (a book, an order). Give it
   a bad value and read the error.
2. Rewrite three comprehensions from `graders.py` as explicit loops, then
   back again.
3. Add a `--limit N` option to the `run` command with argparse.
4. Write one failing test, then make it pass.
5. Read `real_agent/loop.py` top to bottom and write down, in your own words,
   what each line does. If you can do that, you can read this whole repo.

Then go to doc [08](08-extend-it.md), which is nine exercises against this
codebase, easiest first.
