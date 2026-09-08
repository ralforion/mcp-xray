from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import ClassVar

import pytest

from mcp_xray import connect

# The MCP SDK renamed these wire fields at 2.0 and does not keep the old names
# readable. Every fake here is built in both shapes and every test runs against
# both, so a reader of one shape only cannot pass CI (see connect._field).
SDK_SHAPES = {
    "camel": {"tool_schema": "inputSchema", "server_info": "serverInfo", "is_error": "isError"},
    "snake": {"tool_schema": "input_schema", "server_info": "server_info", "is_error": "is_error"},
}


@pytest.fixture(params=sorted(SDK_SHAPES), ids=sorted(SDK_SHAPES))
def shape(request):
    """One field-naming shape of the MCP SDK: mcp 1.x camelCase or 2.x snake."""
    return SDK_SHAPES[request.param]


class FakeTool:
    def __init__(self, name, shape):
        self.name = name
        self.description = f"{name} tool"
        setattr(self, shape["tool_schema"], {"type": "object", "properties": {}})


class FakeSession:
    """Simulates a phase-swapped server: design phase until load_model is
    called, then the run phase exposes a different tool set."""

    DESIGN: ClassVar[list[str]] = ["open_session", "get_reference"]
    RUN: ClassVar[list[str]] = ["open_session", "get_item", "run_query"]

    def __init__(self, shape):
        self.shape = shape
        self.loaded = False
        self.calls = []

    async def initialize(self):
        info = SimpleNamespace(name="fake", version="1.2.3")
        return SimpleNamespace(**{self.shape["server_info"]: info})

    async def call_tool(self, name, args):
        self.calls.append((name, args))
        if name == "open_session":
            self.loaded = True
        return SimpleNamespace(**{self.shape["is_error"]: False}, content="ok")

    async def list_tools(self):
        names = self.RUN if self.loaded else self.DESIGN
        return SimpleNamespace(tools=[FakeTool(n, self.shape) for n in names])


def _factory(session):
    @asynccontextmanager
    async def make_session():
        yield session

    return make_session


def test_capture_phases_swaps_tools(shape):
    session = FakeSession(shape)
    spec = [
        {"name": "design"},
        {"name": "run", "advance": [{"tool": "open_session", "args": {"session_id": "s1"}}]},
    ]
    out = connect.capture_phases(_factory(session), spec, transport="stdio", source="fake")
    assert set(out) == {"design", "run"}
    assert out["design"].names == ["open_session", "get_reference"]
    assert out["run"].names == ["open_session", "get_item", "run_query"]
    # the advance call was made exactly once, with its args
    assert session.calls == [("open_session", {"session_id": "s1"})]


def test_capture_phases_reads_server_identity(shape):
    """serverInfo names the run folder and carries drift detection, so losing it
    to an attribute rename has to fail here rather than degrade quietly."""
    out = connect.capture_phases(
        _factory(FakeSession(shape)), [{"name": "design"}], transport="stdio", source="fake"
    )
    assert out["design"].server_name == "fake"
    assert out["design"].server_version == "1.2.3"


def test_capture_phases_reads_tool_schema(shape):
    out = connect.capture_phases(
        _factory(FakeSession(shape)), [{"name": "design"}], transport="stdio", source="fake"
    )
    assert all(t.input_schema == {"type": "object", "properties": {}} for t in out["design"].tools)


def test_capture_phase_error_raises(shape):
    class ErrSession(FakeSession):
        async def call_tool(self, name, args):
            return SimpleNamespace(**{self.shape["is_error"]: True}, content="boom")

    spec = [{"name": "run", "advance": [{"tool": "open_session", "args": {}}]}]
    try:
        connect.capture_phases(_factory(ErrSession(shape)), spec, transport="stdio", source="fake")
        raise AssertionError("expected RuntimeError")
    except RuntimeError as e:
        assert "returned an error" in str(e)


def test_measure_result_sizes_flags_tool_errors(shape):
    """A failed call must be recorded as an error, not measured as a result."""
    session = FakeSession(shape)

    async def failing(name, args):
        return SimpleNamespace(**{shape["is_error"]: True}, content=[SimpleNamespace(text="boom")])

    session.call_tool = failing
    out = connect.measure_result_sizes(
        _factory(session), [{"tool": "run_query", "args": {}}], transport="stdio", source="fake"
    )
    assert "error" in out[0] and "chars" not in out[0]
