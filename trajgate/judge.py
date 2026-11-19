"""
The optional LLM judge.

Why optional, and off by default: a grader that blocks merges should be free,
instant and reproducible, and a model is none of those. But some answers
cannot be checked with string assertions - "was the tone right for a customer
who has been charged twice?" has no keyword.

So the judge is here, with three guard rails:

  1. OFF unless you turn it on, and weight 0.0 unless you give it weight.
  2. CACHED on disk by a hash of (rubric + answer + path), so a re-run of the
     same pull request costs nothing and returns the same number.
  3. HONEST when it breaks: if the model is unreachable or returns nonsense,
     the judge is EXCLUDED from the score rather than scored as 1.0. A grader
     that silently passes when it fails is worse than no grader.

Any callable that takes a prompt string and returns a response string works as
a judge client, so you can point this at whatever you already pay for.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable, Protocol

from .schema import Golden, GraderScore, Trajectory

JUDGE_PROMPT = """You are grading one run of a customer-support AI agent.

Grade ONLY against the rubric below. Be strict and literal: if the rubric is \
not met, say so. Do not reward fluency, politeness or length on their own.

RUBRIC
{rubric}
{criteria}

THE USER ASKED
{prompt}

THE AGENT'S PATH (the tools it called, in order)
{path}

THE AGENT'S FINAL ANSWER
{answer}

