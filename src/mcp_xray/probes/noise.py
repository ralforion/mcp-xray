"""OWNED probe -- behavioral noise (M3, LLM, optional). PLAN S9.

The credible part of the review. Three modes, in priority order:
  - labeled queries (--queries): selection accuracy, wasted calls, turns
  - confusability proxy (no labels): one probe query per tool from its
    description; off-pick == confusable surface. Feeds M4. No ground truth.
  - distraction (registry-citizen): off-domain tasks must fire nothing.

requires() = {inventory, api_key, llm}. Skipped cleanly when no key.
Selection is non-deterministic even at temp 0, so we run N samples and report
a pass rate, not a boolean (PLAN S9).
"""

from __future__ import annotations

import json

from ..finding import Finding
from ..inventory import Inventory, Tool
from .base import Probe, RunContext

DEFAULT_SAMPLES = 3

# Sentinel for a sample whose API call FAILED (rate limit, auth, bad schema,
# model error). Distinct from None (= the model legitimately called no tool):
# an errored sample must never be scored as a wrong pick or a clean decline.
_PROBE_ERROR = object()

# Sentinel for "not in the resume cache" (distinct from a cached None = the
# model legitimately declined and we want to reuse that decline).
_MISS = object()

# Off-domain tasks a focused server should ignore entirely (PLAN S9).
DISTRACTION_TASKS = [
    "What's 17 times 23?",
    "Translate 'good morning' into French.",
    "Write a haiku about the ocean.",
    "Convert 5 miles to kilometers.",
]


