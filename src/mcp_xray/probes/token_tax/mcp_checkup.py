"""mcp-checkup adapter (v0.2). Consume per-server/per-tool token costs and
duplicate detection; discard its grades and optimization prose (PLAN S7).

The parse contract assumes ``mcp-checkup --json`` emits:
    {"servers": [{"name": ..., "tools": [{"name", "tokens"}], "duplicates": [[a,b]]}]}
Pinned via tests/contracts/test_mcp_checkup.py against a frozen fixture.
"""

from __future__ import annotations

import json

from ...finding import Finding
from ..base import RunContext
from .base_adapter import SubprocessAdapter


class McpCheckupAdapter(SubprocessAdapter):
    name = "mcp_checkup"
    binary = "mcp-checkup"

    def argv(self, ctx: RunContext) -> list[str]:
        return [self.binary, "--json", "--config", ctx.config_path or ""]

    def parse(self, stdout: str) -> list[Finding]:
        data = json.loads(stdout)
        findings: list[Finding] = []
        for server in data.get("servers", []):
            for tool in server.get("tools", []):
                if "tokens" not in tool:
                    continue
                findings.append(
                    Finding(
                        probe=self.name,
                        kind="token_cost",
                        target=tool["name"],
                        measurement={
                            "scope": "tool",
                            "tokens": int(tool["tokens"]),
                            "authoritative": False,  # external approximation
                            "backend": "mcp-checkup",
                        },
                        confidence=0.6,
                    )
                )
            for pair in server.get("duplicates", []):
                findings.append(
                    Finding(
                        probe=self.name,
                        kind="duplicate",
                        target=list(pair),
                        measurement={"source": "mcp-checkup"},
                        confidence=0.6,
                    )
                )
        return findings
