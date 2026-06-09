"""Token counting -- one interface, two backends (PLAN S13).

- ``api`` (default, authoritative): Anthropic ``messages.count_tokens`` -- the
  only faithful measure of tool serialization. Requires an API key.
- ``offline`` (flagged ESTIMATE): a documented heuristic over the serialized
  schema, for keyless / air-gapped quick scans. Never the headline number.

Both expose ``count(tools) -> int`` so the static_hygiene probe's leave-one-out
attribution is backend-agnostic.
"""

from __future__ import annotations

import json
from typing import Any, Protocol

# A tiny user message is required by the API; its cost is constant and cancels
# out in every (all - subset) difference the hygiene probe takes.
_PROBE_MESSAGE = [{"role": "user", "content": "."}]


def _api_tool(tool: dict) -> dict:
    """Coerce a tool into a shape the Anthropic count_tokens API accepts.

    The API requires every tool's ``input_schema`` to be an object schema with
    an explicit ``"type"``. Servers in the wild (and mcp-xray's own synthetic
    blobs) sometimes omit it or send an empty/missing schema; default it to
    ``{"type": "object"}`` so counting never 400s on an otherwise-valid tool.
    """
    schema = tool.get("input_schema")
    if not isinstance(schema, dict):
        schema = {}
    if "type" not in schema:
        schema = {**schema, "type": "object"}
    return {**tool, "input_schema": schema}


class TokenCounter(Protocol):
    authoritative: bool
    backend: str
    model: str | None

    def count(self, tools: list[dict]) -> int: ...


class ApiCounter:
    """Authoritative backend. ``model`` MUST equal the model the client deploys
    agents with (PLAN S13 / S16)."""

    authoritative = True
    backend = "api"

    def __init__(self, model: str, client: Any | None = None) -> None:
        self.model = model
        if client is None:
            try:
                import anthropic
            except ImportError as e:  # pragma: no cover - env dependent
                raise RuntimeError(
                    "anthropic SDK not installed; install mcp-xray[api] or use the "
                    "offline counter"
                ) from e
            import os

            if not os.environ.get("ANTHROPIC_API_KEY"):
                raise RuntimeError(
                    "ANTHROPIC_API_KEY not set -- the api token backend needs a key. "
                    "Export it, or use --token-backend offline for a flagged estimate."
                )
            client = anthropic.Anthropic()
        self._client = client
        self._cache: dict[str, int] = {}

    def count(self, tools: list[dict]) -> int:
        tools = [_api_tool(t) for t in tools]
        key = json.dumps(tools, sort_keys=True, default=str)
        if key in self._cache:
            return self._cache[key]
        kwargs: dict[str, Any] = {"model": self.model, "messages": _PROBE_MESSAGE}
        if tools:
            kwargs["tools"] = tools
        resp = self._client.messages.count_tokens(**kwargs)
        n = int(getattr(resp, "input_tokens", 0))
        self._cache[key] = n
        return n


class TiktokenCounter:
    """Authoritative OpenAI backend. Counts the tool surface with the model's
    real tiktoken encoding -- local, no API call, no credits needed. The
    *tokenizer* is exact; the request *framing* OpenAI adds around the function
    specs is not public, but it is a constant that cancels in every
    ``count(all) - count(subset)`` difference the hygiene probe takes, so
    per-tool attribution is exact and only the absolute base carries a small
    fixed offset. Encoding is resolved per model, falling back to o200k_base
    (the GPT-4o/4.1/5 family encoder) for ids tiktoken doesn't recognize yet.
    """

    authoritative = True
    backend = "tiktoken"

    def __init__(self, model: str) -> None:
        self.model = model
        import tiktoken

        try:
            self._enc = tiktoken.encoding_for_model(model)
        except KeyError:
            self._enc = tiktoken.get_encoding("o200k_base")
        self._cache: dict[str, int] = {}

    @staticmethod
    def _to_function(tool: dict) -> dict:
        return {
            "type": "function",
            "function": {
                "name": tool.get("name", ""),
                "description": tool.get("description", "") or "",
                "parameters": tool.get("input_schema") or {"type": "object", "properties": {}},
            },
        }

    def count(self, tools: list[dict]) -> int:
        key = json.dumps(tools, sort_keys=True, default=str)
        if key in self._cache:
            return self._cache[key]
        if not tools:
            n = len(self._enc.encode("."))  # constant base framing
        else:
            blob = json.dumps([self._to_function(t) for t in tools], default=str)
            n = len(self._enc.encode(blob))
        self._cache[key] = n
        return n


class OfflineCounter:
    """Heuristic ESTIMATE backend -- no network, no key.

    Method (documented so the deliverable can disclose it): serialize each tool
    to its Anthropic ``tools[]`` JSON shape and estimate tokens as
    ``ceil(len(json) / CHARS_PER_TOKEN) + PER_TOOL_OVERHEAD``. Calibrated
    loosely against observed count_tokens output; good to ~10-15%, which is why
    it is never the headline figure.
    """

    authoritative = False
    backend = "offline"
    model = None

    CHARS_PER_TOKEN = 3.8  # JSON with punctuation packs denser than prose
    PER_TOOL_OVERHEAD = 8  # name/schema framing tokens the serialization adds
    BASE_OVERHEAD = 3  # constant request framing, cancels in differences

    def _tool_tokens(self, tool: dict) -> int:
        blob = json.dumps(tool, separators=(",", ":"), default=str)
        return int(len(blob) / self.CHARS_PER_TOKEN) + self.PER_TOOL_OVERHEAD

    def count(self, tools: list[dict]) -> int:
        if not tools:
            return self.BASE_OVERHEAD
        return self.BASE_OVERHEAD + sum(self._tool_tokens(t) for t in tools)


def make_counter(
    backend: str = "offline",
    *,
    model: str | None = None,
    client: Any | None = None,
) -> TokenCounter:
    if backend == "api":
        if not model:
            raise ValueError("api counter requires --model (the client's production model)")
        # Route by vendor: Anthropic counts via count_tokens, OpenAI via local
        # tiktoken. Unknown vendors fall back to Anthropic's API (historical
        # default) so existing behavior is preserved.
        from .vendors import OPENAI, vendor_for

        if vendor_for(model) == OPENAI:
            return TiktokenCounter(model=model)
        return ApiCounter(model=model, client=client)
    if backend == "offline":
        return OfflineCounter()
    raise ValueError(f"unknown token backend {backend!r}")