Reply with JSON only, no other text, in exactly this shape:
{{"score": <number between 0 and 1>, "reason": "<one sentence>"}}"""


class JudgeClient(Protocol):
    def __call__(self, prompt: str) -> str: ...


# ---------------------------------------------------------------------------
# Clients
# ---------------------------------------------------------------------------

class ScriptedJudge:
    """A fake judge for tests: returns canned replies, counts its calls."""

    def __init__(self, replies: list[str] | str):
        self.replies = [replies] if isinstance(replies, str) else list(replies)
        self.calls: list[str] = []

    def __call__(self, prompt: str) -> str:
        self.calls.append(prompt)
        if not self.replies:
            raise RuntimeError("ScriptedJudge ran out of replies")
        return self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]


class AnthropicJudge:
    """Minimal Anthropic Messages API client - no SDK dependency.

    Temperature is pinned to 0. Needs ANTHROPIC_API_KEY in the environment.
    """

    def __init__(self, model: str = "claude-sonnet-4-5", max_tokens: int = 300,
                 api_key: str | None = None, timeout: int = 60):
        self.model = model
        self.max_tokens = max_tokens
        self.timeout = timeout
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")

    def __call__(self, prompt: str) -> str:
        if not self.api_key:
            raise RuntimeError(
                "ANTHROPIC_API_KEY is not set, so the judge cannot run")
        body = json.dumps({
            "model": self.model,
            "max_tokens": self.max_tokens,
            "temperature": 0,
            "messages": [{"role": "user", "content": prompt}],
        }).encode()
        req = urllib.request.Request(
            "https://api.anthropic.com/v1/messages", data=body,
            headers={"content-type": "application/json",
                     "x-api-key": self.api_key,
                     "anthropic-version": "2023-06-01"})
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            data = json.loads(resp.read())
        return "".join(b.get("text", "") for b in data.get("content", []))


class OpenAIJudge:
    """Minimal OpenAI chat-completions client. Needs OPENAI_API_KEY."""

    def __init__(self, model: str = "gpt-4o-mini", max_tokens: int = 300,
                 api_key: str | None = None, timeout: int = 60):
        self.model = model
        self.max_tokens = max_tokens
        self.timeout = timeout
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY", "")

    def __call__(self, prompt: str) -> str:
        if not self.api_key:
            raise RuntimeError(
                "OPENAI_API_KEY is not set, so the judge cannot run")
        body = json.dumps({
            "model": self.model,
            "max_tokens": self.max_tokens,
            "temperature": 0,
            "messages": [{"role": "user", "content": prompt}],
        }).encode()
        req = urllib.request.Request(
            "https://api.openai.com/v1/chat/completions", data=body,
            headers={"content-type": "application/json",
                     "authorization": f"Bearer {self.api_key}"})
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            data = json.loads(resp.read())
        return data["choices"][0]["message"]["content"]


class CachedJudge:
    """Wraps any judge client with a JSON file cache keyed by the prompt.

    This is what makes the judge affordable and repeatable in CI: the second
    run of an unchanged pull request asks the model nothing.
    """

    def __init__(self, inner: JudgeClient, path: str | Path):
        self.inner = inner
        self.path = Path(path)
        self.hits = 0
        self.misses = 0
        try:
            self._cache: dict[str, str] = json.loads(self.path.read_text())
        except Exception:
            self._cache = {}

    @staticmethod
    def key(prompt: str) -> str:
        return hashlib.sha256(prompt.encode()).hexdigest()[:32]

    def __call__(self, prompt: str) -> str:
        k = self.key(prompt)
        if k in self._cache:
            self.hits += 1
            return self._cache[k]
        self.misses += 1
        reply = self.inner(prompt)
        self._cache[k] = reply
        self.save()
        return reply

    def save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self._cache, indent=2))
        except Exception:
            pass  # a cache that cannot be written is a slow judge, not a broken one


def build_client(provider: str, model: str | None = None,
                 cache_path: str | None = None) -> JudgeClient:
    provider = (provider or "").lower()
    if provider == "anthropic":
        inner: JudgeClient = AnthropicJudge(model=model or "claude-sonnet-4-5")
    elif provider == "openai":
        inner = OpenAIJudge(model=model or "gpt-4o-mini")
    else:
        raise ValueError(f"unknown judge provider {provider!r} "
                         f"(expected 'anthropic' or 'openai')")
    return CachedJudge(inner, cache_path) if cache_path else inner


# ---------------------------------------------------------------------------
# Parsing and grading
# ---------------------------------------------------------------------------

def parse_verdict(text: str) -> tuple[float | None, str]:
    """Pull {"score": .., "reason": ".."} out of a model reply.

    Returns (None, why) when the reply cannot be trusted - the caller then
    EXCLUDES the judge rather than inventing a score.
    """
    if not text:
        return (None, "the judge returned an empty reply")
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return (None, f"no JSON in the judge's reply: {text[:120]!r}")
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return (None, f"unparseable JSON from the judge: {text[:120]!r}")
    if "score" not in data:
        return (None, "the judge's JSON had no 'score' field")
    try:
        score = float(data["score"])
    except (TypeError, ValueError):
        return (None, f"non-numeric score from the judge: {data['score']!r}")
    if not 0.0 <= score <= 1.0:
        # A judge that returns 7/10 is common; rescale rather than reject.
        score = score / 10.0 if 1.0 < score <= 10.0 else max(0.0, min(1.0, score))
    reason = str(data.get("reason", "")).strip() or "no reason given"
    return (score, reason)


def build_prompt(golden: Golden, traj: Trajectory) -> str:
    spec = golden.judge
    criteria = ""
    if spec and spec.criteria:
        criteria = "\nIt must satisfy all of:\n" + "\n".join(
            f"- {c}" for c in spec.criteria)
    path = " -> ".join(traj.call_names()) or "(no tools called)"
    return JUDGE_PROMPT.format(
        rubric=(spec.rubric if spec else ""), criteria=criteria,
        prompt=golden.prompt, path=path,
        answer=traj.final_answer or "(no answer)")


def grade_judge(traj: Trajectory, golden: Golden,
                client: JudgeClient | Callable[[str], str] | None
                ) -> GraderScore | None:
    """Returns a GraderScore, or None when the judge should be excluded."""
    spec = golden.judge
    if spec is None or not spec.rubric:
        return None
    if client is None:
        return None

    prompt = build_prompt(golden, traj)
    try:
        reply = client(prompt)
    except Exception as e:                      # network, auth, rate limit
        return GraderScore(
            name="judge", score=0.0, weight=0.0,
            detail=f"judge unavailable: {e}", excluded=True,
            violations=[f"judge could not run ({e}) - excluded from the score"],
        )

    score, reason = parse_verdict(reply)
    if score is None:
        return GraderScore(
            name="judge", score=0.0, weight=0.0,
            detail=f"judge unusable: {reason}", excluded=True,
            violations=[f"judge reply unusable ({reason}) - "
                        f"excluded from the score"])

    violations = []
    if score < spec.min_score:
        violations.append(
            f"judge scored {score:.2f} against the rubric (needs "
            f"{spec.min_score:.2f}): {reason}")
    return GraderScore(name="judge", score=score, weight=0.0,
                       detail=f"{score:.2f} - {reason}", violations=violations)
