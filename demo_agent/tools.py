"""
A fake customer-support backend, so the demo runs offline, free and the same
way every time. Swap these for your real tools and nothing else changes.
"""

from __future__ import annotations

from typing import Any

ORDERS: dict[str, dict[str, Any]] = {
    "A1001": {"order_id": "A1001", "email": "priya@example.com", "amount": 120.00,
              "days_since_delivery": 5, "status": "delivered", "charges": 1},
    "A1002": {"order_id": "A1002", "email": "sam@example.com", "amount": 90.00,
              "days_since_delivery": 90, "status": "delivered", "charges": 1},
    "A1003": {"order_id": "A1003", "email": "lee@example.com", "amount": 500.00,
              "days_since_delivery": 2, "status": "delivered", "charges": 2},
}

CUSTOMERS: dict[str, list[str]] = {
    "priya@example.com": ["A1001"],
    "sam@example.com": ["A1002"],
    "lee@example.com": ["A1003"],
}

KB: dict[str, str] = {
    "password": "To reset your password, open Settings > Security and choose "
                "'Reset password'. A reset link is emailed to you and is valid "
                "for 30 minutes.",
    "shipping": "Standard shipping takes 3-5 business days.",
    "refund": "Refunds are available within 30 days of delivery.",
}

REFUND_WINDOW_DAYS = 30

# Cost model for the fake tools, so the gate has cost numbers to watch.
LATENCY_MS = {
    "search_kb": 120, "get_order": 180, "get_customer": 150,
    "check_refund_eligibility": 90, "issue_refund": 320,
    "get_payments": 200, "send_email": 140, "escalate_to_human": 60,
}


class ToolError(Exception):
    pass


class Backend:
    """Holds the per-run state, including deterministic transient failures."""

    def __init__(self, flaky_tool: str | None = None, flaky_times: int = 0):
        self.flaky_tool = flaky_tool
        self.flaky_times = flaky_times
        self._flaky_seen = 0

    def _maybe_fail(self, tool: str) -> None:
        if tool == self.flaky_tool and self._flaky_seen < self.flaky_times:
            self._flaky_seen += 1
            raise ToolError(f"upstream 503 from {tool} (transient)")

    # --- the tools ---------------------------------------------------------

    def search_kb(self, query: str) -> str:
        self._maybe_fail("search_kb")
        for key, text in KB.items():
            if key in (query or "").lower():
                return text
        return "No article found."

    def get_order(self, order_id: str) -> dict:
        self._maybe_fail("get_order")
        if order_id not in ORDERS:
            raise ToolError(f"order {order_id} not found")
        return dict(ORDERS[order_id])

    def get_customer(self, email: str) -> dict:
        self._maybe_fail("get_customer")
        if email not in CUSTOMERS:
            raise ToolError(f"no customer for {email}")
        return {"email": email, "order_ids": CUSTOMERS[email]}

    def check_refund_eligibility(self, order_id: str) -> dict:
        self._maybe_fail("check_refund_eligibility")
        order = ORDERS.get(order_id)
        if not order:
            raise ToolError(f"order {order_id} not found")
        eligible = order["days_since_delivery"] <= REFUND_WINDOW_DAYS
        return {
            "order_id": order_id,
            "eligible": eligible,
            "reason": "within 30 day window" if eligible
                      else f"delivered {order['days_since_delivery']} days ago, "
                           f"outside the 30 day window",
            "max_amount": order["amount"],
        }

    def issue_refund(self, order_id: str, amount: float) -> dict:
        self._maybe_fail("issue_refund")
        order = ORDERS.get(order_id)
        if not order:
            raise ToolError(f"order {order_id} not found")
        return {"order_id": order_id, "refunded": amount, "ref": f"RF-{order_id}"}

    def get_payments(self, order_id: str) -> dict:
        self._maybe_fail("get_payments")
        order = ORDERS.get(order_id)
        if not order:
            raise ToolError(f"order {order_id} not found")
        return {"order_id": order_id, "charge_count": order["charges"],
                "amount_each": order["amount"]}

    def send_email(self, to: str, body: str) -> dict:
        self._maybe_fail("send_email")
        return {"to": to, "queued": True}

    def escalate_to_human(self, reason: str) -> dict:
        self._maybe_fail("escalate_to_human")
        return {"ticket": "T-5567", "reason": reason}

    def call(self, tool: str, **kwargs):
        fn = getattr(self, tool, None)
        if fn is None:
            raise ToolError(f"unknown tool {tool}")
        return fn(**kwargs)
