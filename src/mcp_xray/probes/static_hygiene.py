"""OWNED probe -- static hygiene (authoritative). PLAN S8.

Zero-config, no LLM, the trustworthy core:
  - global tool-surface cost = count(all) - count([])
  - per-tool attribution via leave-one-out (surfaces the schema monster)
  - hidden injectors (server instructions / prompts / auto resources)
  - schema smells -> findings (feed grading AND consolidation)

This probe's per-tool token cost is the authoritative figure; wrapped
token-tax adapters are reconciled against it (PLAN S7).
"""

from __future__ import annotations

from ..finding import Finding
from ..inventory import Inventory, Tool
from .base import Probe, RunContext

# Schema-smell thresholds. Conservative; tuned to flag the genuinely bad.
MIN_DESC_WORDS = 3
SHORT_DESC_WORDS = 6
DEEP_NESTING = 4
ENUM_BLOAT = 12
WIDE_PROPS = 15

VAGUE_TERMS = {"handle", "process", "manage", "do", "stuff", "thing", "various", "etc", "helper", "util"}


def drop(tools: list[dict], name: str) -> list[dict]:
    return [t for t in tools if t.get("name") != name]


class StaticHygieneProbe(Probe):
    name = "static_hygiene"

    def requires(self) -> set[str]:
        # Needs only an inventory + a counter; the counter is always present
        # (offline fallback). No live server, no key.
        return {"inventory"}

    def run(self, ctx: RunContext) -> list[Finding]:
        inv = ctx.inventory
        findings: list[Finding] = []
        api_tools = [t.to_api_dict() for t in inv.tools]
        counter = ctx.counter

        empty = counter.count([])
        full = counter.count(api_tools)
        global_cost = full - empty

        findings.append(
            Finding(
                probe=self.name,
                kind="token_cost",
                target=inv.names,
                measurement={
                    "scope": "surface",
                    "tokens": global_cost,
                    "tool_count": len(inv.tools),
                    "tokens_per_tool_avg": round(global_cost / max(1, len(inv.tools)), 1),
                    "authoritative": counter.authoritative,
                    "backend": counter.backend,
                    "model": counter.model,
                },
                confidence=1.0 if counter.authoritative else 0.7,
                detail={"empty_baseline": empty, "full": full},
            )
        )

        # Per-tool attribution via leave-one-out.
        per_tool = self._per_tool_cost(counter, api_tools, full)
        for tool in inv.tools:
            cost = per_tool.get(tool.name, 0)
            findings.append(
                Finding(
                    probe=self.name,
                    kind="token_cost",
                    target=tool.name,
                    measurement={
                        "scope": "tool",
                        "tokens": cost,
                        "share": round(cost / global_cost, 3) if global_cost else 0.0,
                        "authoritative": counter.authoritative,
                        "backend": counter.backend,
                    },
                    confidence=1.0 if counter.authoritative else 0.7,
                )
            )

        # Hidden injectors.
        findings.extend(self._hidden_injectors(inv, counter))

        # Schema smells.
        for tool in inv.tools:
            findings.extend(self._schema_smells(tool, ctx))

        return findings

    def _per_tool_cost(self, counter, api_tools, full) -> dict[str, int]:
        out: dict[str, int] = {}
        for t in api_tools:
            without = counter.count(drop(api_tools, t["name"]))
            out[t["name"]] = full - without
        return out

    def _blob_cost(self, counter, pairs: list[tuple]) -> int:
        """Token cost of a set of (name, description) entries, measured the same
        way as the tool surface (count(blob) - count([])) so the numbers are
        comparable and feed the same grade penalty."""
        blob = [
            {"name": str(n or ""), "description": str(d or ""), "input_schema": {}}
            for n, d in pairs
        ]
        return counter.count(blob) - counter.count([])

    def _hidden_injectors(self, inv: Inventory, counter) -> list[Finding]:
        findings: list[Finding] = []
        if inv.instructions:
            # Cost of the instructions string in isolation (injected every turn).
            cost = self._blob_cost(counter, [("__instructions__", inv.instructions)])
            findings.append(
                Finding(
                    probe=self.name,
                    kind="hidden_injector",
                    target="server.instructions",
                    measurement={
                        "kind": "instructions",
                        "chars": len(inv.instructions),
                        "tokens_est": cost,
                    },
                    confidence=0.9,
                    detail={"preview": inv.instructions[:200]},
                )
            )
        if inv.prompts:
            # Listing footprint (name + description per prompt), not full content.
            cost = self._blob_cost(counter, [(p.get("name"), p.get("description")) for p in inv.prompts])
            findings.append(
                Finding(
                    probe=self.name,
                    kind="hidden_injector",
                    target="server.prompts",
                    measurement={"kind": "prompts", "count": len(inv.prompts), "tokens_est": cost},
                    confidence=0.6,
                    detail={"names": [p.get("name") for p in inv.prompts]},
                )
            )
        if inv.resources:
            # Listing footprint (uri + name per resource), not fetched content.
            cost = self._blob_cost(counter, [(r.get("name"), r.get("uri")) for r in inv.resources])
            findings.append(
                Finding(
                    probe=self.name,
                    kind="hidden_injector",
                    target="server.resources",
                    measurement={"kind": "resources", "count": len(inv.resources), "tokens_est": cost},
                    confidence=0.5,
                    detail={"uris": [r.get("uri") for r in inv.resources][:20]},
                )
            )
        return findings

    def _schema_smells(self, tool: Tool, ctx: RunContext) -> list[Finding]:
        f = tool.features
        out: list[Finding] = []

        def smell(sub: str, meas: dict, conf: float = 0.9) -> Finding:
            return Finding(
                probe=self.name,
                kind="schema_smell",
                target=tool.name,
                measurement={"smell": sub, **meas},
                confidence=conf,
            )

        desc = (tool.description or "").strip()
        if not desc:
            out.append(smell("missing_description", {"words": 0}, 1.0))
        elif f.description_words < MIN_DESC_WORDS:
            out.append(smell("tiny_description", {"words": f.description_words}, 0.95))
        elif f.description_words < SHORT_DESC_WORDS:
            out.append(smell("short_description", {"words": f.description_words}, 0.7))

        words = desc.lower().split()
        hits = sorted(term for term in VAGUE_TERMS if term in words)
        # "do" is filler in "do stuff with the table" but precise in a
        # "do NOT call this for every table" guardrail - don't flag it when
        # every occurrence is negated.
        if "do" in hits:
            do_at = [i for i, w in enumerate(words) if w == "do"]
            if all(i + 1 < len(words) and words[i + 1].startswith("not") for i in do_at):
                hits.remove("do")
        if hits:
            # Optional LLM confirmation: the word list NOMINATES; when a chat
            # client is configured, the model ADJUDICATES whether the flagged
            # words are genuinely vague filler or precise prose (e.g. a "do NOT"
            # guardrail). The model can only ever REMOVE a finding, never add
            # one - so the deterministic word-list result is an upper bound and
            # the authoritative token costs stay LLM-free. Offline / on error we
            # keep the raw nomination.
            verdict = self._llm_confirms_vague(ctx, tool.name, desc, hits)
            if verdict is not False:  # True or None (offline/error) -> keep
                meas = {"terms": hits}
                if verdict is True:
                    meas["llm_confirmed"] = True
                out.append(smell("vague_description", meas, 0.6))

        if f.max_depth >= DEEP_NESTING:
            out.append(smell("deep_nesting", {"depth": f.max_depth}, 0.85))

        big_enums = [n for n in f.enum_sizes if n >= ENUM_BLOAT]
        if big_enums:
            out.append(smell("enum_bloat", {"enum_sizes": big_enums, "max": max(big_enums)}, 0.8))

        if f.property_count >= WIDE_PROPS:
            out.append(smell("wide_schema", {"property_count": f.property_count}, 0.75))

        return out

    def _llm_confirms_vague(
        self, ctx: RunContext, tool_name: str, desc: str, terms: list[str]
    ) -> bool | None:
        """Ask the model whether the flagged words are genuinely vague filler.

        Returns True (vague - keep), False (precise - drop), or None when no
        client is configured or the call fails (keep the deterministic
        nomination). Only callable downgrade path: never invents findings.
        """
        client = getattr(ctx, "client", None)
        if client is None or "llm" not in ctx.available:
            return None
        flagged = ", ".join(f'"{t}"' for t in terms)
        system = (
            "You audit MCP tool descriptions for vague wording. A word is VAGUE "
            "only when it leaves the tool's behavior unclear (e.g. 'do stuff', "
            "'handle things'). The SAME word can be precise in context - e.g. "
            "'do' inside a 'do NOT call this' guardrail, or 'process' meaning a "
            "named operation. Judge how the words are actually used here."
        )
        query = (
            f"Tool: {tool_name}\n"
            f'Description: """{desc}"""\n'
            f"Flagged word(s): {flagged}\n\n"
            "Are the flagged words used as vague filler that obscures what the "
            'tool does? Answer "yes" if genuinely vague, "no" if used precisely.'
        )
        try:
            return client.ask_yes_no(system, query)
        except Exception:
            return None
