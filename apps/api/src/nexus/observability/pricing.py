"""Versioned public list prices. These are estimates, not an invoice.

Copied 2026-10-19 from OpenAI's published text rates for ``gpt-4o-mini``
($0.15 / 1M input, $0.60 / 1M output) and ``text-embedding-3-small``
($0.02 / 1M tokens). Cached input is not discounted. Tavily has no stable
public unit price; search cost stays unavailable unless configured.
"""

from decimal import Decimal

PRICING_VERSION = "2026-10-19"
PER_MILLION = Decimal("1000000")

# model -> (input USD per 1M tokens, output USD per 1M tokens)
CHAT_PRICES: dict[str, tuple[Decimal, Decimal]] = {
    "gpt-4o-mini": (Decimal("0.15"), Decimal("0.60")),
}

# model -> USD per 1M tokens
EMBEDDING_PRICES: dict[str, Decimal] = {
    "text-embedding-3-small": Decimal("0.02"),
}
