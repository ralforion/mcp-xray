"""Model-id -> vendor routing.

One place that maps a ``--model`` string to the vendor whose tokenizer, chat
API, and price list apply. Counting, the behavioral probe, and pricing all
dispatch through ``vendor_for`` so adding a vendor is a single-table change.
"""

from __future__ import annotations

ANTHROPIC = "anthropic"
OPENAI = "openai"
UNKNOWN = "unknown"

# Ordered prefix table; first match wins. Lowercased before matching.
_PREFIXES = [
    ("claude", ANTHROPIC),
    ("gpt-", OPENAI),
    ("gpt", OPENAI),
    ("o1", OPENAI),
    ("o3", OPENAI),
    ("o4", OPENAI),
    ("chatgpt", OPENAI),
]


def vendor_for(model: str | None) -> str:
    """Return the vendor that owns ``model`` (``anthropic`` | ``openai`` |
    ``unknown``). Strips a ``[1m]``-style context suffix first."""
    if not model:
        return UNKNOWN
    norm = model.split("[")[0].strip().lower()
    for prefix, vendor in _PREFIXES:
        if norm.startswith(prefix):
            return vendor
    return UNKNOWN
