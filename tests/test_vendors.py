"""Vendor routing: model -> tokenizer / chat client / pricing (option B)."""

import pytest

from mcp_xray import vendors
from mcp_xray.chat import AnthropicChat, OpenAIChat, make_chat_client
from mcp_xray.counting import ApiCounter, TiktokenCounter, make_counter


def test_vendor_for():
    assert vendors.vendor_for("claude-opus-4-8") == vendors.ANTHROPIC
    assert vendors.vendor_for("claude-opus-4-8[1m]") == vendors.ANTHROPIC
    assert vendors.vendor_for("gpt-5.5") == vendors.OPENAI
    assert vendors.vendor_for("gpt-4o-mini") == vendors.OPENAI
    assert vendors.vendor_for("o3") == vendors.OPENAI
    assert vendors.vendor_for("mistral-large") == vendors.UNKNOWN
    assert vendors.vendor_for(None) == vendors.UNKNOWN


def test_make_counter_routes_openai_to_tiktoken():
    counter = make_counter("api", model="gpt-4o-mini")
    assert isinstance(counter, TiktokenCounter)
    assert counter.authoritative is True
    assert counter.backend == "tiktoken"


def test_make_counter_routes_anthropic_to_api():
    # No network/SDK call at construction beyond client creation; ApiCounter
    # builds its own client lazily, so just assert the type via a fake client.
    counter = make_counter("api", model="claude-opus-4-8", client=object())
    assert isinstance(counter, ApiCounter)


def test_api_counter_defaults_missing_schema_type():
    # A tool with an empty/typeless input_schema must be coerced to a valid
    # object schema before hitting count_tokens (the API 400s otherwise).
    class _FakeMessages:
        def __init__(self):
            self.last_tools = None

        def count_tokens(self, **kwargs):
            self.last_tools = kwargs.get("tools")
            return type("R", (), {"input_tokens": 7})()

    class _FakeClient:
        def __init__(self):
            self.messages = _FakeMessages()

    client = _FakeClient()
    counter = ApiCounter("claude-opus-4-8", client=client)
    counter.count([{"name": "__instructions__", "description": "x", "input_schema": {}}])
    assert client.messages.last_tools[0]["input_schema"] == {"type": "object"}


def test_tiktoken_counts_are_additive_and_nonzero():
    counter = TiktokenCounter("gpt-4o-mini")
    tools = [
        {"name": "alpha", "description": "do alpha things", "input_schema": {"type": "object", "properties": {}}},
        {"name": "beta", "description": "do beta things", "input_schema": {"type": "object", "properties": {}}},
    ]
    base = counter.count([])
    one = counter.count(tools[:1])
    two = counter.count(tools)
    assert base >= 0
    assert one > base
    assert two > one  # adding a tool adds tokens (leave-one-out is meaningful)


def test_tiktoken_unknown_model_falls_back_to_o200k():
    # A made-up id tiktoken can't map should still construct via o200k_base.
    counter = TiktokenCounter("gpt-5.5")
    assert counter.count([]) >= 0


def test_make_chat_client_routes_by_vendor():
    # Pass a dummy client so no SDK/key is needed at construction.
    assert isinstance(make_chat_client("claude-opus-4-8", client=object()), AnthropicChat)
    assert isinstance(make_chat_client("gpt-5.5", client=object()), OpenAIChat)
    with pytest.raises(ValueError):
        make_chat_client("mistral-large", client=object())


def test_openai_chat_converts_tools_to_function_specs():
    specs = OpenAIChat._to_functions(
        [{"name": "x", "description": "d", "input_schema": {"type": "object", "properties": {"a": {"type": "string"}}}}]
    )
    assert specs[0]["type"] == "function"
    assert specs[0]["function"]["name"] == "x"
    assert specs[0]["function"]["parameters"]["properties"] == {"a": {"type": "string"}}


class _Resp:
    def __init__(self, tool=None, stop_reason=None):
        self.content = [type("B", (), {"type": "tool_use", "name": tool})()] if tool else []
        self.stop_reason = stop_reason or ("tool_use" if tool else "end_turn")


class _ApiError(Exception):
    """Stands in for anthropic.BadRequestError: a status_code and a message."""

    def __init__(self, message, status_code=400):
        super().__init__(message)
        self.status_code = status_code


_FORCED_400 = _ApiError('tool_choice: type "tool" and "any" are not supported for this model.')


class _ScriptedMessages:
    """Replays scripted responses (or raises scripted errors) and records every
    request, so a test can assert what was sent and how many calls were made."""

    def __init__(self, *script):
        self.script = list(script)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _anthropic_chat(*script):
    messages = _ScriptedMessages(*script)
    client = type("C", (), {"messages": messages})()
    return AnthropicChat("claude-x", client=client), messages


