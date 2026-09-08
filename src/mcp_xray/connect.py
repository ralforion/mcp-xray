"""Transports: collapse any source into a normalized Inventory.

v0.1 ships the two offline-friendly paths -- ``tools-json`` dump and
``stdio`` (live). SSE/HTTP are wired through the same MCP client path and
declared here; they need the optional ``mcp`` extra.
"""

from __future__ import annotations

import contextlib
import json
import shlex
from pathlib import Path

from .inventory import Inventory

# The MCP SDK renamed its wire fields from camelCase to snake_case at 2.0
# (``Tool.inputSchema`` -> ``input_schema``, ``InitializeResult.serverInfo`` ->
# ``server_info``, ``CallToolResult.isError`` -> ``is_error``), and the old
# names are not readable as attributes on the new models. pyproject asks for
# ``mcp>=1.0``, so both shapes reach this module: read whichever the installed
# SDK exposes rather than pinning it back. Under 2.x the camelCase reads were
# not merely wrong, they were silent -- ``getattr(res, "isError", False)``
# turned every failed advance call into a success.
_MISSING = object()


def _field(obj, *names, default=None):
    """First attribute of ``names`` that the object actually has."""
    for name in names:
        value = getattr(obj, name, _MISSING)
        if value is not _MISSING:
            return value
    return default


def from_tools_json(path: str | Path) -> Inventory:
    """Load an offline ``tools/list`` dump.

    Accepts several real-world shapes:
      - a full MCP result:        {"tools": [...], "instructions": "..."}
      - a bare list:              [ {...}, {...} ]
      - a nested result envelope: {"result": {"tools": [...]}}
    """
    p = Path(path)
    data = json.loads(p.read_text())

    instructions = None
    prompts: list[dict] = []
    resources: list[dict] = []
    server_name = None
    server_version = None

    if isinstance(data, dict) and "result" in data and isinstance(data["result"], dict):
        data = data["result"]

    if isinstance(data, list):
        raw_tools = data
    elif isinstance(data, dict):
        raw_tools = data.get("tools", [])
        instructions = data.get("instructions")
        prompts = data.get("prompts", []) or []
        resources = data.get("resources", []) or []
        server_name = data.get("serverName") or data.get("server_name") or data.get("name")
        server_version = data.get("serverVersion") or data.get("server_version") or data.get("version")
    else:
        raise ValueError(f"unrecognized tools-json shape in {p}")

    return Inventory.from_tool_dicts(
        raw_tools,
        server_name=server_name,
        server_version=server_version,
        instructions=instructions,
        prompts=prompts,
        resources=resources,
        transport="tools-json",
        source=str(p),
    )


def _from_mcp_session(make_session, *, transport: str, source: str) -> Inventory:
    """Shared live path. ``make_session`` is an async context manager factory
    yielding an initialized ``mcp.ClientSession``."""
    import asyncio

    async def _go() -> Inventory:
        async with make_session() as session:
            init = await session.initialize()
            server_name = None
            server_version = None
            try:
                info = _field(init, "server_info", "serverInfo")
                server_name = info.name
                server_version = info.version
            except Exception:
                pass

            instructions, prompts, resources = await _gather_extras(session, init)

            tools_resp = await session.list_tools()
            raw_tools = [
                {
                    "name": t.name,
                    "description": t.description or "",
                    "inputSchema": _field(t, "input_schema", "inputSchema") or {},
                }
                for t in tools_resp.tools
            ]

            return Inventory.from_tool_dicts(
                raw_tools,
                server_name=server_name,
                server_version=server_version,
                instructions=instructions,
                prompts=prompts,
                resources=resources,
                transport=transport,
                source=source,
            )

    return asyncio.run(_go())


# Many hosted MCP servers sit behind WAFs (e.g. Google Frontend) that 403 the
# SDK's default ``python-httpx/*`` User-Agent. A browser-like UA gets through;
# the trailing token keeps us honestly identifiable. Override per-call with the
# MCP_XRAY_USER_AGENT env var.
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36 mcp-xray/0.1"
)


def _require_mcp():
    try:
        from mcp import ClientSession  # noqa: F401
    except ImportError as e:  # pragma: no cover - env dependent
        raise RuntimeError(
            "live transport needs the mcp extra: pip install mcp-xray[live]"
        ) from e


