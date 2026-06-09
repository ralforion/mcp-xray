"""OWNED probe -- capability reduction (M4). PLAN S10. The differentiator.

Two lenses doing different jobs:
  - capability lens (group by resource) -> drives merging toward one tool/resource
  - behavioral lens (read/write/destructive) -> drives resource-vs-tool + safety

Plus the architectural alternative: detect static-vs-dynamic toolsets and frame
every proposal as merge | resource | JIT (PLAN S10.4).

No LLM in v0.1: merge signals are structural/lexical (name, schema, signature).
Confusability is an optional upgrade the noise probe contributes when an LLM is
available (PLAN S16 -- leaning "structural is good enough for v0.1").
"""

from __future__ import annotations

import itertools
import re

from ..finding import Finding
from ..inventory import Inventory, Tool
from .base import Probe, RunContext

# A read whose schema carries one of these parameters is keyed over a
# *dynamic keyspace* (ids/sessions discovered at runtime) and must stay a
# tool -- a static ``resource://{id}`` template can't enumerate those keys.
# Such reads are excluded from the resource-candidate list entirely.
_KEY_PARAM = re.compile(r"(^|_)(id|ids|uuid|guid|key|slug|ref|name|model|session|dataset)($|_)", re.I)

MERGE_THRESHOLD = 0.45  # below this a pair isn't worth proposing
WIDE_UNION = 4  # a merged action-enum this wide raises call-difficulty (complexity flag)

# Verbs that compute/propose WITHOUT mutating state - advisory, read-like for
# merge-safety even though the surface verb isn't a classic read.
_ADVISORY_VERBS = {
    "suggest", "propose", "recommend", "preview", "validate", "simulate",
    "explain", "check", "analyze", "dry", "dryrun", "estimate", "plan",
}


def _mutates(tool) -> bool:
    """Does calling this tool change server state? Pure reads and advisory
    (suggest/preview/validate…) tools don't; everything else (write,
    destructive, or unknown-verb default-write) does."""
    if tool.verb in _ADVISORY_VERBS:
        return False
    return tool.behavior != "read"


def _mixes_read_write(group) -> bool:
    """True if a merge group blends a non-mutating tool (read or advisory) with
    a mutating one - collapsing them behind one action= enum raises the stakes
    of an action mistake (a stray write when the user wanted a read/preview)."""
    muts = {_mutates(t) for t in group}
    return len(muts) > 1


def jaccard(a: frozenset, b: frozenset) -> float:
    if not a and not b:
        return 0.0
    return len(a & b) / len(a | b)


def merge_score(a: Tool, b: Tool) -> tuple[float, dict]:
    """Structural merge affinity for a tool pair (PLAN S10.1)."""
    same_resource = bool(a.resource) and a.resource == b.resource
    same_verb = bool(a.verb) and a.verb == b.verb
    name_aff = 1.0 if (same_resource or same_verb) else 0.0
    schema_aff = jaccard(a.features.property_names, b.features.property_names)
    sig_aff = 1.0 if (a.features.type_signature == b.features.type_signature and a.features.type_signature) else 0.0
    score = 0.4 * name_aff + 0.4 * schema_aff + 0.2 * sig_aff
    return score, {
        "name_aff": name_aff,
        "schema_aff": round(schema_aff, 3),
        "sig_aff": sig_aff,
        "same_resource": same_resource,
        "same_verb": same_verb,
    }


