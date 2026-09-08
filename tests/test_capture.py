from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import ClassVar

from mcp_xray import connect

# These fakes mirror the mcp>=2 SDK, which names its wire fields in snake_case
# (input_schema / server_info / is_error). Reading the 1.x camelCase names here
# would be silently wrong rather than loud, so the shape is worth stating: see
# the version guard in connect._require_mcp.


class FakeTool:
    def __init__(self, name):
        self.name = name
        self.description = f"{name} tool"
        self.input_schema = {"type": "object", "properties": {}}


class FakeSession:
    """Simulates a phase-swapped server: design phase until load_model is
    called, then the run phase exposes a different tool set."""

    DESIGN: ClassVar[list[str]] = ["open_session", "get_reference"]
    RUN: ClassVar[list[str]] = ["open_session", "get_item", "run_query"]

    def __init__(self):
        self.loaded = False
        self.calls = []

    async def initialize(self):
        return SimpleNamespace(server_info=SimpleNamespace(name="fake", version="1.2.3"))

    async def call_tool(self, name, args):
        self.calls.append((name, args))
        if name == "open_session":
            self.loaded = True
        return SimpleNamespace(is_error=False, content="ok")

    async def list_tools(self):
        names = self.RUN if self.loaded else self.DESIGN
        return SimpleNamespace(tools=[FakeTool(n) for n in names])


def _factory(session):
    @asynccontextmanager
    async def make_session():
        yield session

    return make_session


def test_capture_phases_swaps_tools():
    session = FakeSession()
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


def test_capture_phases_reads_server_identity():
    """server_info names the run folder and carries drift detection, and the read
    sits behind a bare except, so losing it has to fail here or not at all."""
    out = connect.capture_phases(
        _factory(FakeSession()), [{"name": "design"}], transport="stdio", source="fake"
    )
    assert out["design"].server_name == "fake"
    assert out["design"].server_version == "1.2.3"


def test_capture_phases_reads_tool_schema():
    out = connect.capture_phases(
        _factory(FakeSession()), [{"name": "design"}], transport="stdio", source="fake"
    )
    assert all(t.input_schema == {"type": "object", "properties": {}} for t in out["design"].tools)


def test_capture_phase_error_raises():
    class ErrSession(FakeSession):
        async def call_tool(self, name, args):
            return SimpleNamespace(is_error=True, content="boom")

    spec = [{"name": "run", "advance": [{"tool": "open_session", "args": {}}]}]
    try:
        connect.capture_phases(_factory(ErrSession()), spec, transport="stdio", source="fake")
        raise AssertionError("expected RuntimeError")
    except RuntimeError as e:
        assert "returned an error" in str(e)


def test_measure_result_sizes_flags_tool_errors():
    """A failed call must be recorded as an error, not measured as a result."""
    session = FakeSession()

    async def failing(name, args):
        return SimpleNamespace(is_error=True, content=[SimpleNamespace(text="boom")])

    session.call_tool = failing
    out = connect.measure_result_sizes(
        _factory(session), [{"tool": "run_query", "args": {}}], transport="stdio", source="fake"
    )
    assert "error" in out[0] and "chars" not in out[0]