def _stdio_factory(command: str):
    _require_mcp()
    from contextlib import asynccontextmanager

    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    argv = shlex.split(command)
    params = StdioServerParameters(command=argv[0], args=argv[1:])

    @asynccontextmanager
    async def make_session():
        async with (
            stdio_client(params) as (read, write),
            ClientSession(read, write) as session,
        ):
            yield session

    return make_session


def _http_factory(url: str, *, sse: bool = False, headers: dict | None = None):
    _require_mcp()
    import os
    from contextlib import asynccontextmanager

    from mcp import ClientSession

    req_headers = {"User-Agent": os.environ.get("MCP_XRAY_USER_AGENT", DEFAULT_USER_AGENT)}
    if headers:
        req_headers.update(headers)

    @asynccontextmanager
    async def make_session():
        if sse:
            from mcp.client.sse import sse_client

            async with (
                sse_client(url, headers=req_headers) as (read, write),
                ClientSession(read, write) as session,
            ):
                yield session
        else:
            from mcp.client import streamable_http as sh

            # Three shapes of this transport are in the wild and pyproject asks
            # for mcp>=1.0, so handle all of them. Late 1.x moved headers off
            # the call and onto a caller-owned httpx client under the new name
            # ``streamable_http_client``; 2.0 then dropped the old
            # ``streamablehttp_client`` entirely and stopped yielding the third,
            # session-id element. Slicing the yielded streams keeps the arity
            # difference from mattering; we never used the session-id callback.
            connect_http = getattr(sh, "streamable_http_client", None)
            if connect_http is None:  # mcp 1.x before the rename
                async with (
                    sh.streamablehttp_client(url, headers=req_headers) as streams,
                    ClientSession(*streams[:2]) as session,
                ):
                    yield session
            else:
                # A client we build is a client we own: streamable_http_client
                # only closes one it created itself.
                async with (
                    sh.create_mcp_http_client(headers=req_headers) as http_client,
                    connect_http(url, http_client=http_client) as streams,
                    ClientSession(*streams[:2]) as session,
                ):
                    yield session

    return make_session


def from_stdio(command: str) -> Inventory:
    """Spawn a local MCP server over stdio and snapshot its surface."""
    return _from_mcp_session(_stdio_factory(command), transport="stdio", source=command)


def from_http(url: str, *, sse: bool = False, headers: dict | None = None) -> Inventory:
    """Connect over streamable HTTP (or SSE). Needs the mcp extra."""
    return _from_mcp_session(
        _http_factory(url, sse=sse, headers=headers),
        transport="sse" if sse else "http",
        source=url,
    )


async def _gather_extras(session, init):
    """Collect the hidden-injector channels - server instructions, prompts, and
    auto resources - from a live session. Shared by the flat and phased paths so
    both measure injectors identically (a phased server can still ship a costly
    instructions blob)."""
    instructions = None
    with contextlib.suppress(Exception):
        instructions = init.instructions  # type: ignore[attr-defined]
    prompts: list[dict] = []
    try:
        pr = await session.list_prompts()
        prompts = [{"name": p.name, "description": p.description or ""} for p in pr.prompts]
    except Exception:
        pass
    resources: list[dict] = []
    try:
        rr = await session.list_resources()
        resources = [{"uri": str(r.uri), "name": r.name or ""} for r in rr.resources]
    except Exception:
        pass
    return instructions, prompts, resources


def _inventory_from_list_tools(
    tools_resp, *, transport: str, source: str, server_name=None, server_version=None,
    instructions=None, prompts=None, resources=None,
) -> Inventory:
    raw = [
        {
            "name": t.name,
            "description": t.description or "",
            "inputSchema": _field(t, "input_schema", "inputSchema") or {},
        }
        for t in tools_resp.tools
    ]
    return Inventory.from_tool_dicts(
        raw, server_name=server_name, server_version=server_version,
        instructions=instructions, prompts=prompts or [], resources=resources or [],
        transport=transport, source=source,
    )


