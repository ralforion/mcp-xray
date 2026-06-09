"""Vendor-agnostic chat client for the behavioral probe (PLAN S9, option B).

The noise probe needs exactly one capability: "given these tools and a prompt,
which tool - if any - does the model call?" Each vendor's SDK and tool-call
format differs, so this module hides them behind ``ChatClient.pick_tool`` and
returns a plain tool name (or ``None``). ``make_chat_client`` routes by model id.

Tools arrive in the neutral Anthropic ``{name, description, input_schema}``
shape (``Tool.to_api_dict()``); each client converts to its own wire format.
"""

from __future__ import annotations

from typing import Any, Protocol

from .vendors import ANTHROPIC, OPENAI, vendor_for


class ChatClient(Protocol):
    backend: str
    model: str

    def pick_tool(
        self, tools: list[dict], system: str, query: str, *, allow_none: bool
    ) -> str | None: ...

    def ask_yes_no(self, system: str, query: str) -> bool | None: ...


def _parse_yes_no(text: str) -> bool | None:
    """Map a free-text answer to True/False, or None if it's neither."""
    t = (text or "").strip().lower()
    if t.startswith("yes"):
        return True
    if t.startswith("no"):
        return False
    return None


class AnthropicChat:
    """Anthropic Messages API. ``allow_none`` -> tool_choice auto (may decline);
    otherwise ``any`` (must call exactly one)."""

    backend = ANTHROPIC

    def __init__(self, model: str, client: Any | None = None) -> None:
        self.model = model
        if client is None:
            import anthropic  # imported lazily so the offline path needs no SDK

            client = anthropic.Anthropic()
        self._client = client

    def pick_tool(self, tools: list[dict], system: str, query: str, *, allow_none: bool) -> str | None:
        # API errors propagate to the caller - a failed call is NOT a model
        # decision and must not be silently read as "no tool chosen."
        resp = self._client.messages.create(
            model=self.model,
            max_tokens=256,
            system=system,
            tools=tools,  # Anthropic consumes the neutral shape directly
            tool_choice={"type": "auto"} if allow_none else {"type": "any"},
            messages=[{"role": "user", "content": query}],
        )
        for block in getattr(resp, "content", []) or []:
            if getattr(block, "type", None) == "tool_use":
                return block.name
        return None  # model legitimately called no tool

    def ask_yes_no(self, system: str, query: str) -> bool | None:
        # API errors propagate; None means only "answer wasn't yes/no."
        resp = self._client.messages.create(
            model=self.model,
            max_tokens=8,
            system=system,
            messages=[{"role": "user", "content": query}],
        )
        text = "".join(
            getattr(b, "text", "") for b in getattr(resp, "content", []) or []
            if getattr(b, "type", None) == "text"
        )
        return _parse_yes_no(text)


class OpenAIChat:
    """OpenAI Chat Completions API. ``allow_none`` -> tool_choice 'auto' (may
    decline); otherwise 'required' (must call a tool)."""

    backend = OPENAI

    def __init__(self, model: str, client: Any | None = None) -> None:
        self.model = model
        if client is None:
            import openai  # lazy import

            client = openai.OpenAI()
        self._client = client

    @staticmethod
    def _to_functions(tools: list[dict]) -> list[dict]:
        return [
            {
                "type": "function",
                "function": {
                    "name": t["name"],
                    "description": t.get("description", "") or "",
                    "parameters": t.get("input_schema") or {"type": "object", "properties": {}},
                },
            }
            for t in tools
        ]

    def pick_tool(self, tools: list[dict], system: str, query: str, *, allow_none: bool) -> str | None:
        # API errors propagate to the caller (see AnthropicChat.pick_tool).
        resp = self._client.chat.completions.create(
            model=self.model,
            tools=self._to_functions(tools),
            tool_choice="auto" if allow_none else "required",
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": query},
            ],
        )
        choices = getattr(resp, "choices", None) or []
        if not choices:
            return None
        calls = getattr(choices[0].message, "tool_calls", None) or []
        if calls:
            return calls[0].function.name
        return None

    def ask_yes_no(self, system: str, query: str) -> bool | None:
        # API errors propagate; None means only "answer wasn't yes/no."
        resp = self._client.chat.completions.create(
            model=self.model,
            max_tokens=8,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": query},
            ],
        )
        choices = getattr(resp, "choices", None) or []
        if not choices:
            return None
        return _parse_yes_no(getattr(choices[0].message, "content", "") or "")


def make_chat_client(model: str, client: Any | None = None) -> ChatClient:
    """Build the chat client for ``model``'s vendor. Raises for unknown vendors
    so the caller can fall back to no behavioral probe."""
    vendor = vendor_for(model)
    if vendor == ANTHROPIC:
        return AnthropicChat(model, client)
    if vendor == OPENAI:
        return OpenAIChat(model, client)
    raise ValueError(f"no chat client for model {model!r} (unknown vendor)")