_TOOLS = [{"name": "a", "description": "d", "input_schema": {"type": "object"}}]


def test_anthropic_forced_pick_uses_any_where_accepted():
    chat, sent = _anthropic_chat(_Resp("a"))
    assert chat.pick_tool(_TOOLS, "sys", "q", allow_none=False) == "a"
    assert [c["tool_choice"] for c in sent.calls] == [{"type": "any"}]
    assert sent.calls[0]["system"] == "sys"
    assert chat.forces_choice is True


def test_anthropic_falls_back_to_required_auto_when_any_is_rejected():
    chat, sent = _anthropic_chat(_FORCED_400, _Resp("a"), _Resp("a"))
    assert chat.pick_tool(_TOOLS, "sys", "q", allow_none=False) == "a"
    assert [c["tool_choice"] for c in sent.calls] == [{"type": "any"}, {"type": "auto"}]
    assert sent.calls[1]["system"].startswith("sys\n\n")
    assert "requires a tool call" in sent.calls[1]["system"]
    assert chat.forces_choice is False
    # Remembered: the next must-pick goes straight to auto, no second 400.
    assert chat.pick_tool(_TOOLS, "sys", "q2", allow_none=False) == "a"
    assert [c["tool_choice"] for c in sent.calls[2:]] == [{"type": "auto"}]


def test_anthropic_required_auto_retries_once_then_declines():
    chat, sent = _anthropic_chat(_FORCED_400, _Resp(), _Resp("a"))
    assert chat.pick_tool(_TOOLS, "sys", "q", allow_none=False) == "a"
    assert len(sent.calls) == 3

    chat, sent = _anthropic_chat(_FORCED_400, _Resp(), _Resp())
    assert chat.pick_tool(_TOOLS, "sys", "q", allow_none=False) is None
    assert len(sent.calls) == 3  # one retry, not a loop


def test_anthropic_other_errors_still_propagate():
    # A 400 about anything else (e.g. a bad schema) is a real failure, and must
    # not flip the client into auto mode or be read as a decision.
    chat, _ = _anthropic_chat(_ApiError("tools.0.input_schema: invalid"))
    with pytest.raises(_ApiError):
        chat.pick_tool(_TOOLS, "sys", "q", allow_none=False)
    assert chat.forces_choice is True

    chat, _ = _anthropic_chat(_ApiError("rate limited: tool_choice", status_code=429))
    with pytest.raises(_ApiError):
        chat.pick_tool(_TOOLS, "sys", "q", allow_none=False)
    assert chat.forces_choice is True


@pytest.mark.parametrize("allow_none", [True, False])
def test_anthropic_truncated_response_is_an_error_not_a_decline(allow_none):
    # Thinking can use the whole budget before any call is written; that must
    # surface as a failed sample, never as "no tool" or a wrong pick.
    chat, _ = _anthropic_chat(_Resp(stop_reason="max_tokens"))
    with pytest.raises(RuntimeError, match="max_tokens"):
        chat.pick_tool(_TOOLS, "sys", "q", allow_none=allow_none)


def test_anthropic_allow_none_keeps_plain_auto():
    chat, sent = _anthropic_chat(_Resp())
    assert chat.pick_tool(_TOOLS, "sys", "q", allow_none=True) is None
    assert sent.calls[0]["tool_choice"] == {"type": "auto"}
    assert sent.calls[0]["system"] == "sys"


class _TextResp:
    def __init__(self, text, stop_reason="end_turn"):
        # A thinking block first, as models that always think return one (with
        # empty text by default); only the text block carries the answer.
        self.content = [type("B", (), {"type": "thinking", "thinking": ""})()]
        if text:
            self.content.append(type("B", (), {"type": "text", "text": text})())
        self.stop_reason = stop_reason


def test_anthropic_yes_no_leaves_room_for_thinking():
    chat, sent = _anthropic_chat(_TextResp("Yes"))
    assert chat.ask_yes_no("sys", "q") is True
    assert sent.calls[0]["max_tokens"] >= 1024  # was 8: no room to think first


def test_anthropic_yes_no_cut_off_before_answering_is_an_error():
    chat, _ = _anthropic_chat(_TextResp("", stop_reason="max_tokens"))
    with pytest.raises(RuntimeError, match="max_tokens"):
        chat.ask_yes_no("sys", "q")


def test_anthropic_yes_no_reads_a_cut_off_answer_that_started():
    # The answer is in the first word, so a reply truncated after it still counts.
    chat, _ = _anthropic_chat(_TextResp("No, the word", stop_reason="max_tokens"))
    assert chat.ask_yes_no("sys", "q") is False