def capture_phases(make_session, spec: list[dict], *, transport: str, source: str) -> dict[str, Inventory]:
    """Walk a phase-swapped server in ONE session, snapshotting the tool list at
    each phase. ``spec`` is a list of ``{name, advance?}`` -- the first phase is
    captured before any call; each later phase first issues its ``advance`` tool
    calls (e.g. ``load_model``), then re-lists.

    Tool calls are made ONLY as declared in ``spec`` (the operator-supplied
    advance manifest) -- never inferred (PLAN S5: no call without a manifest)."""
    import asyncio

    async def _go() -> dict[str, Inventory]:
        out: dict[str, Inventory] = {}
        async with make_session() as session:
            init = await session.initialize()
            sname = sver = None
            try:
                info = _field(init, "server_info", "serverInfo")
                sname = info.name
                sver = info.version
            except Exception:
                pass
            # Injector channels are server-level (same across phases) - gather
            # once so each phase inventory reports them (PLAN S8).
            instructions, prompts, resources = await _gather_extras(session, init)
            for phase in spec:
                name = phase["name"]
                for call in phase.get("advance", []) or []:
                    res = await session.call_tool(call["tool"], call.get("args", {}) or {})
                    if _field(res, "is_error", "isError", default=False):
                        raise RuntimeError(
                            f"advance call {call['tool']}({call.get('args', {})}) for phase "
                            f"'{name}' returned an error: {getattr(res, 'content', '')}"
                        )
                tr = await session.list_tools()
                out[name] = _inventory_from_list_tools(
                    tr, transport=transport, source=f"{source}#{name}",
                    server_name=sname, server_version=sver,
                    instructions=instructions, prompts=prompts, resources=resources,
                )
        return out

    return asyncio.run(_go())


def _serialize_result(res) -> str:
    """Best-effort flatten of a CallToolResult's content into one string, so we
    can measure how much it costs to hand the model this tool's output."""
    parts: list[str] = []
    for block in getattr(res, "content", []) or []:
        text = getattr(block, "text", None)
        if text is not None:
            parts.append(str(text))
            continue
        # Non-text blocks (image/resource): fall back to their serialized form.
        try:
            parts.append(json.dumps(block, default=str))
        except Exception:
            parts.append(str(block))
    sc = _field(res, "structured_content", "structuredContent")
    if sc is not None:
        try:
            parts.append(json.dumps(sc, default=str))
        except Exception:
            parts.append(str(sc))
    return "".join(parts)


def measure_result_sizes(make_session, calls: list[dict], *, transport: str, source: str) -> list[dict]:
    """Call each operator-confirmed tool ONCE and measure its result size.

    ``calls`` is the --call-manifest list of ``{tool, args}``. Tool outputs are
    returned to the model and cost context, so this measures chars + utf-8 bytes
    of each result. Calls are made ONLY as the manifest declares (PLAN S5: no
    call without a manifest). One failed call doesn't abort the rest.
    """
    import asyncio

    async def _go() -> list[dict]:
        out: list[dict] = []
        async with make_session() as session:
            await session.initialize()
            for call in calls:
                tool = call.get("tool")
                args = call.get("args", {}) or {}
                rec: dict = {"tool": tool, "args": args}
                try:
                    res = await session.call_tool(tool, args)
                    if _field(res, "is_error", "isError", default=False):
                        rec["error"] = f"tool returned isError: {_serialize_result(res)[:200]}"
                    else:
                        text = _serialize_result(res)
                        rec["chars"] = len(text)
                        rec["bytes"] = len(text.encode("utf-8"))
                except Exception as e:  # network / protocol / bad args
                    rec["error"] = f"{type(e).__name__}: {e}"
                out.append(rec)
        return out

    return asyncio.run(_go())


def result_sizes_stdio(command: str, calls: list[dict]) -> list[dict]:
    return measure_result_sizes(_stdio_factory(command), calls, transport="stdio", source=command)


def result_sizes_http(
    url: str, calls: list[dict], *, sse: bool = False, headers: dict | None = None
) -> list[dict]:
    return measure_result_sizes(
        _http_factory(url, sse=sse, headers=headers),
        calls, transport="sse" if sse else "http", source=url,
    )


def capture_phases_stdio(command: str, spec: list[dict]) -> dict[str, Inventory]:
    return capture_phases(_stdio_factory(command), spec, transport="stdio", source=command)


def capture_phases_http(
    url: str, spec: list[dict], *, sse: bool = False, headers: dict | None = None
) -> dict[str, Inventory]:
    return capture_phases(
        _http_factory(url, sse=sse, headers=headers),
        spec, transport="sse" if sse else "http", source=url,
    )
