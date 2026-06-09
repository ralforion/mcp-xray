"""Per-model input-token list prices, with an opt-in live refresh.

The context-tax cost illustration prices the tool surface using *input*
pricing - tool definitions are loaded into context every turn, never output.

Default behavior is fully offline and deterministic: the built-in table below
is the source of truth, so two runs of the same server version always produce
the same number. ``load_prices(refresh=True)`` fetches the public pricing page,
parses the model table, merges the result *over* the built-in table, and caches
it. A failed or partial fetch always falls back to built-in values, and the
returned metadata records which source was used so a stale fetch is never
silent.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

from .vendors import ANTHROPIC, OPENAI, vendor_for

ANTHROPIC_PRICING_URL = "https://platform.claude.com/docs/en/docs/about-claude/pricing"
OPENAI_PRICING_URL = "https://developers.openai.com/api/docs/pricing"
CACHE_PATH = Path.home() / ".cache" / "mcp-xray" / "prices.json"

# Public list prices in USD per 1M input tokens, keyed by model id.
# Anthropic source: platform.claude.com/docs pricing (verified 2026-06-02).
# OpenAI source: developers.openai.com/api/docs/pricing (verified 2026-06-02);
# the gpt-5.x family is authoritative from that page, the gpt-4.x rows are
# long-standing list prices. Kept as the offline source of truth and the
# fallback when a live fetch is unavailable.
INPUT_PRICE_PER_MTOK = {
    # Anthropic
    "claude-opus-4-8": 5.0,
    "claude-opus-4-7": 5.0,
    "claude-opus-4-6": 5.0,
    "claude-opus-4-5": 5.0,
    "claude-opus-4-1": 15.0,
    "claude-opus-4": 15.0,
    "claude-sonnet-4-6": 3.0,
    "claude-sonnet-4-5": 3.0,
    "claude-sonnet-4": 3.0,
    "claude-haiku-4-5": 1.0,
    "claude-haiku-3-5": 0.80,
    # OpenAI
    "gpt-5.5": 5.0,
    "gpt-5.4": 2.50,
    "gpt-5.4-mini": 0.75,
    "gpt-5.4-nano": 0.20,
    "gpt-4.1": 2.0,
    "gpt-4.1-mini": 0.40,
    "gpt-4.1-nano": 0.10,
    "gpt-4o": 2.50,
    "gpt-4o-mini": 0.15,
}

# Anchor used when the model is unspecified or unrecognized (Sonnet-tier).
DEFAULT_INPUT_COST_PER_MTOK = 3.0

# Anthropic: a model cell ("Claude Opus 4.8") followed immediately by its first
# "$N / MTok" cell - the Base Input Tokens column on the rendered page.
_ANTHROPIC_ROW = re.compile(
    r">Claude (Opus|Sonnet|Haiku) ([0-9.]+)[^<]*</td>\s*<td[^>]*>\$([0-9.]+) / MTok",
    re.S,
)

# OpenAI: the pricing page serializes each row as
#   [0,"<model> (<context note>)"],[0,<input>],[0,<cached>],[0,<output>]
# (HTML-escaped). Requiring the full 4-cell shape avoids grabbing the wrong
# column from partial/odd rows; the leading token is the model id.
_OPENAI_ROW = re.compile(
    r'\[0,"([a-zA-Z0-9.\-]+)[^"]*"\],\[0,([0-9]+(?:\.[0-9]+)?)\],'
    r'\[0,(?:[0-9.]+|"")\],\[0,(?:[0-9.]+|"")\]'
)
# Snapshot/dated ids (gpt-4o-2024-08-06, gpt-3.5-turbo-0613) carry an odd row
# layout on the page and misparse; drop them - the resolver's longest-prefix
# match maps a dated id back to its clean bare-id price anyway.
_OPENAI_SNAPSHOT = re.compile(r"-\d{3,}")


def input_price_per_mtok(model: str | None, table: dict | None = None) -> tuple[float, bool]:
    """Resolve the input list price (USD/Mtok) for a model id.

    Returns ``(price, known)``. Strips a ``[1m]``-style context suffix and
    lowercases, matches an exact key, then falls back to the longest known
    prefix (so date-stamped ids like ``claude-haiku-4-5-20251001`` resolve).
    Unknown/None models return the Sonnet-tier default with ``known=False`` so
    callers can flag the figure as a fallback. ``table`` overrides the built-in
    map (used by the live-refresh path).
    """
    prices = table or INPUT_PRICE_PER_MTOK
    if not model:
        return DEFAULT_INPUT_COST_PER_MTOK, False
    norm = model.split("[")[0].strip().lower()
    if norm in prices:
        return prices[norm], True
    for key in sorted(prices, key=len, reverse=True):
        if norm.startswith(key):
            return prices[key], True
    return DEFAULT_INPUT_COST_PER_MTOK, False


def parse_anthropic_prices(html: str) -> dict[str, float]:
    """Extract ``{model_id: input_usd_per_mtok}`` from the rendered Anthropic
    pricing page (first / Base Input cell per model). Empty if the structure no
    longer matches."""
    out: dict[str, float] = {}
    for family, version, price in _ANTHROPIC_ROW.findall(html):
        key = f"claude-{family.lower()}-{version.replace('.', '-')}"
        out.setdefault(key, float(price))
    return out


def parse_openai_prices(html: str) -> dict[str, float]:
    """Extract ``{model_id: input_usd_per_mtok}`` from the OpenAI pricing page's
    serialized rows, skipping dated snapshot ids. Empty if the structure no
    longer matches."""
    import html as _html

    text = _html.unescape(html)
    out: dict[str, float] = {}
    for name, inp in _OPENAI_ROW.findall(text):
        n = name.lower()
        if not vendor_for(n) == OPENAI or _OPENAI_SNAPSHOT.search(n):
            continue
        out.setdefault(n, float(inp))
    return out


# vendor -> (pricing url, html parser)
_VENDOR_SOURCES = {
    ANTHROPIC: (ANTHROPIC_PRICING_URL, parse_anthropic_prices),
    OPENAI: (OPENAI_PRICING_URL, parse_openai_prices),
}


def fetch_vendor_prices(vendor: str, *, sources: dict | None = None, timeout: float = 20.0) -> tuple[str, dict[str, float]]:
    """Fetch + parse one vendor's live prices. Returns ``(url, table)``; raises
    on network/parse error or empty result so the caller can fall back."""
    src = (sources or _VENDOR_SOURCES).get(vendor)
    if src is None:
        raise ValueError(f"no pricing source for vendor {vendor!r}")
    url, parser = src
    import httpx

    resp = httpx.get(url, follow_redirects=True, timeout=timeout, headers={"user-agent": "Mozilla/5.0 mcp-xray"})
    resp.raise_for_status()
    parsed = parser(resp.text)
    if not parsed:
        raise ValueError(f"no price rows parsed from {vendor} pricing page")
    return url, parsed


def load_prices(
    *,
    refresh: bool = False,
    model: str | None = None,
    cache_path: Path = CACHE_PATH,
    sources: dict | None = None,
) -> tuple[dict, dict]:
    """Return ``(table, meta)``.

    ``refresh=False`` → built-in table, deterministic, no network.
    ``refresh=True``  → fetch the live prices for ``model``'s vendor (both
    vendors if the model is unknown/None), merge *over* the built-in table, and
    cache per vendor. On failure, fall back to that vendor's cache if present,
    else built-in - always with a ``meta`` that names the source so a stale or
    failed fetch is never silent.

    ``meta`` keys: ``source`` (built-in|fetched|cache), ``fetched_at`` (ISO or
    None), ``url``, ``count`` (models merged live/from cache), ``note``.
    """
    if not refresh:
        return dict(INPUT_PRICE_PER_MTOK), {"source": "built-in", "fetched_at": None, "url": None, "count": 0}

    vendor = vendor_for(model)
    vendors = [vendor] if vendor in _VENDOR_SOURCES else list(_VENDOR_SOURCES)
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    cache = _read_cache(cache_path)

    live: dict[str, float] = {}
    used_url = None
    notes: list[str] = []
    source = "built-in"
    fetched_at = None
    for v in vendors:
        try:
            url, table = fetch_vendor_prices(v, sources=sources)
            live.update(table)
            cache.setdefault("vendors", {})[v] = {"fetched_at": now, "url": url, "table": table}
            used_url, source, fetched_at = url, "fetched", now
        except Exception as exc:  # network, parse, or HTTP error
            cached_v = cache.get("vendors", {}).get(v)
            if cached_v:
                live.update({k: float(val) for k, val in cached_v.get("table", {}).items()})
                if source != "fetched":
                    source, fetched_at, used_url = "cache", cached_v.get("fetched_at"), cached_v.get("url")
                notes.append(f"{v} fetch failed ({type(exc).__name__}); used cached")
            else:
                notes.append(f"{v} fetch failed ({type(exc).__name__}); used built-in")

    _write_cache(cache_path, cache)
    meta = {"source": source, "fetched_at": fetched_at, "url": used_url, "count": len(live)}
    if notes:
        meta["note"] = "; ".join(notes)
    return {**INPUT_PRICE_PER_MTOK, **live}, meta


def _read_cache(cache_path: Path) -> dict:
    try:
        return json.loads(cache_path.read_text())
    except (OSError, ValueError):
        return {}


def _write_cache(cache_path: Path, cache: dict) -> None:
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(cache, indent=2))
    except OSError:
        pass  # caching is best-effort
