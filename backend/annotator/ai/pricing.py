"""Per-model token prices (USD per million tokens) for cost estimates.

Prices are first-party Anthropic API list prices as of 2026-10. Costs shown to users are
labelled as estimates. Unknown models fall back to ANNOTATOR_PRICE_* overrides, or are
reported as "unknown" rather than guessed.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Price:
    input: float
    output: float
    cache_read: float | None = None  # defaults to 10% of input

    def cache_read_rate(self) -> float:
        return self.cache_read if self.cache_read is not None else self.input * 0.1


PRICES: dict[str, Price] = {
    "claude-fable-5-1": Price(10.0, 50.0, 0.25),
    "claude-fable-5": Price(10.0, 50.0),
    "claude-opus-5-5": Price(4.0, 20.0, 0.20),
    "claude-opus-5": Price(5.0, 25.0),
    "claude-opus-4-8": Price(5.0, 25.0),
    "claude-opus-4-7": Price(5.0, 25.0),
    "claude-opus-4-6": Price(5.0, 25.0),
    "claude-sonnet-5-5": Price(2.0, 10.0, 0.20),
    "claude-sonnet-5": Price(2.0, 10.0),
    "claude-sonnet-4-6": Price(3.0, 15.0),
    "claude-haiku-5-5": Price(0.10, 0.50),  # prompts up to 100K tokens
    "claude-haiku-4-5": Price(1.0, 5.0),
}
CACHE_WRITE_MULTIPLIER = 1.25  # 5-minute TTL


def price_for(model: str, override_in: float | None = None, override_out: float | None = None) -> Price | None:
    if override_in is not None and override_out is not None:
        return Price(override_in, override_out)
    if model in PRICES:
        return PRICES[model]
    # Tolerate provider prefixes or suffixes, e.g. "anthropic.claude-opus-5-5".
    for key in sorted(PRICES, key=len, reverse=True):
        if key in model:
            return PRICES[key]
    return None


def estimate_cost(model: str, input_tokens: int, output_tokens: int, cache_write: int, cache_read: int,
                  override_in: float | None = None, override_out: float | None = None) -> float | None:
    p = price_for(model, override_in, override_out)
    if p is None:
        return None
    total = (
        input_tokens * p.input
        + output_tokens * p.output
        + cache_write * p.input * CACHE_WRITE_MULTIPLIER
        + cache_read * p.cache_read_rate()
    )
    return round(total / 1_000_000, 6)
