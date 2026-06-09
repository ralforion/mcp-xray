"""Report / deliverable (PLAN S12). Emits both report.json (versioned,
fingerprinted, for drift) and report.md (the client artifact). Every
recommendation traces to a Finding. One voice throughout, with a methodology &
coverage footer for defensibility.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

from . import __version__
from .finding import Finding
from .grade import DESC_SMELLS, DIMENSION_HELP, GRADE_SCALE_HELP, Grade
from .orchestrator import RunResult
from .pricing import DEFAULT_INPUT_COST_PER_MTOK, input_price_per_mtok


def _smell_issue_fix(m: dict) -> tuple[str, str]:
    """Map a schema_smell measurement to a human issue + concrete fix."""
    s = m.get("smell")
    if s == "missing_description":
        return ("no description", "Add a one-line description: what it does and when to use it.")
    if s == "tiny_description":
        return (f"description too short ({m.get('words', 0)} words)",
                "Expand to a full sentence - what it does, its key input, and when to pick it.")
    if s == "short_description":
        return (f"short description ({m.get('words', 0)} words)",
                "Add a clause on when to use it / how it differs from sibling tools.")
    if s == "vague_description":
        terms = ", ".join(f"`{t}`" for t in m.get("terms", []))
        confirmed = " - LLM-confirmed" if m.get("llm_confirmed") else ""
        return (f"vague wording ({terms}){confirmed}",
                "Replace filler with concrete behavior and named inputs.")
    if s == "deep_nesting":
        return (f"deeply nested input (depth {m.get('depth', 0)})",
                "Flatten the schema or split the tool - deep nesting costs tokens and misfills args.")
    if s == "enum_bloat":
        return (f"oversized enum ({m.get('max', 0)} values)",
                "Trim the enum, or take a free string and validate server-side.")
    if s == "wide_schema":
        return (f"too many parameters ({m.get('property_count', 0)})",
                "Group related params into an object, default the optionals, or split the tool.")
    return (str(s), "Review against schema-hygiene guidance.")

# Plain-English glossary, rendered as a collapsible block at the end of the
# report so the deliverable is self-contained.
GLOSSARY = [
    ("Surface tokens / Context tax", "Tokens the tool definitions (name + description + input schema) add to the model's context on every turn - computed as count_tokens(all tools) − count_tokens(no tools). Paid whether or not any tool is actually called."),
    ("Per-tool cost (leave-one-out)", "A single tool's share of the surface, measured as count(all tools) − count(all tools except this one)."),
    ("Authoritative vs ESTIMATE", "Authoritative = the model's real tokenizer (Anthropic count_tokens, or OpenAI tiktoken locally). ESTIMATE = an offline heuristic over the serialized schema - fine for quick scans, never the headline number."),
    ("Schema smell", "A structural input-schema problem: deep nesting, an oversized enum, too many parameters, or a missing/short/vague description."),
    ("Merge candidate", "Two or more tools similar enough to collapse into one - e.g. a CRUD family (create/update/delete_x) into manage_x(action=…). Scored as tokens saved vs. added call-complexity."),
    ("Resource candidate", "A pure-read tool (get_/list_) that could be exposed as an MCP resource instead, removing it from the tool-selection space. Advisory - depends on access pattern and a dynamic keyspace may have to stay a tool."),
    ("Confusability (proxy)", "When the model picks the wrong tool given a tool's own description as the prompt - a no-LLM-ground-truth signal that two tools overlap."),
    ("Distraction", "A tool firing on an off-domain prompt it has no business handling - a sign of over-broad descriptions."),
    ("Selection robustness", "Whether the model picks the right tool and stays quiet on off-domain prompts. Requires the LLM probe; otherwise 'not measured'."),
    ("Phase / carried tools", "A phase-swapped server exposes different tools by journey phase (e.g. setup vs. run). 'Carried' tools are visible in more than one phase - the cross-phase cost."),
    ("Progressive / JIT loading", "Exposing tools on demand per phase instead of all at once, so the model never carries the full union - the per-turn tax is the worst single phase."),
    ("Fingerprint", "A hash of the tool inventory; keys a run so re-audits of the same server reveal drift."),
    ("Call manifest", "An operator-supplied list of tool calls mcp-xray is allowed to actually execute (--call-manifest). No tool is ever called without it - it's your signed permission slip that these calls are read-only/sandbox-safe."),
    ("Result size", "How big a tool's OUTPUT is (chars + bytes), measured by calling each manifested tool once. Tool outputs are fed back to the model and cost context on every call - a cost the static surface scan can't see."),
]


def build_report(result: RunResult, grade: Grade, *, generated_at: str | None = None) -> dict:
    inv = result.inventory
    findings = result.findings

    surface = _surface_finding(findings)
    surface_tokens = surface.measurement.get("tokens", 0) if surface else 0

    price_table = result.context.get("price_table")  # None -> built-in
    price_meta = result.context.get("price_meta") or {"source": "built-in"}
    price_per_mtok, price_known = input_price_per_mtok(result.context.get("model"), price_table)

    report = {
        "tool": "mcp-xray",
        "version": __version__,
        # Local time (with offset), not UTC - the report is read by a human.
        "generated_at": generated_at or datetime.now().astimezone().isoformat(timespec="seconds"),
        "server": {
            "name": inv.server_name,
            "version": inv.server_version,
            "transport": inv.transport,
            "source": inv.source,
            "fingerprint": inv.fingerprint(),
            "tool_count": len(inv.tools),
        },
        "grade": {
            "overall": grade.overall,
            "letter": grade.letter,
            "subscores": {
                k: {
                    "score": s.score,
                    "measured": s.measured,
                    "weight": s.weight,
                    "rationale": s.rationale,
                    "finding_count": s.finding_count,
                }
                for k, s in grade.subscores.items()
            },
        },
        "headline": {
            "surface_tokens_per_turn": surface_tokens,
            "est_cost_per_1k_turns_usd": round(surface_tokens * 1000 / 1_000_000 * price_per_mtok, 2),
            "input_price_per_mtok": price_per_mtok,
            "input_price_known": price_known,
            "price_source": price_meta.get("source"),
            "price_fetched_at": price_meta.get("fetched_at"),
            "price_note": price_meta.get("note"),
            "authoritative": surface.measurement.get("authoritative") if surface else None,
            "token_backend": result.context.get("token_backend"),
            "model": result.context.get("model"),
            "phased": bool(result.context.get("phased")),
            "worst_phase": surface.measurement.get("worst_phase") if surface else None,
            "phase_tokens": result.context.get("phase_tokens"),
            "union_tool_count": result.context.get("union_tool_count"),
            "carried_tools": result.context.get("carried_tools"),
        },
        "coverage": {
            "ran": result.ran,
            "skipped": result.skipped,
            "available": result.context.get("available"),
        },
        # Which LLM did the work, and what it was used for (token counting and/or
        # the behavioral probe). Stored so a report is self-describing about its
        # model provenance.
        "llm": {
            "model": result.context.get("model"),
            "token_backend": result.context.get("token_backend"),
            "behavioral_probe": "noise" in result.ran,
            "samples": result.context.get("noise_samples"),
        },
        "findings": [f.to_dict() for f in findings],
    }
    return report


def _surface_finding(findings: list[Finding]) -> Finding | None:
    return next(
        (f for f in findings if f.kind == "token_cost" and f.measurement.get("scope") == "surface"),
        None,
    )


def _fs_safe(value: str) -> str:
    """Make a string safe for a folder/file name: path separators and other
    chars unsafe across filesystems (``/ : \\`` and whitespace) become ``_``."""
    return re.sub(r"[/:\\\s]", "_", str(value))


def standard_run_name(report: dict) -> str:
    """Consistent run-folder name: ``<server-version>`` (filesystem-sanitized).

    One folder per server version - reruns of the same version overwrite in
    place. The full local-time ISO timestamp lives inside the report
    (``generated_at``), not the folder name. Falls back to the inventory
    fingerprint when the server reports no version."""
    srv = report.get("server", {})
    return _fs_safe(srv.get("version") or srv.get("fingerprint") or "unknown")


def write_run(out_dir: str | Path, result: RunResult, grade: Grade) -> dict[str, Path]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    report = build_report(result, grade)
    json_path = out / "report.json"
    md_path = out / "report.md"
    json_path.write_text(json.dumps(report, indent=2, default=str))
    md_path.write_text(render_markdown(report))
    return {"json": json_path, "md": md_path}


# --- markdown rendering --------------------------------------------------
def _esc(value) -> str:
    """Escape a value for a Markdown table cell - a bare '|' splits cells."""
    return str(value).replace("|", "\\|")


def render_markdown(report: dict) -> str:
    L: list[str] = []
    g = report["grade"]
    h = report["headline"]
    srv = report["server"]
    findings = [Finding.from_dict(f) for f in report["findings"]]

    L.append(f"# MCP Surface Review - {srv.get('name') or srv.get('source') or 'server'}")
    L.append("")
    server_ver = f"MCP server v{srv.get('version')} · " if srv.get("version") else ""
    L.append(
        f"_Generated {report.get('generated_at')} · {server_ver}mcp-xray v{report.get('version')} · "
        f"{srv.get('tool_count')} tools · transport `{srv.get('transport')}` · "
        f"fingerprint `{srv.get('fingerprint')}`_"
    )
    L.append("")
    L.append(f"## Result - Grade {g['letter']} ({g['overall']} / 100)")
    L.append("")
    L.append("<details><summary>How the grade works</summary>")
    L.append("")
    for line in GRADE_SCALE_HELP:
        L.append(f"- {line}")
    L.append("")
    L.append("</details>")
    L.append("")
    auth = "authoritative" if h["authoritative"] else "ESTIMATE - offline heuristic"
    rate = h.get("input_price_per_mtok", DEFAULT_INPUT_COST_PER_MTOK)
    L.append(
        f"**Context tax:** {h['surface_tokens_per_turn']:,} tokens/turn "
        f"(~\\${h['est_cost_per_1k_turns_usd']:.2f}/1k turns at \\${rate:.2f}/Mtok input) - _{auth}_"
    )
    if h.get("model"):
        rate_src = (
            f"list price \\${rate:.2f}/Mtok input"
            if h.get("input_price_known")
            else f"no list price on file - using \\${rate:.2f}/Mtok Sonnet-tier default"
        )
        src = h.get("price_source")
        if src == "fetched" and h.get("price_fetched_at"):
            rate_src += f", fetched live {h['price_fetched_at']}"
        elif src == "cache" and h.get("price_fetched_at"):
            rate_src += f", cached from {h['price_fetched_at']}"
        elif src == "built-in":
            rate_src += ", built-in table"
        L.append(f"_Priced against model: `{h['model']}` ({rate_src})._")
        if h.get("price_note"):
            L.append(f"_Price note: {h['price_note']}._")
    if h.get("phased"):
        L.append(
            f"_Phased surface: tax shown is the **worst phase** (`{h.get('worst_phase')}`) - "
            f"the {h.get('union_tool_count')}-tool union is never co-loaded._"
        )
    L.append("")

    # Subscores
    L.append("## Scorecard")
    L.append("")
    L.append("| Dimension | Score | Weight | Notes |")
    L.append("|---|---|---|---|")
    for key, s in g["subscores"].items():
        label = key.replace("_", " ").title()
        score = "not measured" if not s["measured"] or s["score"] is None else f"{s['score']:.0f}"
        L.append(f"| {label} | {score} | {int(s['weight']*100)}% | {s['rationale']} |")
    L.append("")
    L.append("<details><summary>What the dimensions mean</summary>")
    L.append("")
    L.append("_Each is 0–100; the grade is their weighted average over the dimensions actually measured (skipped probes drop their weight, never scored zero)._")
    L.append("")
    for key in g["subscores"]:
        blurb = DIMENSION_HELP.get(key)
        if blurb:
            L.append(f"- **{key.replace('_', ' ').title()}** ({int(g['subscores'][key]['weight']*100)}%) - {blurb}")
    L.append("")
    L.append("</details>")
    L.append("")

    # Phase surfaces (only when phased)
    phase_findings = sorted(
        (f for f in findings if f.kind == "token_cost" and f.measurement.get("scope") == "phase"),
        key=lambda f: f.measurement.get("tokens", 0),
        reverse=True,
    )
    if phase_findings:
        L.append("## Phase surfaces")
        L.append("")
        L.append("_Tool list swaps by journey phase - the model carries one phase at a time._")
        L.append("")
        L.append("| Phase | Tools | Tokens/turn | Tokens/tool |")
        L.append("|---|---:|---:|---:|")
        for f in phase_findings:
            m = f.measurement
            L.append(f"| `{m.get('phase')}` | {m.get('tool_count')} | {m.get('tokens', 0):,} | {m.get('tokens_per_tool_avg')} |")
        L.append("")
        carried = h.get("carried_tools") or []
        if carried:
            L.append(f"**Carried across phases** ({len(carried)}): " + ", ".join(f"`{c}`" for c in carried))
            L.append("")

    # Per-tool cost table
    tool_costs = sorted(
        (f for f in findings if f.kind == "token_cost" and f.measurement.get("scope") == "tool"),
        key=lambda f: f.measurement.get("tokens", 0),
        reverse=True,
    )
    if tool_costs:
        phased_tools = any(f.measurement.get("phases") for f in tool_costs)
        L.append(f"## Per-tool context cost - all {len(tool_costs)} tools")
        L.append("")
        if phased_tools:
            L.append("| Tool | Tokens | Share | Phases |")
            L.append("|---|---:|---:|---|")
            for f in tool_costs:
                m = f.measurement
                phs = ", ".join(m.get("phases", [])) or "-"
                L.append(f"| `{f.target}` | {m.get('tokens', 0):,} | {m.get('share', 0)*100:.0f}% | {phs} |")
        else:
            L.append("| Tool | Tokens | Share |")
            L.append("|---|---:|---:|")
            for f in tool_costs:
                m = f.measurement
                L.append(f"| `{f.target}` | {m.get('tokens', 0):,} | {m.get('share', 0)*100:.0f}% |")
        L.append("")

    # Hidden injectors
    injectors = [f for f in findings if f.kind == "hidden_injector"]
    if injectors:
        L.append("## Hidden context injectors")
        L.append("")
        for f in injectors:
            m = f.measurement
            extra = f", ~{m['tokens_est']} tokens" if m.get("tokens_est") else ""
            # Only instructions are a per-turn tax; prompts/resources are lazy
            # (metadata listing only, content fetched on demand) - flag as such.
            tag = " - _per turn_" if m.get("kind") == "instructions" else " - _on-demand (listing only, not graded)_"
            L.append(f"- **{m.get('kind')}** (`{f.target}`){extra}{tag}")
        L.append("")

    # Consolidation proposals
    merges = [f for f in findings if f.kind == "merge_candidate"]
    resources = [f for f in findings if f.kind == "resource_candidate"]
    jit = [f for f in findings if f.kind == "jit_candidate"]
    if merges or resources or jit:
        L.append("## Consolidation proposals")
        L.append("")
        L.append("_Framed as one of three: **merge**, **convert to resource**, or **switch to JIT loading**._")
        L.append("")

    if merges:
        merges_sorted = sorted(merges, key=lambda f: f.measurement.get("tokens_saved_est", 0), reverse=True)
        phased_merges = any(f.measurement.get("phases") for f in merges_sorted)
        L.append("### Merge candidates")
        L.append("")
        if phased_merges:
            L.append("_Only tools surfaced in the same phase(s) are merged - a merge must not change what any phase exposes._")
            L.append("")
            L.append("| Tools | Phase | Proposal | Tokens saved | Complexity | Flag |")
            L.append("|---|---|---|---:|---:|---|")
        else:
            L.append("| Tools | Proposal | Tokens saved | Complexity | Flag |")
            L.append("|---|---|---:|---:|---|")
        for f in merges_sorted:
            m = f.measurement
            tools = ", ".join(f"`{t}`" for t in (f.target if isinstance(f.target, list) else [f.target]))
            proposal = _esc(f.detail.get("proposal") or m.get("action", "merge"))
            flags = []
            if m.get("wide_union"):
                flags.append("⚠ wide union")
            if m.get("mixes_read_write"):
                flags.append("⚠ splits read+write")
            if f.detail.get("mixes_destructive"):
                flags.append("⚠ mixes destructive")
            flag = "; ".join(flags)
            cost = f"{m.get('tokens_saved_est', 0):,}"
            if phased_merges:
                phase = "+".join(m.get("phases", [])) or "-"
                L.append(
                    f"| {_esc(tools)} | {phase} | {proposal} | {cost} "
                    f"| {m.get('complexity_delta', 0)} | {flag} |"
                )
            else:
                L.append(
                    f"| {_esc(tools)} | {proposal} | {cost} "
                    f"| {m.get('complexity_delta', 0)} | {flag} |"
                )
        L.append("")

    if resources:
        L.append("### Read-only tools - could be MCP resources")
        L.append("")
        L.append(
            f"_**Advisory, not a checklist.** {len(resources)} pure-read tool(s) take **no key "
            "parameter**, so they could map to static MCP resources - removing them from the "
            "tool-selection space - **if** the data is host-surfaced or the client supports "
            "resources. Reads parameterized over a **dynamic keyspace** (e.g. `get_x(model_id)` "
            "where ids are discovered at runtime - the common case for data / semantic-layer "
            "servers) are **not listed**: they should stay tools. Treat this as a lens, not a "
            "to-do._"
        )
        L.append("")
        L.append(f"<details><summary>The {len(resources)} pure-read tools</summary>")
        L.append("")
        for f in resources:
            m = f.measurement
            clean = " - no parameters; maps to a static `resource://` document" if m.get("clean_map") else ""
            L.append(f"- `{f.target}` ({m.get('verb')}){clean}")
        L.append("")
        L.append("</details>")
        L.append("")

    if jit:
        jf = jit[0]
        if jf.measurement.get("progressive"):
            L.append("### Progressive tool loading - ✓ already in place")
            L.append("")
            L.append(f"{jf.detail.get('framing')}")
            counts = jf.detail.get("phase_tool_counts") or {}
            if counts:
                L.append("")
                L.append("Phase tool counts: " + ", ".join(f"`{k}`={v}" for k, v in counts.items()))
            L.append("")
        elif jf.measurement.get("recommend_jit"):
            L.append("### Architectural alternative - JIT / progressive loading")
            L.append("")
            L.append(f"{jf.detail.get('framing')}")
            L.append("")

    # Schema & description issues (the named tools behind the hygiene/quality scores)
    smells = [f for f in findings if f.kind == "schema_smell"]
    if smells:
        desc = [f for f in smells if f.measurement.get("smell") in DESC_SMELLS]
        struct = [f for f in smells if f.measurement.get("smell") not in DESC_SMELLS]
        L.append("## Schema & description issues")
        L.append("")

        def _smell_table(group: list[Finding]) -> None:
            L.append("| Tool | Issue | Fix |")
            L.append("|---|---|---|")
            for f in sorted(group, key=lambda f: str(f.target)):
                issue, fix = _smell_issue_fix(f.measurement)
                L.append(f"| `{f.target}` | {issue} | {fix} |")
            L.append("")

        if desc:
            L.append("**Description quality** - drives the Description Quality score:")
            L.append("")
            _smell_table(desc)
        if struct:
            L.append("**Schema hygiene** - drives the Schema Hygiene score:")
            L.append("")
            _smell_table(struct)

    # Selection / distraction
    sel = [f for f in findings if f.kind == "selection_error"]
    distr = [f for f in findings if f.kind == "distraction"]
    if sel or distr:
        L.append("## Behavioral findings")
        L.append("")
        if distr:
            L.append("**Distraction (off-domain firing):**")
            for f in distr:
                m = f.measurement
                L.append(
                    f"- fired on off-domain task ({m.get('fire_rate', 0)*100:.0f}% of samples): "
                    f"`{f.detail.get('off_domain_task')}` → {f.detail.get('fired')}"
                )
            L.append("")
        if sel:
            weak = [f for f in sel if f.measurement.get("pass_rate", 1.0) < 0.8]
            if weak:
                L.append("**Weak / confusable selection:**")
                for f in sorted(weak, key=lambda f: f.measurement.get("pass_rate", 1.0)):
                    m = f.measurement
                    L.append(f"- `{f.target}` - pass rate {m.get('pass_rate', 0)*100:.0f}% ({m.get('mode')})")
                L.append("")

    # Result sizes (--call-manifest): tool OUTPUTS that cost context per call.
    rsize = [f for f in findings if f.kind == "result_size"]
    if rsize:
        L.append("## Result sizes (per call)")
        L.append("")
        L.append("_Tool outputs cost context on every call. Measured by calling each "
                 "operator-confirmed tool once (--call-manifest)._")
        L.append("")
        L.append("| Tool | Result chars | Result bytes |")
        L.append("|---|--:|--:|")
        for f in sorted(rsize, key=lambda f: f.measurement.get("bytes", 0), reverse=True):
            m = f.measurement
            if m.get("error"):
                L.append(f"| `{_esc(f.target)}` | - | error: {_esc(m['error'])} |")
            else:
                L.append(f"| `{_esc(f.target)}` | {m.get('chars', 0):,} | {m.get('bytes', 0):,} |")
        L.append("")

    # Methodology footer
    L.append("---")
    L.append("")
    L.append("## Methodology & coverage")
    L.append("")
    cov = report["coverage"]
    L.append(f"- **Probes run:** {', '.join(cov['ran']) or 'none'}")
    if cov["skipped"]:
        sk = "; ".join(f"{s['probe']} (missing: {', '.join(s['missing'])})" for s in cov["skipped"])
        L.append(f"- **Skipped (not measured):** {sk}")
    _backend_desc = {
        "api": "authoritative (Anthropic count_tokens)",
        "tiktoken": "authoritative (OpenAI tiktoken, local)",
    }
    _auth_desc = _backend_desc.get(report["headline"]["token_backend"], "authoritative")
    L.append(f"- **Token figure:** {report['headline']['token_backend']} backend - "
             f"{_auth_desc if h['authoritative'] else 'offline ESTIMATE, not for headline use'}")
    llm = report.get("llm") or {}
    if llm.get("model"):
        uses = []
        if h["authoritative"]:
            uses.append("token counting")
        if llm.get("behavioral_probe"):
            s = llm.get("samples")
            uses.append(f"behavioral probe ({s} samples)" if s else "behavioral probe")
        L.append(f"- **Model used:** `{llm['model']}`" + (f": {'; '.join(uses)}" if uses else ""))
    L.append(f"- **Server fingerprint:** `{srv['fingerprint']}` (re-audits keyed to this for drift)")
    L.append(f"- **mcp-xray v{report['version']}**")
    L.append("")

    # Glossary
    L.append("<details><summary>Glossary</summary>")
    L.append("")
    for term, definition in GLOSSARY:
        L.append(f"- **{term}** - {definition}")
    L.append("")
    L.append("</details>")
    L.append("")
    return "\n".join(L)
