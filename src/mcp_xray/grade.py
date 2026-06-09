"""Grading -- the single voice (PLAN S11). The ONLY place interpretation
happens.

The engine assigns ``severity`` to every Finding, rolls findings into five
weighted dimensions -> 0-100 -> a letter grade. Probes that did not run drop
their dimension's weight and are reported "not measured," never scored zero.
Wrapped-tool recommendation text never enters here -- only their numbers.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .finding import Finding

# --- thresholds (PLAN S11 "vs thresholds"; tunable) ----------------------
GOOD_TOKENS_PER_TOOL = 150
BAD_TOKENS_PER_TOOL = 600
GOOD_SURFACE_TOKENS = 1500
BAD_SURFACE_TOKENS = 12000

SMELL_SEVERITY = {
    "missing_description": 0.9,
    "tiny_description": 0.7,
    "short_description": 0.4,
    "vague_description": 0.45,
    "deep_nesting": 0.6,
    "enum_bloat": 0.55,
    "wide_schema": 0.5,
}
DESC_SMELLS = {"missing_description", "tiny_description", "short_description", "vague_description"}

DIMENSIONS = {
    "context_efficiency": 0.30,
    "surface_redundancy": 0.15,
    "schema_hygiene": 0.15,
    "description_quality": 0.15,
    "selection_robustness": 0.25,
}

# One-line gloss per dimension, surfaced in the report so the deliverable
# explains itself. Kept next to DIMENSIONS so weights and text stay in sync.
DIMENSION_HELP = {
    "context_efficiency": "Tokens the tool surface injects into context every turn, before any work - scored against per-tool and total-surface thresholds. Lower is better.",
    "surface_redundancy": "Overlapping or duplicate tools the model must disambiguate. Merge candidates and duplicate detections lower it.",
    "schema_hygiene": "Structural quality of input schemas - deep nesting, oversized enums, or too many parameters drag it down.",
    "description_quality": "Whether tool descriptions are present, specific, and distinct. Missing/vague descriptions and model confusability lower it.",
    "selection_robustness": "Whether the model picks the right tool and stays quiet on off-domain prompts. Needs the LLM probe; 'not measured' without one.",
}


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def _lerp_score(value: float, good: float, bad: float) -> float:
    """Map value to 0..100 where <=good -> 100, >=bad -> 0 (linear)."""
    if value <= good:
        return 100.0
    if value >= bad:
        return 0.0
    return round(100.0 * (1 - (value - good) / (bad - good)), 1)


# Plain-English grade explainer, surfaced in the report (single source of truth).
GRADE_SCALE_HELP = [
    "The **overall score** (0–100) is the weighted average of the five scorecard "
    "dimensions, over only the ones actually measured - a skipped probe drops its "
    "weight and is reported \"not measured,\" never counted as zero. So a grade with "
    "selection robustness unmeasured reflects ~75% of the rubric.",
    "**Letter bands:** A = 90–100 (A+ ≥97, A- 90–92), B = 80–89, C = 70–79, "
    "D = 60–69, F = below 60.",
    "A grade is a *relative* read on surface quality, not a pass/fail - the "
    "actionable detail is in the findings below (per-tool cost, consolidation, "
    "behavioral), and every recommendation traces back to one.",
]


def _letter(score: float) -> str:
    table = [
        (97, "A+"), (93, "A"), (90, "A-"),
        (87, "B+"), (83, "B"), (80, "B-"),
        (77, "C+"), (73, "C"), (70, "C-"),
        (67, "D+"), (63, "D"), (60, "D-"),
        (0, "F"),
    ]
    for cutoff, letter in table:
        if score >= cutoff:
            return letter
    return "F"


@dataclass
class Subscore:
    score: float | None  # None == not measured
    measured: bool
    weight: float
    rationale: str
    finding_count: int = 0


@dataclass
class Grade:
    overall: float
    letter: str
    subscores: dict[str, Subscore] = field(default_factory=dict)


class Grader:
    def grade(self, findings: list[Finding], *, ran: list[str]) -> Grade:
        # 1) assign severity in place (engine owns this, not probes)
        for f in findings:
            f.severity = self._severity(f, findings)

        # 2) compute each dimension
        subs: dict[str, Subscore] = {}
        subs["context_efficiency"] = self._context_efficiency(findings)
        subs["surface_redundancy"] = self._surface_redundancy(findings, ran)
        subs["schema_hygiene"] = self._schema_hygiene(findings)
        subs["description_quality"] = self._description_quality(findings, ran)
        subs["selection_robustness"] = self._selection_robustness(findings, ran)

        # 3) weighted average over MEASURED dimensions only (re-normalize)
        measured = {k: s for k, s in subs.items() if s.measured and s.score is not None}
        tot_w = sum(s.weight for s in measured.values()) or 1.0
        overall = round(sum(s.score * s.weight for s in measured.values()) / tot_w, 1)

        return Grade(overall=overall, letter=_letter(overall), subscores=subs)

    # --- severity per finding -------------------------------------------
    def _severity(self, f: Finding, all_findings: list[Finding]) -> float:
        m = f.measurement
        if f.kind == "token_cost":
            if m.get("scope") == "surface":
                per_tool = m.get("tokens_per_tool_avg", 0)
                return round(1 - _lerp_score(per_tool, GOOD_TOKENS_PER_TOOL, BAD_TOKENS_PER_TOOL) / 100, 3)
            # per-tool: severity by share of surface
            return round(_clamp(m.get("share", 0) * 3), 3)
        if f.kind == "schema_smell":
            return SMELL_SEVERITY.get(m.get("smell", ""), 0.4)
        if f.kind == "merge_candidate":
            saved = m.get("tokens_saved_est", 0)
            base = 0.4 + _clamp(saved / 800) * 0.4
            if m.get("wide_union"):
                base *= 0.7  # wide union is a flagged, weaker recommendation
            if m.get("mixes_read_write"):
                base *= 0.6  # blends read+write - riskier merge, don't push it
            return round(_clamp(base), 3)
        if f.kind == "duplicate":
            return 0.6
        if f.kind == "selection_error":
            return round(_clamp(1 - m.get("pass_rate", 1.0)), 3)
        if f.kind == "distraction":
            return round(_clamp(m.get("fire_rate", 0)), 3)
        if f.kind == "hidden_injector":
            toks = m.get("tokens_est", 0)
            return round(_clamp(toks / 1000), 3)
        if f.kind == "resource_candidate":
            return 0.3
        if f.kind == "jit_candidate":
            return 0.5 if m.get("recommend_jit") else 0.1
        return 0.0

    # --- dimensions ------------------------------------------------------
    def _context_efficiency(self, findings: list[Finding]) -> Subscore:
        surface = next(
            (f for f in findings if f.kind == "token_cost" and f.measurement.get("scope") == "surface"),
            None,
        )
        if surface is None:
            return Subscore(None, False, DIMENSIONS["context_efficiency"], "no token data")
        per_tool = surface.measurement.get("tokens_per_tool_avg", 0)
        total = surface.measurement.get("tokens", 0)
        s_pt = _lerp_score(per_tool, GOOD_TOKENS_PER_TOOL, BAD_TOKENS_PER_TOOL)
        s_tot = _lerp_score(total, GOOD_SURFACE_TOKENS, BAD_SURFACE_TOKENS)
        score = round(0.6 * s_pt + 0.4 * s_tot, 1)
        # Only INSTRUCTIONS shave points - they're injected into context every
        # turn (a true per-turn tax). Prompts and resources are lazy: only their
        # metadata listing exists up front and the content is fetched on demand
        # (prompts aren't loaded at all unless invoked), so they're measured and
        # reported but not graded as per-turn cost.
        inj = sum(
            f.measurement.get("tokens_est", 0)
            for f in findings
            if f.kind == "hidden_injector" and f.measurement.get("kind") == "instructions"
        )
        if inj:
            score = round(max(0.0, score - _clamp(inj / 2000) * 10), 1)
        auth = surface.measurement.get("authoritative")
        note = "authoritative" if auth else "ESTIMATE (offline)"
        # Phased servers are scored on the worst phase (what's carried per turn),
        # not the union they never co-load -- name that phase so the row is honest.
        worst_phase = surface.measurement.get("worst_phase")
        phase_lbl = f", worst phase `{worst_phase}`" if worst_phase else ""
        return Subscore(
            score, True, DIMENSIONS["context_efficiency"],
            f"{total} surface tokens, {per_tool}/tool{phase_lbl} ({note})",
            finding_count=1,
        )

    def _surface_redundancy(self, findings: list[Finding], ran: list[str]) -> Subscore:
        if "consolidate" not in ran:
            return Subscore(None, False, DIMENSIONS["surface_redundancy"], "consolidate probe not run")
        merges = [f for f in findings if f.kind == "merge_candidate"]
        dups = [f for f in findings if f.kind == "duplicate"]
        items = merges + dups
        if not items:
            return Subscore(100.0, True, DIMENSIONS["surface_redundancy"], "no redundancy detected", 0)
        # Penalize by summed severity, saturating.
        penalty = _clamp(sum(f.severity for f in items) / 4) * 100
        score = round(max(0.0, 100 - penalty), 1)
        return Subscore(
            score, True, DIMENSIONS["surface_redundancy"],
            f"{len(merges)} merge candidate(s), {len(dups)} duplicate(s)",
            finding_count=len(items),
        )

    def _schema_hygiene(self, findings: list[Finding]) -> Subscore:
        structural = [
            f for f in findings
            if f.kind == "schema_smell" and f.measurement.get("smell") not in DESC_SMELLS
        ]
        # Density relative to tool count.
        n_tools = self._tool_count(findings)
        if n_tools == 0:
            return Subscore(None, False, DIMENSIONS["schema_hygiene"], "no tools")
        penalty = _clamp(sum(f.severity for f in structural) / max(1, n_tools)) * 100
        score = round(max(0.0, 100 - penalty), 1)
        return Subscore(
            score, True, DIMENSIONS["schema_hygiene"],
            f"{len(structural)} structural smell(s) over {n_tools} tools",
            finding_count=len(structural),
        )

    def _description_quality(self, findings: list[Finding], ran: list[str]) -> Subscore:
        desc = [
            f for f in findings
            if f.kind == "schema_smell" and f.measurement.get("smell") in DESC_SMELLS
        ]
        proxy = [
            f for f in findings
            if f.kind == "selection_error" and f.measurement.get("mode") == "confusability_proxy"
        ]
        n_tools = self._tool_count(findings)
        if n_tools == 0:
            return Subscore(None, False, DIMENSIONS["description_quality"], "no tools")
        penalty = _clamp((sum(f.severity for f in desc) + sum(f.severity for f in proxy)) / max(1, n_tools)) * 100
        score = round(max(0.0, 100 - penalty), 1)
        note = f"{len(desc)} description smell(s)"
        if "noise" in ran:
            note += f", {len(proxy)} confusable"
        return Subscore(score, True, DIMENSIONS["description_quality"], note, len(desc) + len(proxy))

    def _selection_robustness(self, findings: list[Finding], ran: list[str]) -> Subscore:
        if "noise" not in ran:
            return Subscore(None, False, DIMENSIONS["selection_robustness"], "behavioral probe not run (no LLM)")
        sel = [f for f in findings if f.kind == "selection_error" and f.measurement.get("mode") == "labeled"]
        distr = [f for f in findings if f.kind == "distraction"]
        # Labeled pass rates dominate; distraction failures are sharp penalties.
        if sel:
            avg_pass = sum(f.measurement.get("pass_rate", 1.0) for f in sel) / len(sel)
            base = avg_pass * 100
        else:
            # proxy-only: derive from confusability rate
            proxy = [f for f in findings if f.kind == "selection_error" and f.measurement.get("mode") == "confusability_proxy"]
            n_tools = self._tool_count(findings) or 1
            base = max(0.0, 100 - _clamp(len(proxy) / n_tools) * 60)
        distr_penalty = _clamp(sum(f.measurement.get("fire_rate", 0) for f in distr) / 2) * 40
        score = round(max(0.0, base - distr_penalty), 1)
        return Subscore(
            score, True, DIMENSIONS["selection_robustness"],
            f"{len(sel)} labeled, {len(distr)} distraction failure(s)",
            len(sel) + len(distr),
        )

    def _tool_count(self, findings: list[Finding]) -> int:
        surface = next(
            (f for f in findings if f.kind == "token_cost" and f.measurement.get("scope") == "surface"),
            None,
        )
        if surface:
            return surface.measurement.get("tool_count", 0)
        names = {
            f.target for f in findings
            if f.kind == "token_cost" and f.measurement.get("scope") == "tool" and isinstance(f.target, str)
        }
        return len(names)
