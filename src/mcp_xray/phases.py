"""Phase-aware surfaces (PLAN S10.4 / S15 v0.4): some servers swap their tool
list by journey phase rather than exposing one static surface.

A snapshot auditor can't see a swap. This module models a server as a set of
per-phase inventories plus their union, so the report can show what the model
*actually* carries each turn (worst phase) instead of a union it never co-loads
-- and credit progressive loading instead of recommending it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .inventory import Inventory


@dataclass
class PhasedSurface:
    phases: dict[str, Inventory]  # phase name -> that phase's visible tools
    union: Inventory  # all distinct tools across phases
    tool_phases: dict[str, set[str]] = field(default_factory=dict)  # tool -> phases it's in
    server_name: str | None = None

    @property
    def carried(self) -> list[str]:
        """Tools visible in more than one phase -- the cross-phase carried cost."""
        return sorted(n for n, ps in self.tool_phases.items() if len(ps) > 1)

    def exclusive(self) -> dict[str, list[str]]:
        """phase -> tools that appear only in that phase."""
        out: dict[str, list[str]] = {p: [] for p in self.phases}
        for name, ps in self.tool_phases.items():
            if len(ps) == 1:
                out[next(iter(ps))].append(name)
        return {p: sorted(v) for p, v in out.items()}


def build_phased(phase_invs: dict[str, Inventory]) -> PhasedSurface:
    """Fold per-phase inventories into a union + provenance map.

    First occurrence of a tool name wins for the union schema; a differing
    schema under the same name in a later phase is ignored (rare, and the
    per-phase tax still reflects each phase's own serialization)."""
    tool_phases: dict[str, set[str]] = {}
    union_tools: dict[str, object] = {}
    server_name = None
    server_version = None
    # Injector channels are server-level (same across phases); carry the first
    # non-empty onto the union so the hidden-injector probe sees them - it runs
    # on the union, not the per-phase inventories.
    instructions = None
    prompts: list = []
    resources: list = []
    for phase, inv in phase_invs.items():
        server_name = server_name or inv.server_name
        server_version = server_version or inv.server_version
        instructions = instructions or inv.instructions
        prompts = prompts or list(inv.prompts or [])
        resources = resources or list(inv.resources or [])
        for t in inv.tools:
            tool_phases.setdefault(t.name, set()).add(phase)
            union_tools.setdefault(t.name, t)
    union = Inventory(
        tools=list(union_tools.values()),  # type: ignore[arg-type]
        server_name=server_name,
        server_version=server_version,
        instructions=instructions,
        prompts=prompts,
        resources=resources,
        transport="phases",
        source="+".join(phase_invs.keys()),
        dynamic=len(phase_invs) > 1,
    )
    return PhasedSurface(
        phases=phase_invs, union=union, tool_phases=tool_phases, server_name=server_name
    )


def load_phases_manifest(path: str | Path) -> dict[str, Inventory]:
    """Load a phases manifest: ``{phases: {name: tools-json-path}}`` (paths
    relative to the manifest). Also accepts a bare ``{name: path}`` mapping."""
    import yaml

    from . import connect

    p = Path(path)
    data = yaml.safe_load(p.read_text())
    mapping = data.get("phases", data) if isinstance(data, dict) else None
    if not isinstance(mapping, dict) or not mapping:
        raise ValueError(f"phases manifest {p} must map phase-name -> tools-json path")

    out: dict[str, Inventory] = {}
    for name, rel in mapping.items():
        tj = Path(rel)
        if not tj.is_absolute():
            tj = p.parent / tj
        out[str(name)] = connect.from_tools_json(tj)
    return out