class ConsolidateProbe(Probe):
    name = "consolidate"

    def requires(self) -> set[str]:
        return {"inventory"}

    def run(self, ctx: RunContext) -> list[Finding]:
        inv = ctx.inventory
        findings: list[Finding] = []

        # JIT / static detection first -- frames everything else.
        findings.append(self._jit_detect(inv))

        # Capability lens: CRUD families on a shared resource -> manage_<resource>.
        findings.extend(self._resource_families(inv, ctx))

        # Pairwise merge candidates (cross-resource shape matches the families miss).
        findings.extend(self._pairwise_merges(inv, ctx))

        # Behavioral lens: pure reads -> MCP resource candidates.
        findings.extend(self._resource_candidates(inv))

        return findings

    # --- phase awareness -------------------------------------------------
    def _phase_sig(self, name: str, ctx: RunContext) -> frozenset[str]:
        """The set of phases a tool is surfaced in (empty for a non-phased run)."""
        tp = getattr(ctx, "tool_phases", None)
        return frozenset(tp.get(name, ())) if tp else frozenset()

    def _phase_groups(self, tools: list[Tool], ctx: RunContext) -> list[tuple[frozenset[str], list[Tool]]]:
        """Partition tools by identical phase membership.

        Merging is only phase-neutral when every merged tool is surfaced in the
        *same* phases -- otherwise the merged tool would over- or under-expose an
        action in some phase (e.g. forcing a run-only ``describe`` into design).
        A non-phased run has no map, so all tools fall in one group.
        """
        if not getattr(ctx, "tool_phases", None):
            return [(frozenset(), tools)]
        buckets: dict[frozenset[str], list[Tool]] = {}
        for t in tools:
            buckets.setdefault(self._phase_sig(t.name, ctx), []).append(t)
        return list(buckets.items())

    # --- capability lens -------------------------------------------------
    def _resource_families(self, inv: Inventory, ctx: RunContext) -> list[Finding]:
        by_resource: dict[str, list[Tool]] = {}
        for t in inv.tools:
            if t.resource:
                by_resource.setdefault(t.resource, []).append(t)

        out: list[Finding] = []
        for resource, tools in by_resource.items():
            # Only merge tools that share the same phase membership.
            for phases, group in self._phase_groups(tools, ctx):
                if len(group) < 2:
                    continue
                verbs = sorted({t.verb for t in group})
                names = [t.name for t in group]
                tokens_saved = self._merge_token_estimate(ctx, group)
                # A merged manage_<resource>(action=enum) -- union width = #verbs.
                union_width = len(verbs)
                complexity_delta = self._complexity_delta(union_width)
                destructive = any(t.behavior == "destructive" for t in group)
                mixes_rw = _mixes_read_write(group)
                measurement = {
                    "lens": "capability",
                    "action": "merge",
                    "resource": resource,
                    "verbs": verbs,
                    "tokens_saved_est": tokens_saved,
                    "union_width": union_width,
                    "complexity_delta": complexity_delta,
                    "wide_union": union_width >= WIDE_UNION,
                    "mixes_read_write": mixes_rw,
                }
                if phases:
                    measurement["phases"] = sorted(phases)
                out.append(
                    Finding(
                        probe=self.name,
                        kind="merge_candidate",
                        target=names,
                        # A read+write blend is a riskier proposal - lower confidence.
                        measurement=measurement,
                        confidence=0.6 if mixes_rw else 0.8,
                        detail={
                            # '/' not '|' -- a pipe breaks Markdown table cells.
                            "proposal": f"manage_{resource}(action={'/'.join(verbs)})",
                            "mixes_destructive": destructive,
                            "mixes_read_write": mixes_rw,
                        },
                    )
                )
        return out

    # --- pairwise (cross-resource shape) --------------------------------
    def _pairwise_merges(self, inv: Inventory, ctx: RunContext) -> list[Finding]:
        out: list[Finding] = []
        seen_in_family: set[frozenset[str]] = set()
        for a, b in itertools.combinations(inv.tools, 2):
            # Skip same-resource pairs -- already covered by _resource_families.
            if a.resource and a.resource == b.resource:
                continue
            # Phase-neutral merges only: a and b must be surfaced in the same
            # phases, else collapsing them changes a phase's capability surface.
            if self._phase_sig(a.name, ctx) != self._phase_sig(b.name, ctx):
                continue
            # Cross-resource merges need the STRONG signal: an identical call
            # shape (exact type signature). Verb-only overlap across resources is
            # a resource-style-access hint, not a merge -- so we don't fire on it.
            sig = a.features.type_signature
            if not sig or sig != b.features.type_signature:
                continue
            score, parts = merge_score(a, b)
            if score < MERGE_THRESHOLD:
                continue
            # Identical shape alone pairs tools that merely share an {id} param
            # (delete_label + get_thread). Require a shared verb or resource too;
            # synonym-verb redundancy (get/fetch/lookup) is the LLM confusability
            # proxy's job, not a structural call (PLAN S10.1 / S16).
            if not parts["name_aff"]:
                continue
            key = frozenset({a.name, b.name})
            if key in seen_in_family:
                continue
            seen_in_family.add(key)
            tokens_saved = self._merge_token_estimate(ctx, [a, b])
            mixes_rw = _mixes_read_write([a, b])
            measurement = {
                "lens": "shape",
                "action": "merge",
                "score": round(score, 3),
                "tokens_saved_est": tokens_saved,
                "union_width": 2,
                "complexity_delta": self._complexity_delta(2),
                "wide_union": False,
                "mixes_read_write": mixes_rw,
                **parts,
            }
            phases = self._phase_sig(a.name, ctx)  # == b's by the guard above
            if phases:
                measurement["phases"] = sorted(phases)
            conf = round(min(0.9, 0.4 + score / 2), 2)
            out.append(
                Finding(
                    probe=self.name,
                    kind="merge_candidate",
                    target=[a.name, b.name],
                    measurement=measurement,
                    confidence=round(conf * 0.7, 2) if mixes_rw else conf,
                    detail={"note": "same call shape across resources", "mixes_read_write": mixes_rw},
                )
            )
        return out

    # --- behavioral lens -------------------------------------------------
    def _resource_candidates(self, inv: Inventory) -> list[Finding]:
        out: list[Finding] = []
        for t in inv.tools:
            if not t.is_pure_read:
                continue
            # Reads keyed over a dynamic keyspace (model_id, session, name...)
            # stay tools -- skip them so the list isn't a misleading to-do.
            if any(_KEY_PARAM.search(p) for p in t.features.property_names):
                continue
            # Only a parameterless read maps cleanly to a static resource URI.
            clean = t.features.property_count == 0
            out.append(
                Finding(
                    probe=self.name,
                    kind="resource_candidate",
                    target=t.name,
                    measurement={
                        "lens": "behavioral",
                        "action": "resource",
                        "verb": t.verb,
                        "property_count": t.features.property_count,
                        "clean_map": clean,
                    },
                    confidence=0.75 if clean else 0.55,
                    detail={
                        "rationale": "pure-read lookup -- expose as MCP resource, "
                        "removing it from the selection space entirely",
                    },
                )
            )
        return out

    # --- architectural alternative --------------------------------------
    def _jit_detect(self, inv: Inventory) -> Finding:
        """Heuristic static-vs-dynamic detection (PLAN S10.4 / S16 open Q).

        We cannot universally distinguish progressive discovery, so this is a
        framed signal, not a verdict: a large static surface with no
        dynamic-loading markers is flagged as a JIT candidate.
        """
        n = len(inv.tools)
        dynamic_markers = inv.dynamic is True
        # Lexical hint: meta-tools that themselves load/enable toolsets.
        meta = [
            t.name
            for t in inv.tools
            if any(k in t.name.lower() for k in ("enable_tool", "load_tool", "list_tools", "activate", "toolset"))
        ]
        looks_dynamic = dynamic_markers or bool(meta)
        big_static = (not looks_dynamic) and n >= 15
        return Finding(
            probe=self.name,
            kind="jit_candidate",
            target=inv.names,
            measurement={
                "tool_count": n,
                "looks_dynamic": looks_dynamic,
                "recommend_jit": big_static,
                "threshold": 15,
            },
            confidence=0.5,
            detail={
                "meta_tools": meta,
                "framing": (
                    "Large static surface -- consider just-in-time / progressive "
                    "tool loading as an alternative to (or alongside) merging."
                    if big_static
                    else "Surface size or dynamic markers do not single out JIT."
                ),
            },
        )

    # --- helpers ---------------------------------------------------------
    def _merge_token_estimate(self, ctx: RunContext, tools: list[Tool]) -> int:
        """Tokens saved by collapsing N defs into 1.

        Estimate: keep the single largest tool's cost, save the rest. Computed
        on the same counter the hygiene probe uses, so estimates are consistent
        with the headline figure.
        """
        api = [t.to_api_dict() for t in tools]
        counter = ctx.counter
        base = counter.count([])
        costs = [counter.count([t]) - base for t in api]
        if not costs:
            return 0
        return max(0, sum(costs) - max(costs))

    def _complexity_delta(self, union_width: int) -> float:
        """0..1 -- how much call-difficulty a polymorphic action enum adds.

        A 2-way union is near-free; width grows the conditional-field burden.
        """
        if union_width <= 1:
            return 0.0
        return round(min(1.0, (union_width - 1) * 0.18), 2)
