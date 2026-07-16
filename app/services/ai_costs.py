from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CostEstimate:
    amount: float | None
    currency: str | None


# Keep pricing explicit. Unknown or future models return no estimate instead of guessing.
MODEL_PRICES_USD_PER_1M: dict[str, tuple[float, float, float]] = {
    # model: input, cached_input, output
    "gpt-5-mini": (0.25, 0.025, 2.0),
    "gpt-5": (1.25, 0.125, 10.0),
}


def estimate_cost(
    model: str,
    input_tokens: int,
    output_tokens: int,
    cached_tokens: int = 0,
) -> CostEstimate:
    prices = MODEL_PRICES_USD_PER_1M.get(model)
    if prices is None:
        return CostEstimate(None, None)
    input_price, cached_price, output_price = prices
    billable_input = max(0, input_tokens - cached_tokens)
    amount = (
        billable_input * input_price
        + max(0, cached_tokens) * cached_price
        + max(0, output_tokens) * output_price
    ) / 1_000_000
    return CostEstimate(round(amount, 8), "USD")
