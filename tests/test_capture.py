from contextlib import asynccontextmanager
from types import SimpleNamespace

from mcp_xray import connect


class FakeTool:
    def __init__(self, name):
        self.name = name
        self.description = f"{name} tool"
        self.inputSchema = {"type": "object", "properties": {}}


class FakeSession:
    """Simulates a phase-swapped server: design phase until load_model is
    called, then the run phase exposes a different tool set."""

    DESIGN = ["open_session", "get_reference"]
    RUN = ["open_session", "get_item", "run_query"]

    def __init__(self):
        self.loaded = False
        self.calls = []

    async def initialize(self):
        return SimpleNamespace(serverInfo=SimpleNamespace(name="fake"))

    async def call_tool(self, name, args):
        self.calls.append((name, args))
        if name == "open_session":
            self.loaded = True
        return SimpleNamespace(isError=False, content="ok")

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


def test_capture_phase_error_raises():
    class ErrSession(FakeSession):
        async def call_tool(self, name, args):
            return SimpleNamespace(isError=True, content="boom")

    spec = [{"name": "run", "advance": [{"tool": "open_session", "args": {}}]}]
    try:
        connect.capture_phases(_factory(ErrSession()), spec, transport="stdio", source="fake")
        assert False, "expected RuntimeError"
    except RuntimeError as e:
        assert "returned an error" in str(e)
