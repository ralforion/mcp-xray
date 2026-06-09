"""The orchestrator (PLAN S4): build a RunContext, discover probes, run those
that can run, collect normalized Findings, and record skips with reasons.

One voice: probes contribute Findings only. Interpretation happens in grade.py.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .counting import make_counter
from .finding import Finding
from .inventory import Inventory
from .phases import PhasedSurface
from .probes.base import Probe, RunContext
from .probes.consolidate import ConsolidateProbe
from .probes.noise import NoiseProbe
from .probes.static_hygiene import StaticHygieneProbe
from .probes.token_tax.mcp_checkup import McpCheckupAdapter
from .probes.token_tax.token_analyzer import TokenAnalyzerAdapter


def default_probes() -> list[Probe]:
    """Owned probes first (they are the report), wrapped sensors after."""
    return [
        StaticHygieneProbe(),
        ConsolidateProbe(),
        NoiseProbe(),
        McpCheckupAdapter(),
        TokenAnalyzerAdapter(),
    ]


@dataclass
class RunResult:
    inventory: Inventory
    findings: list[Finding] = field(default_factory=list)
    ran: list[str] = field(default_factory=list)
    skipped: list[dict] = field(default_factory=list)  # {probe, missing}
    context: dict = field(default_factory=dict)  # backend, model, fingerprint...


def build_context(
    inventory: Inventory,
    *,
    token_backend: str = "offline",
    model: str | None = None,
    chat_client=None,
    queries: list[dict] | None = None,
    call_manifest: list[dict] | None = None,
    config_path: str | None = None,
    live: bool = False,
    config: dict | None = None,
    tool_phases: dict[str, set[str]] | None = None,
) -> RunContext:
    # Counter builds its own SDK/encoder per vendor; the chat client is a
    # separate ChatClient used only by the behavioral probe.
    counter = make_counter(token_backend, model=model)

    available: set[str] = {"inventory"}
    if live:
        available.add("live_server")
    if chat_client is not None:
        available.update({"api_key", "llm"})
    if queries:
        available.add("queries")
    if call_manifest:
        available.add("call_manifest")
    if config_path:
        available.add("config_path")

    return RunContext(
        inventory=inventory,
        counter=counter,
        available=available,
        model=model,
        client=chat_client,
        queries=queries,
        call_manifest=call_manifest,
        config_path=config_path,
        config=config or {},
        tool_phases=tool_phases,
    )


def run(
    inventory: Inventory,
    *,
    probes: list[Probe] | None = None,
    **ctx_kwargs,
) -> RunResult:
    probes = probes if probes is not None else default_probes()
    ctx = build_context(inventory, **ctx_kwargs)

    result = RunResult(
        inventory=inventory,
        context={
            "token_backend": ctx.counter.backend,
            "authoritative_tokens": ctx.counter.authoritative,
            "model": ctx.model,
            "fingerprint": inventory.fingerprint(),
            "transport": inventory.transport,
            "source": inventory.source,
            "available": sorted(ctx.available),
            "noise_samples": ctx.config.get("noise_samples"),
        },
    )

    for probe in probes:
        if not probe.can_run(ctx):
            result.skipped.append(
                {"probe": probe.name, "missing": sorted(probe.missing(ctx))}
            )
            continue
        findings = probe.run(ctx)
        result.findings.extend(findings)
        result.ran.append(probe.name)

    return result


def run_phased(
    phased: PhasedSurface,
    *,
    probes: list[Probe] | None = None,
    token_backend: str = "offline",
    model: str | None = None,
    chat_client=None,
    **ctx_kwargs,
) -> RunResult:
    """Audit a server whose tool list swaps by journey phase.

    Quality analysis (per-tool cost, schema smells, merges, resources) runs on
    the *union* of all phases -- every distinct tool gets reviewed. But the
    headline context tax is the *worst phase* (what the model actually carries
    per turn), not the union it never co-loads. The JIT finding flips from
    "recommend" to "already progressive -- credited."
    """
    # 1) standard probes over the union surface (phase map lets the consolidate
    #    probe avoid merging tools that aren't surfaced in the same phases)
    result = run(
        phased.union,
        probes=probes,
        token_backend=token_backend,
        model=model,
        chat_client=chat_client,
        tool_phases=phased.tool_phases,
        **ctx_kwargs,
    )

    # 2) per-phase surface tax (same backend so numbers reconcile with per-tool)
    counter = make_counter(token_backend, model=model)
    base = counter.count([])
    phase_costs: dict[str, int] = {}
    for name, inv in phased.phases.items():
        api = [t.to_api_dict() for t in inv.tools]
        phase_costs[name] = counter.count(api) - base

    # annotate per-tool findings with the phases each tool lives in
    for f in result.findings:
        if f.kind == "token_cost" and f.measurement.get("scope") == "tool" and isinstance(f.target, str):
            f.measurement["phases"] = sorted(phased.tool_phases.get(f.target, []))

    # 3) headline = worst phase (max per-turn tax), replacing the union surface
    result.findings = [
        f for f in result.findings
        if not (f.kind == "token_cost" and f.measurement.get("scope") == "surface")
    ]
    worst = max(phase_costs, key=lambda k: phase_costs[k]) if phase_costs else None
    if worst is not None:
        wc, wtools = phase_costs[worst], phased.phases[worst].tools
        result.findings.insert(
            0,
            Finding(
                probe="phase_surface",
                kind="token_cost",
                target=sorted(t.name for t in wtools),
                measurement={
                    "scope": "surface",
                    "tokens": wc,
                    "tool_count": len(wtools),
                    "tokens_per_tool_avg": round(wc / max(1, len(wtools)), 1),
                    "authoritative": counter.authoritative,
                    "backend": counter.backend,
                    "model": counter.model,
                    "phased": True,
                    "worst_phase": worst,
                },
                confidence=1.0 if counter.authoritative else 0.7,
                detail={"phases": phase_costs, "note": "per-turn tax = worst phase; union never co-loaded"},
            ),
        )

    # one token_cost finding per phase (rendered as the phase table)
    for name, inv in phased.phases.items():
        result.findings.append(
            Finding(
                probe="phase_surface",
                kind="token_cost",
                target=sorted(inv.names),
                measurement={
                    "scope": "phase",
                    "phase": name,
                    "tokens": phase_costs[name],
                    "tool_count": len(inv.tools),
                    "tokens_per_tool_avg": round(phase_costs[name] / max(1, len(inv.tools)), 1),
                    "authoritative": counter.authoritative,
                    "backend": counter.backend,
                },
                confidence=1.0 if counter.authoritative else 0.7,
            )
        )

    # 4) credit progressive loading instead of the static-surface JIT flag
    result.findings = [f for f in result.findings if f.kind != "jit_candidate"]
    result.findings.append(
        Finding(
            probe="consolidate",
            kind="jit_candidate",
            target=sorted(phased.union.names),
            measurement={
                "tool_count": len(phased.union.tools),
                "phase_count": len(phased.phases),
                "looks_dynamic": True,
                "recommend_jit": False,
                "progressive": True,
            },
            confidence=0.9,
            detail={
                "phase_tool_counts": {n: len(i.tools) for n, i in phased.phases.items()},
                "carried_tools": phased.carried,
                "framing": (
                    "Server already uses progressive (phase-scoped) tool loading: the "
                    "model carries one phase at a time, not the full union. This is the "
                    "recommended pattern -- credited, not flagged."
                ),
            },
        )
    )

    result.context["phased"] = True
    result.context["phases"] = {n: len(i.tools) for n, i in phased.phases.items()}
    result.context["phase_tokens"] = phase_costs
    result.context["carried_tools"] = phased.carried
    result.context["union_tool_count"] = len(phased.union.tools)
    return result
