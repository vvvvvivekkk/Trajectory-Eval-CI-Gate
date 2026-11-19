"""
Tool definitions for the real agent: one description per tool, in both the
Anthropic and OpenAI shapes.

The tools themselves are the same fake support backend the offline demo uses
(demo_agent/tools.py), so the only difference between the two agents in this
repo is who decides what to call: a script, or a language model.
"""

from __future__ import annotations

from typing import Any

SYSTEM_PROMPT = """You are a customer-support agent for an online shop.

Use the tools to find out what is true before you act. Rules you must follow:
- Look an order up before you do anything to it.
- Check refund eligibility BEFORE issuing any refund. Never skip this.
- Never refund more than the order was worth.
- For a disputed or duplicate charge, escalate to a human; do not refund it
  yourself.
- For a general how-to question, search the knowledge base. Do not look up
  customer records you do not need.
- If a tool fails, retry it at most twice. If it still fails, say plainly
  that you could not complete the lookup, and escalate.
- Never invent an answer.

When you have finished, reply to the customer in two or three sentences,
including the order id and any amount."""

TOOLS: list[dict[str, Any]] = [
    {
        "name": "search_kb",
        "description": "Search the help-centre knowledge base for an article.",
        "parameters": {
            "type": "object",
            "properties": {"query": {
                "type": "string",
                "description": "What to search for, in a few words."}},
            "required": ["query"],
        },
    },
    {
        "name": "get_order",
        "description": "Look up one order by its id (format A1234).",
        "parameters": {
            "type": "object",
            "properties": {"order_id": {"type": "string"}},
            "required": ["order_id"],
        },
    },
    {
        "name": "get_customer",
        "description": "Find a customer by email, with their order ids.",
        "parameters": {
            "type": "object",
            "properties": {"email": {"type": "string"}},
            "required": ["email"],
        },
    },
    {
        "name": "check_refund_eligibility",
        "description": ("Check whether an order can still be refunded. Must "
                        "be called before issue_refund."),
        "parameters": {
            "type": "object",
            "properties": {"order_id": {"type": "string"}},
            "required": ["order_id"],
        },
    },
    {
        "name": "issue_refund",
        "description": "Refund an order. Only after checking eligibility.",
        "parameters": {
            "type": "object",
            "properties": {
                "order_id": {"type": "string"},
                "amount": {"type": "number",
                           "description": "Never more than the order total."},
            },
            "required": ["order_id", "amount"],
        },
    },
    {
        "name": "get_payments",
        "description": "List the charges taken against one order.",
        "parameters": {
            "type": "object",
            "properties": {"order_id": {"type": "string"}},
            "required": ["order_id"],
        },
    },
    {
        "name": "send_email",
        "description": "Email the customer a confirmation.",
        "parameters": {
            "type": "object",
            "properties": {"to": {"type": "string"}, "body": {"type": "string"}},
            "required": ["to", "body"],
        },
    },
    {
        "name": "escalate_to_human",
        "description": "Hand the case to a human agent, with a reason.",
        "parameters": {
            "type": "object",
            "properties": {"reason": {"type": "string"}},
            "required": ["reason"],
        },
    },
]


def anthropic_tools() -> list[dict]:
    return [{"name": t["name"], "description": t["description"],
             "input_schema": t["parameters"]} for t in TOOLS]


def openai_tools() -> list[dict]:
    return [{"type": "function",
             "function": {"name": t["name"], "description": t["description"],
                          "parameters": t["parameters"]}} for t in TOOLS]
