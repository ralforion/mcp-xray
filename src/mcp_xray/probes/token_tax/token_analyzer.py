"""token-analyzer-mcp adapter (v0.2). Consume per-tool overhead breakdown;
discard prose (PLAN S7).

Assumed ``token-analyzer --format json`` contract:
    {"tools": [{"name", "overhead_tokens", "schema_tokens", "description_tokens"}]}
Pinned via tests/contracts/test_token_analyzer.py.
"""

from __future__ import annotations

import json

from ...finding import Finding
from ..base import RunContext
from .base_adapter import SubprocessAdapter


class TokenAnalyzerAdapter(SubprocessAdapter):
    name = "token_analyzer"
    binary = "token-analyzer"

    def argv(self, ctx: RunContext) -> list[str]:
        return [self.binary, "--format", "json", "--config", ctx.config_path or ""]

    def parse(self, stdout: str) -> list[Finding]:
        data = json.loads(stdout)
        findings: list[Finding] = []
        for tool in data.get("tools", []):
            if "overhead_tokens" not in tool:
                continue
            findings.append(
                Finding(
                    probe=self.name,
                    kind="token_cost",
                    target=tool["name"],
                    measurement={
                        "scope": "tool",
                        "tokens": int(tool["overhead_tokens"]),
                        "authoritative": False,
                        "backend": "token-analyzer",
                    },
                    confidence=0.6,
                    detail={
                        "schema_tokens": tool.get("schema_tokens"),
                        "description_tokens": tool.get("description_tokens"),
                    },
                )
            )
        return findings
