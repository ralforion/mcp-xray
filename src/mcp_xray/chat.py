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


# Output budget for one probe call (a tool pick or a yes/no answer). Thinking
# counts against max_tokens, and on models that always think (Claude Opus 5.5,
# Sonnet 5.5, Fable 5.1) a small cap can run out before the call or the answer
# is written. Only generated tokens are billed.
_MAX_TOKENS = 4096

# Appended to the system prompt when the API can't force a call (see pick_tool).
_REQUIRE_CALL = (
    "This request requires a tool call, even if no tool fits perfectly: "
    "call exactly one, the tool that fits best."
)


def _rejects_forced_choice(err: Exception) -> bool:
    """True for the 400 a model without forced tool use returns for
    ``tool_choice`` ``any``/``tool`` ('tool_choice: type "tool" and "any" are
    not supported for this model.'). Duck-typed so it needs no SDK import; any
    other 400 is a real error and must still propagate."""
    return getattr(err, "status_code", None) == 400 and "tool_choice" in str(err)


class AnthropicChat:
    """Anthropic Messages API. ``allow_none`` -> tool_choice auto (may decline);
    otherwise ``any`` (must call exactly one). Models that reject ``any``
    (Claude Opus 5.5, Sonnet 5.5, Fable 5.1) get ``auto`` with the call required
    in the system prompt instead; the first rejected request detects this and
    ``forces_choice`` remembers it for the client's lifetime."""

    backend = ANTHROPIC

    def __init__(self, model: str, client: Any | None = None) -> None:
        self.model = model
        if client is None:
            import anthropic  # imported lazily so the offline path needs no SDK

            client = anthropic.Anthropic()
        self._client = client
        self.forces_choice = True

    def _pick(self, tools: list[dict], system: str, query: str, tool_choice: dict) -> str | None:
        resp = self._client.messages.create(
            model=self.model,
            max_tokens=_MAX_TOKENS,
            system=system,
            tools=tools,  # Anthropic consumes the neutral shape directly
            tool_choice=tool_choice,
            messages=[{"role": "user", "content": query}],
        )
        for block in getattr(resp, "content", []) or []:
            if getattr(block, "type", None) == "tool_use":
                return block.name
        if getattr(resp, "stop_reason", None) == "max_tokens":
            # Cut off before any call: not a decision, so it must not be read
            # as "no tool chosen" (a clean decline or a wrong pick).
            raise RuntimeError(f"{self.model} hit max_tokens before choosing a tool")
        return None  # model legitimately called no tool

    def pick_tool(self, tools: list[dict], system: str, query: str, *, allow_none: bool) -> str | None:
        # API errors propagate to the caller - a failed call is NOT a model
        # decision and must not be silently read as "no tool chosen."
        if allow_none:
            return self._pick(tools, system, query, {"type": "auto"})
        if self.forces_choice:
            try:
                return self._pick(tools, system, query, {"type": "any"})
            except Exception as e:
                if not _rejects_forced_choice(e):
                    raise
                self.forces_choice = False
        # No forced tool use on this model: require the call in the prompt and,
        # since auto doesn't guarantee one, ask once more if none was made.
        steered = f"{system}\n\n{_REQUIRE_CALL}"
        for _ in range(2):
            pick = self._pick(tools, steered, query, {"type": "auto"})
            if pick is not None:
                return pick
        return None  # declined twice even when told a call is required

    def ask_yes_no(self, system: str, query: str) -> bool | None:
        # API errors propagate; None means only "answer wasn't yes/no."
        resp = self._client.messages.create(
            model=self.model,
            max_tokens=_MAX_TOKENS,
            system=system,
            messages=[{"role": "user", "content": query}],
        )
        text = "".join(
            getattr(b, "text", "") for b in getattr(resp, "content", []) or []
            if getattr(b, "type", None) == "text"
        )
        if not text and getattr(resp, "stop_reason", None) == "max_tokens":
            # Cut off before any answer: a failed call, not an answer that
            # "wasn't yes/no."
            raise RuntimeError(f"{self.model} hit max_tokens before answering")
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
