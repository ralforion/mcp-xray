"""Pricing resolution + opt-in live refresh (with built-in fallback)."""

from pathlib import Path

from mcp_xray import pricing


def test_resolver_known_models():
    assert pricing.input_price_per_mtok("claude-opus-4-8") == (5.0, True)
    assert pricing.input_price_per_mtok("claude-sonnet-4-6") == (3.0, True)
    assert pricing.input_price_per_mtok("claude-haiku-4-5") == (1.0, True)


def test_resolver_strips_suffix_and_datestamp():
    assert pricing.input_price_per_mtok("claude-opus-4-8[1m]") == (5.0, True)
    # date-stamped id resolves via longest-prefix match
    assert pricing.input_price_per_mtok("claude-haiku-4-5-20251001") == (1.0, True)


def test_resolver_known_openai_models():
    assert pricing.input_price_per_mtok("gpt-5.5") == (5.0, True)
    assert pricing.input_price_per_mtok("gpt-4o-mini") == (0.15, True)
    # date-stamped OpenAI id resolves via longest-prefix
    assert pricing.input_price_per_mtok("gpt-4o-2024-08-06") == (2.50, True)


def test_resolver_unknown_falls_back_to_default():
    assert pricing.input_price_per_mtok("mistral-large") == (pricing.DEFAULT_INPUT_COST_PER_MTOK, False)
    assert pricing.input_price_per_mtok(None) == (pricing.DEFAULT_INPUT_COST_PER_MTOK, False)


def test_resolver_honors_override_table():
    table = {"claude-opus-4-8": 99.0}
    assert pricing.input_price_per_mtok("claude-opus-4-8", table) == (99.0, True)


def test_parse_anthropic_prices_from_rendered_html():
    html = (
        '<td class="x">Claude Opus 4.8</td><td class="x">$5 / MTok</td>'
        '<td class="x">$6.25 / MTok</td>'  # cache-write column must NOT win
        '<td class="x">Claude Sonnet 4.6</td><td class="x">$3 / MTok</td>'
    )
    assert pricing.parse_anthropic_prices(html) == {"claude-opus-4-8": 5.0, "claude-sonnet-4-6": 3.0}


def test_parse_openai_prices_skips_dated_snapshots():
    # full 4-cell rows: name, input, cached, output
    blob = (
        '[0,"gpt-5.5 (&lt;272K context length)"],[0,5],[0,0.5],[0,30]'
        '[0,"gpt-4o-mini"],[0,0.15],[0,0.075],[0,0.6]'
        '[0,"gpt-4o-2024-08-06"],[0,25],[0,""],[0,50]'  # dated snapshot -> dropped
    )
    out = pricing.parse_openai_prices(blob)
    assert out == {"gpt-5.5": 5.0, "gpt-4o-mini": 0.15}


def test_load_default_is_builtin_and_deterministic():
    table, meta = pricing.load_prices()
    assert meta["source"] == "built-in"
    assert table == pricing.INPUT_PRICE_PER_MTOK


def _bad_sources():
    # Force every vendor fetch to fail by pointing at a dead port.
    return {v: ("http://127.0.0.1:9/nope", lambda _t: {}) for v in pricing._VENDOR_SOURCES}


def test_load_refresh_failure_falls_back_to_builtin(tmp_path: Path):
    cache = tmp_path / "prices.json"  # absent
    table, meta = pricing.load_prices(refresh=True, model="gpt-5.5", cache_path=cache, sources=_bad_sources())
    assert meta["source"] == "built-in"
    assert "fetch failed" in meta["note"]
    assert table == pricing.INPUT_PRICE_PER_MTOK


def test_load_refresh_failure_uses_cache_when_present(tmp_path: Path):
    cache = tmp_path / "prices.json"
    cache.write_text(
        '{"vendors": {"openai": {"fetched_at": "2026-06-02T00:00:00", "url": "u", '
        '"table": {"gpt-5.5": 42.0}}}}'
    )
    table, meta = pricing.load_prices(refresh=True, model="gpt-5.5", cache_path=cache, sources=_bad_sources())
    assert meta["source"] == "cache"
    assert table["gpt-5.5"] == 42.0  # cache overrides built-in


def test_load_refresh_fetches_only_model_vendor(tmp_path: Path):
    cache = tmp_path / "prices.json"
    calls = []

    def ok_parser(_t):
        return {"gpt-5.5": 5.0}

    def fail_parser(_t):  # pragma: no cover - should never be called
        raise AssertionError("anthropic source must not be fetched for an OpenAI model")

    # Only the OpenAI source should be hit when the model is an OpenAI model.
    sources = {
        pricing.OPENAI: ("https://example.test/openai", ok_parser),
        pricing.ANTHROPIC: ("https://example.test/anthropic", fail_parser),
    }

    def fake_fetch(vendor, *, sources, timeout=20.0):
        calls.append(vendor)
        url, parser = sources[vendor]
        return url, parser(None)

    orig = pricing.fetch_vendor_prices
    pricing.fetch_vendor_prices = fake_fetch
    try:
        table, meta = pricing.load_prices(refresh=True, model="gpt-5.5", cache_path=cache, sources=sources)
    finally:
        pricing.fetch_vendor_prices = orig
    assert calls == [pricing.OPENAI]
    assert meta["source"] == "fetched"
    assert table["gpt-5.5"] == 5.0
