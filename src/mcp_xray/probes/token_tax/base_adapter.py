"""Shared subprocess-adapter machinery for wrapped token-tax sensors.

Adapters shell out to an external tool, parse its *structured* output, and emit
token_cost / duplicate Findings -- nothing else. Recommendation text is dropped.
The parse step is pure (str -> findings) so a frozen fixture pins the contract
in tests/contracts/ and a silent upstream format change fails our CI, not the
client meeting (PLAN S7).
"""

from __future__ import annotations

import shutil
import subprocess
from abc import abstractmethod

from ...finding import Finding
from ..base import Probe, RunContext


class SubprocessAdapter(Probe):
    binary: str = ""  # the external executable name
    name = "wrapped"

    def requires(self) -> set[str]:
        # Most config scanners need the client config path; live probes need a
        # running server. Subclasses narrow this.
        return {"config_path"}

    def can_run(self, ctx: RunContext) -> bool:
        if not super().can_run(ctx):
            return False
        return shutil.which(self.binary) is not None

    @abstractmethod
    def argv(self, ctx: RunContext) -> list[str]:
        """Command to run."""

    @abstractmethod
    def parse(self, stdout: str) -> list[Finding]:
        """Pure parse: tool output -> normalized Findings (measurements only)."""

    def run(self, ctx: RunContext) -> list[Finding]:
        try:
            proc = subprocess.run(
                self.argv(ctx),
                capture_output=True,
                text=True,
                timeout=ctx.config.get("adapter_timeout", 120),
            )
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return []
        if proc.returncode != 0:
            return []
        try:
            return self.parse(proc.stdout)
        except Exception:
            # A parse failure is a contract break -- surfaced by the contract
            # test in CI. At runtime we degrade to "not measured."
            return []
