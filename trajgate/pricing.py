"""
Turning tokens into money.

Tokens are a proxy for cost; dollars are the thing anyone actually argues
about in a planning meeting. A 20% token rise on a cheap model and a 20% rise
on an expensive one are not the same regression, and an agent that gets
cheaper per token while calling the model twice as often can look like an
improvement in tokens and a disaster on the invoice.

PRICES ARE CONFIGURATION, NOT FACT. The table below ships with the demo
model only. Put your real per-million-token prices in config.yaml under
`pricing:` and keep them updated - this file will not guess on your behalf,
because a confidently wrong price is worse than no price.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class ModelPrice(BaseModel):
    """US dollars per MILLION tokens, which is how providers quote it."""
    usd_per_m_in: float
    usd_per_m_out: float
    note: str = ""


# Only the fake model the offline demo agent uses. Real models belong in
# config.yaml, where you can keep them current without touching code.
DEMO_PRICES: dict[str, ModelPrice] = {
    "demo-model-v1": ModelPrice(
        usd_per_m_in=3.00, usd_per_m_out=15.00,
        note="invented prices for the offline demo - not a real model"),
}


class PricingConfig(BaseModel):
    models: dict[str, ModelPrice] = Field(default_factory=lambda: dict(DEMO_PRICES))
    # Used when a trajectory does not name a model.
    default_model: str | None = "demo-model-v1"
    # If a named model is not in the table: raise (loud) or treat as free
    # (quiet, and the cost gate silently stops working). Loud is the default.
    strict: bool = True


class UnknownModel(KeyError):
    pass


def cost_usd(tokens_in: int, tokens_out: int, model: str | None,
             pricing: PricingConfig | None = None) -> float:
    """What this run cost, in dollars."""
    pricing = pricing or PricingConfig()
    name = model or pricing.default_model
    if name is None:
        if pricing.strict:
            raise UnknownModel(
                "no model on the trajectory and no default_model configured, "
                "so the cost cannot be worked out")
        return 0.0
    price = pricing.models.get(name)
    if price is None:
        if pricing.strict:
            raise UnknownModel(
                f"no price for model {name!r}. Add it under `pricing.models` "
                f"in config.yaml, or set pricing.strict: false to ignore cost")
        return 0.0
    return (tokens_in / 1_000_000 * price.usd_per_m_in
            + tokens_out / 1_000_000 * price.usd_per_m_out)


def fmt_usd(value: float) -> str:
    """Readable money for the report - agent runs are usually sub-cent."""
    if value == 0:
        return "$0"
    if value < 0.01:
        return f"${value:.5f}"
    if value < 1:
        return f"${value:.4f}"
    return f"${value:,.2f}"
