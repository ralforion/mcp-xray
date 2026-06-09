"""The probe contract (PLAN S6) -- load-bearing.

Every sensor implements one interface and emits one shape. ``requires()``
declares capability tokens; the orchestrator runs only probes whose needs are
met and reports the rest as "not measured," never as zero.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from ..finding import Finding
from ..inventory import Inventory

# Capability tokens a probe may require. The orchestrator populates the set of
# available ones from the run configuration.
#   live_server   -- an actual connected server (for calling tools)
#   inventory     -- a tool inventory exists (always true once connected/loaded)
#   api_key       -- an Anthropic key is configured
#   llm           -- an LLM is usable (implies api_key for this tool)
#   config_path   -- a client MCP config file path was supplied
#   call_manifest -- operator-confirmed safe calls supplied
#   queries       -- labeled golden queries supplied
CAPABILITIES = {
    "live_server",
    "inventory",
    "api_key",
    "llm",
    "config_path",
    "call_manifest",
    "queries",
}


@dataclass
class RunContext:
    """Everything a probe might need; probes take only what they ``requires()``."""

    inventory: Inventory
    counter: Any  # TokenCounter
    available: set[str] = field(default_factory=set)
    model: str | None = None
    client: Any | None = None  # anthropic client, if api_key present
    queries: list[dict] | None = None  # labeled golden queries
    call_manifest: list[dict] | None = None  # operator-confirmed safe calls
    config_path: str | None = None
    config: dict = field(default_factory=dict)  # misc tuning knobs / thresholds
    tool_phases: dict[str, set[str]] | None = None  # tool -> phases (phased runs only)


class Probe(ABC):
    name: str = "probe"

    @abstractmethod
    def requires(self) -> set[str]:
        """Capability tokens this probe needs to run."""

    def can_run(self, ctx: RunContext) -> bool:
        return self.requires() <= ctx.available

    def missing(self, ctx: RunContext) -> set[str]:
        return self.requires() - ctx.available

    @abstractmethod
    def run(self, ctx: RunContext) -> list[Finding]:
        """Produce findings. Numbers only -- never set severity here."""
