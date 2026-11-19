"""
Model clients for the real agent.

One small interface, three implementations: Anthropic, OpenAI, and a scripted
one for tests. Written with urllib so the repo needs no SDK and no extra
dependency to install in CI.
"""

from __future__ import annotations

import json
import os
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Protocol

from .schemas import SYSTEM_PROMPT, anthropic_tools, openai_tools


@dataclass
class ToolRequest:
    id: str
    name: str
    args: dict[str, Any]


@dataclass
class ModelReply:
    """One turn from the model: some text, and/or some tools to run."""
    text: str = ""
    tool_requests: list[ToolRequest] = field(default_factory=list)
    tokens_in: int = 0
    tokens_out: int = 0
    raw: Any = None

    @property
    def wants_tools(self) -> bool:
        return bool(self.tool_requests)


class ModelClient(Protocol):
    model_name: str

    def respond(self, messages: list[dict]) -> ModelReply: ...

    def tool_result_message(self, results: list[tuple[ToolRequest, str, bool]]
                            ) -> dict: ...

    def assistant_message(self, reply: ModelReply) -> dict: ...


# ---------------------------------------------------------------------------

class AnthropicClient:
    def __init__(self, model: str = "claude-sonnet-4-5", max_tokens: int = 1024,
                 api_key: str | None = None, timeout: int = 120,
                 system: str = SYSTEM_PROMPT):
        self.model_name = model
        self.max_tokens = max_tokens
        self.timeout = timeout
        self.system = system
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")

    def respond(self, messages: list[dict]) -> ModelReply:
        if not self.api_key:
            raise RuntimeError("ANTHROPIC_API_KEY is not set")
        body = json.dumps({
            "model": self.model_name, "max_tokens": self.max_tokens,
            "system": self.system, "tools": anthropic_tools(),
            "messages": messages,
        }).encode()
        req = urllib.request.Request(
            "https://api.anthropic.com/v1/messages", data=body,
            headers={"content-type": "application/json",
                     "x-api-key": self.api_key,
                     "anthropic-version": "2023-06-01"})
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            data = json.loads(resp.read())
        text = "".join(b.get("text", "") for b in data.get("content", [])
                       if b.get("type") == "text")
        calls = [ToolRequest(id=b["id"], name=b["name"], args=b.get("input") or {})
                 for b in data.get("content", []) if b.get("type") == "tool_use"]
        usage = data.get("usage") or {}
        return ModelReply(text=text, tool_requests=calls,
                          tokens_in=usage.get("input_tokens", 0),
                          tokens_out=usage.get("output_tokens", 0), raw=data)

    def assistant_message(self, reply: ModelReply) -> dict:
        content: list[dict] = []
        if reply.text:
            content.append({"type": "text", "text": reply.text})
        for c in reply.tool_requests:
            content.append({"type": "tool_use", "id": c.id, "name": c.name,
                            "input": c.args})
        return {"role": "assistant", "content": content}

    def tool_result_message(self, results) -> dict:
        return {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": req.id, "content": out,
             "is_error": not ok} for req, out, ok in results]}


class OpenAIClient:
    def __init__(self, model: str = "gpt-4o-mini", max_tokens: int = 1024,
                 api_key: str | None = None, timeout: int = 120,
                 system: str = SYSTEM_PROMPT):
        self.model_name = model
        self.max_tokens = max_tokens
        self.timeout = timeout
        self.system = system
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY", "")

    def respond(self, messages: list[dict]) -> ModelReply:
        if not self.api_key:
            raise RuntimeError("OPENAI_API_KEY is not set")
        body = json.dumps({
            "model": self.model_name, "max_tokens": self.max_tokens,
            "tools": openai_tools(),
            "messages": [{"role": "system", "content": self.system}] + messages,
        }).encode()
        req = urllib.request.Request(
            "https://api.openai.com/v1/chat/completions", data=body,
            headers={"content-type": "application/json",
                     "authorization": f"Bearer {self.api_key}"})
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            data = json.loads(resp.read())
        msg = data["choices"][0]["message"]
        calls = []
        for tc in (msg.get("tool_calls") or []):
            fn = tc["function"]
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {"_raw": fn.get("arguments")}
            calls.append(ToolRequest(id=tc["id"], name=fn["name"], args=args))
        usage = data.get("usage") or {}
        return ModelReply(text=msg.get("content") or "", tool_requests=calls,
                          tokens_in=usage.get("prompt_tokens", 0),
                          tokens_out=usage.get("completion_tokens", 0), raw=data)

    def assistant_message(self, reply: ModelReply) -> dict:
        msg: dict[str, Any] = {"role": "assistant",
                               "content": reply.text or None}
        if reply.tool_requests:
            msg["tool_calls"] = [
                {"id": c.id, "type": "function",
                 "function": {"name": c.name, "arguments": json.dumps(c.args)}}
                for c in reply.tool_requests]
        return msg

    def tool_result_message(self, results) -> dict:
        # OpenAI wants one message per result; the loop flattens this.
        return {"_multi": [{"role": "tool", "tool_call_id": req.id,
                            "content": out} for req, out, ok in results]}


class ScriptedClient:
    """A fake model for tests: returns prepared turns, records what it saw.

    This is how you develop against the eval without paying for a model, and
    how the agent loop itself gets tested - a scripted model can be made to
    skip a safety check on purpose, which a real one cannot be relied on to
    do when you need it.
    """

    model_name = "scripted-model"

    def __init__(self, turns: list[ModelReply]):
        self.turns = list(turns)
        self.seen: list[list[dict]] = []

    def respond(self, messages: list[dict]) -> ModelReply:
        self.seen.append([dict(m) for m in messages])
        if not self.turns:
            return ModelReply(text="(the scripted model ran out of turns)")
        return self.turns.pop(0)

    def assistant_message(self, reply: ModelReply) -> dict:
        return {"role": "assistant", "content": reply.text,
                "tool_calls": [c.name for c in reply.tool_requests]}

    def tool_result_message(self, results) -> dict:
        return {"role": "user", "content": [
            {"tool": req.name, "output": out, "ok": ok}
            for req, out, ok in results]}


def call(name: str, *args, **kwargs) -> ToolRequest:
    """Shorthand for tests: call('get_order', order_id='A1001')."""
    return ToolRequest(id=f"call_{name}_{abs(hash(str(kwargs))) % 10000}",
                       name=name, args=dict(kwargs))


def turn(*tool_calls: ToolRequest, text: str = "", tokens_in: int = 500,
         tokens_out: int = 80) -> ModelReply:
    """Shorthand for tests: one model turn."""
    return ModelReply(text=text, tool_requests=list(tool_calls),
                      tokens_in=tokens_in, tokens_out=tokens_out)


def build_client(provider: str, model: str | None = None) -> ModelClient:
    provider = (provider or "").lower()
    if provider == "anthropic":
        return AnthropicClient(model=model or "claude-sonnet-4-5")
    if provider == "openai":
        return OpenAIClient(model=model or "gpt-4o-mini")
    raise ValueError(f"unknown provider {provider!r} "
                     f"(expected 'anthropic' or 'openai')")
