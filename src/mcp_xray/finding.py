"""The normalized currency of the whole tool.

Every probe -- wrapped or owned -- emits a list of these. ``measurement``
carries numbers only, never prose. Severity is assigned by the grading engine
later, not by the probe (see PLAN S6 / S11).
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any

# Canonical kinds. Probes should use one of these so grading can route them.
KINDS = {
    "token_cost",
    "duplicate",
    "schema_smell",
    "selection_error",
    "distraction",
    "merge_candidate",
    "resource_candidate",
    "jit_candidate",
    "hidden_injector",
    "result_size",
}


@dataclass
class Finding:
    probe: str  # provenance -- which sensor produced this
    kind: str  # one of KINDS
    target: str | list[str]  # tool name(s) the finding is about
    measurement: dict[str, Any]  # raw numbers only -- NO prose recommendation
    severity: float = 0.0  # 0..1, assigned by the engine later, not the probe
    confidence: float = 1.0  # 0..1
    detail: dict[str, Any] = field(default_factory=dict)  # supporting data

    def __post_init__(self) -> None:
        if self.kind not in KINDS:
            # Not fatal -- a new probe may introduce a kind grading ignores --
            # but flag it so typos surface in tests.
            raise ValueError(
                f"unknown finding kind {self.kind!r}; expected one of {sorted(KINDS)}"
            )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Finding":
        return cls(
            probe=d["probe"],
            kind=d["kind"],
            target=d["target"],
            measurement=d.get("measurement", {}),
            severity=d.get("severity", 0.0),
            confidence=d.get("confidence", 1.0),
            detail=d.get("detail", {}),
        )
