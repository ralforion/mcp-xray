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