class NoiseProbe(Probe):
    name = "noise"

    def requires(self) -> set[str]:
        return {"inventory", "api_key", "llm"}

    def run(self, ctx: RunContext) -> list[Finding]:
        inv = ctx.inventory
        samples = int(ctx.config.get("noise_samples", DEFAULT_SAMPLES))
        # Resume cache (optional): completed samples persist to a JSONL keyed by
        # (fingerprint, model, query, idx) so an interrupted/failed run resumes
        # without re-paying for samples already gathered. Off unless a path is
        # set (--resume). Errored samples are never cached, so they retry.
        self._cache_path = ctx.config.get("probe_cache")
        self._fp = inv.fingerprint()
        self._model = ctx.model or ""
        self._cache = self._cache_load(self._cache_path)
        if self._cache_path and self._cache:
            from sys import stderr
            print(f"[resume] loaded {len(self._cache)} cached probe sample(s)", file=stderr)
        findings: list[Finding] = []

        if ctx.queries:
            findings.extend(self._labeled_selection(ctx, inv, samples))
        else:
            findings.extend(self._confusability_proxy(ctx, inv, samples))

        findings.extend(self._distraction(ctx, inv, samples))
        return findings

    # --- mode 1: labeled selection --------------------------------------
    def _labeled_selection(self, ctx: RunContext, inv: Inventory, samples: int) -> list[Finding]:
        out: list[Finding] = []
        for q in ctx.queries or []:
            query = q.get("query") or q.get("prompt", "")
            expected = set(q.get("expected_tools") or ([q["expected_tool"]] if q.get("expected_tool") else []))
            picks, errors = self._sample(ctx, inv, query, samples)
            if not picks:
                # every sample errored - not measured; don't fabricate a 0% pass.
                continue
            hits = [p for p in picks if p in expected]
            pass_rate = len(hits) / len(picks)
            out.append(
                Finding(
                    probe=self.name,
                    kind="selection_error",
                    target=sorted(expected) or query[:40],
                    measurement={
                        "mode": "labeled",
                        "pass_rate": round(pass_rate, 3),
                        "samples": len(picks),
                        "errors": errors,
                        "expected": sorted(expected),
                    },
                    confidence=0.85,
                    detail={"query": query, "picks": picks},
                )
            )
        return out

    # --- mode 2: confusability proxy ------------------------------------
    def _confusability_proxy(self, ctx: RunContext, inv: Inventory, samples: int) -> list[Finding]:
        out: list[Finding] = []
        for tool in inv.tools:
            probe_q = self._synthesize_query(tool)
            picks, errors = self._sample(ctx, inv, probe_q, samples)
            if not picks:
                continue  # all samples errored - not measured
            hits = [p for p in picks if p == tool.name]
            pass_rate = len(hits) / len(picks)
            if pass_rate < 1.0:
                confusers: dict[str, int] = {}
                for p in picks:
                    if p and p != tool.name:
                        confusers[p] = confusers.get(p, 0) + 1
                out.append(
                    Finding(
                        probe=self.name,
                        kind="selection_error",
                        target=tool.name,
                        measurement={
                            "mode": "confusability_proxy",
                            "pass_rate": round(pass_rate, 3),
                            "samples": len(picks),
                            "errors": errors,
                        },
                        confidence=0.7,
                        detail={"query": probe_q, "confused_with": confusers},
                    )
                )
        return out

    # --- mode 3: distraction --------------------------------------------
    def _distraction(self, ctx: RunContext, inv: Inventory, samples: int) -> list[Finding]:
        out: list[Finding] = []
        for task in DISTRACTION_TASKS:
            picks, errors = self._sample(ctx, inv, task, samples, allow_none=True)
            if not picks:
                continue  # all samples errored - not measured
            fired = [p for p in picks if p]
            if fired:
                counts: dict[str, int] = {}
                for p in fired:
                    counts[p] = counts.get(p, 0) + 1
                out.append(
                    Finding(
                        probe=self.name,
                        kind="distraction",
                        target=sorted(counts.keys()),
                        measurement={
                            "fire_rate": round(len(fired) / len(picks), 3),
                            "samples": len(picks),
                            "errors": errors,
                        },
                        confidence=0.8,
                        detail={"off_domain_task": task, "fired": counts},
                    )
                )
        return out

    # --- LLM plumbing ----------------------------------------------------
    def _synthesize_query(self, tool: Tool) -> str:
        desc = (tool.description or tool.name).strip().rstrip(".")
        return f"I need to: {desc}. Which tool should I use?"

    def _ask_tool_choice(
        self, ctx: RunContext, inv: Inventory, query: str, *, allow_none: bool = False
    ):
        """Ask the model to pick a tool. Returns the chosen tool name, ``None``
        if it declined / chose nothing, or ``_PROBE_ERROR`` if the API call
        failed. Vendor-agnostic: ``ctx.client`` is a ``ChatClient`` that hides
        the SDK and tool-call format."""
        client = ctx.client
        if client is None:  # pragma: no cover - guarded by can_run
            return None
        tools = [t.to_api_dict() for t in inv.tools]
        system = (
            "You are choosing whether and which tool to call for the user's request. "
            "If a tool fits, call exactly one. If none fits, do not call any tool."
        )
        try:
            return client.pick_tool(tools, system, query, allow_none=allow_none)
        except Exception:
            # API failure - NOT a model decision. Surface as the error sentinel
            # so the caller can exclude it rather than count it as a miss.
            return _PROBE_ERROR

    def _sample(self, ctx: RunContext, inv: Inventory, query: str, n: int, *, allow_none: bool = False):
        """Run n samples; return (successful_picks, n_errors). Errored samples
        are dropped from the picks so they can't corrupt a pass/fire rate, and
        are never cached (so a resume retries them). Cached samples are reused
        without an API call."""
        picks: list = []
        errors = 0
        for i in range(n):
            key = self._cache_key(query, i, allow_none)
            hit = self._cache.get(key, _MISS) if key else _MISS
            if hit is not _MISS:
                picks.append(hit)  # reuse a completed sample (may be None)
                continue
            r = self._ask_tool_choice(ctx, inv, query, allow_none=allow_none)
            if r is _PROBE_ERROR:
                errors += 1
                continue
            picks.append(r)
            self._cache_put(key, r)
        return picks, errors

    # --- resume cache ----------------------------------------------------
    def _cache_key(self, query: str, idx: int, allow_none: bool) -> str | None:
        """Stable per-sample key. Includes the surface fingerprint + model so a
        changed surface or model misses (and re-probes); returns None when
        caching is off."""
        if not getattr(self, "_cache_path", None):
            return None
        return json.dumps([self._fp, self._model, query, idx, allow_none], sort_keys=True)

    def _cache_load(self, path) -> dict:
        if not path:
            return {}
        out: dict = {}
        try:
            with open(path) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                        out[rec["k"]] = rec["v"]  # v is a tool name or null
                    except Exception:
                        continue  # skip a corrupt line, keep the rest
        except FileNotFoundError:
            pass
        return out

    def _cache_put(self, key: str | None, pick) -> None:
        if not key:
            return
        self._cache[key] = pick
        path = self._cache_path
        try:
            import os
            os.makedirs(os.path.dirname(path), exist_ok=True) if os.path.dirname(path) else None
            with open(path, "a") as f:  # append so partial progress survives a crash
                f.write(json.dumps({"k": key, "v": pick}) + "\n")
        except Exception:
            pass  # caching is best-effort; never break the probe over it
