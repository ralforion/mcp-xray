<h1 align="center">mcp-xray</h1>

<p align="center">
  <a href="https://github.com/ralfbecher/mcp-xray/actions/workflows/ci.yml"><img src="https://github.com/ralfbecher/mcp-xray/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <a href="https://github.com/ralfbecher/mcp-xray/releases/tag/v1.4.0"><img src="https://img.shields.io/badge/version-1.4.0-blue" alt="version"></a>
  <a href="https://github.com/ralfbecher/mcp-xray/blob/main/LICENSE"><img src="https://img.shields.io/badge/License-BSL_1.1-orange.svg" alt="License: BSL 1.1"></a>
  <a href="https://www.python.org/downloads/"><img src="https://img.shields.io/badge/python-3.11+-blue.svg" alt="Python 3.11+"></a>
</p>

**One field instrument for MCP server reviews.** Point it at a client's MCP
server (or an offline `tools/list` dump) and walk away with **one graded
report** that answers three questions:

1. **What does this surface cost?** Per-turn context tax, per tool, before any work.
2. **Does the surface confuse the model?** Wrong-tool selection, spurious firing on off-domain tasks.
3. **Can the surface be smaller?** Which tools merge, which should be MCP _resources_, and whether the real fix is consolidation or just-in-time loading.

Many sensors, **one voice**: wrapped tools contribute _measurements only_; the
grading engine owns all interpretation.

**See real output:** [example reports](examples/reports/) - full `mcp-xray`
audits of two production MCP servers (OrionBelt Semantic Layer & Analytics),
rendered exactly as the tool emits them.

> Built by [RALFORION d.o.o.](https://ralforion.com) - the team behind the
> [OrionBelt Semantic Layer](https://github.com/ralfbecher/orionbelt-semantic-layer).
> See [Professional review & commercial use](#professional-review--commercial-use).

## Install

```bash
pip install -e .            # core (offline static + consolidation half)
pip install -e ".[api]"     # + authoritative token counting & LLM behavioral probes
pip install -e ".[live]"    # + stdio / http / sse transports
pip install -e ".[dev]"     # everything + pytest
```

The static + consolidation half runs **keyless and offline** from a `tools/list`
dump - no API key, no live server.

## Quick start

```bash
# Offline: static hygiene + consolidation, rendered as the client artifact
mcp-xray analyze --tools-json dump.json

# Authoritative token numbers (must match the client's production model)
mcp-xray analyze --tools-json dump.json --token-backend api --model claude-sonnet-4-6

# Live server, full audit including behavioral probe
ANTHROPIC_API_KEY=... mcp-xray analyze --stdio "gmail-mcp serve" --llm --model claude-sonnet-4-6

# Authed HTTP/SSE server -> pass a bearer token (repeatable --header). Prefer the
# MCP_XRAY_HTTP_HEADER env var so the token stays out of ps/shell history.
mcp-xray analyze --http https://server.example/mcp --header "Authorization: Bearer $TOKEN"

# With the client's labeled golden queries -> labeled selection accuracy
mcp-xray analyze --stdio "gmail-mcp serve" --llm --model claude-sonnet-4-6 --queries golden.yaml

# Phase-swapped surface (tool list changes by journey phase) -> per-phase audit
mcp-xray analyze --phases phases.yaml

# Just the capability-reduction analysis
mcp-xray consolidate --tools-json dump.json

# Validate a proposed merge: tokens + selection accuracy, before vs after
mcp-xray validate --before base.json --after merged.json --queries golden.yaml --model claude-sonnet-4-6

# Persist a run, re-render markdown later (fingerprinted for drift)
mcp-xray analyze --tools-json dump.json --out runs/2026-05-31/
mcp-xray report --run runs/2026-05-31/
```

Each run folder is **self-contained and replayable**: alongside `report.json`/
`report.md`, `analyze` writes the run's input under `<run>/dumps/` (a phased
run's `phases.yaml` + per-phase tools-json, or a flat run's `tools.json`). So
you can re-grade or re-probe a past version **offline** - no live server, no
re-capture - e.g. `mcp-xray analyze --phases runs/<version>/dumps/phases.yaml`.

## What it measures

Per-probe deep-dives live in [`docs/`](docs/README.md).

| Probe                           | Owned?                | Needs                 | Emits                                                                                                                                                                     |
| ------------------------------- | --------------------- | --------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `static_hygiene`                | owned (authoritative) | inventory             | per-tool token cost (leave-one-out), hidden injectors, schema smells - see [`docs/static-hygiene-probe.md`](docs/static-hygiene-probe.md)                                 |
| `consolidate`                   | owned                 | inventory             | merge candidates, resource candidates, JIT framing - see [`docs/consolidation-probe.md`](docs/consolidation-probe.md) & [`merge-candidates.md`](docs/merge-candidates.md) |
| `noise`                         | owned                 | LLM + key             | selection accuracy / confusability proxy / distraction - see [`docs/behavioral-probe.md`](docs/behavioral-probe.md)                                                       |
| `mcp_checkup`, `token_analyzer` | wrapped (v0.2)        | external bin + config | token cost, duplicates - _measurements only_                                                                                                                              |

Skipped probes drop their weight and are reported **"not measured,"** never
scored zero. The authoritative per-tool token figure is computed in-house via
the Anthropic `count_tokens` endpoint; the offline backend is a flagged
ESTIMATE and never the headline number.

> **Wrapped sensors** (`mcp_checkup`, `token_analyzer`) run when you pass
> `--client-config <path>` **and** their binary is installed; otherwise they're
> reported "not measured." They contribute measurements only - never grades.

## Grading

Five weighted dimensions roll to a 0–100 score and letter grade:
context efficiency (30%), selection robustness (25%), surface redundancy (15%),
schema hygiene (15%), description quality (15%). Full roll-up math in
[`docs/grading.md`](docs/grading.md).

## Input formats

**tools-json** accepts a full MCP result (`{"tools": [...], "instructions": "..."}`),
a bare list, or a `{"result": {"tools": [...]}}` envelope.

**golden queries** (`--queries`):

```yaml
queries:
  - query: "create a new label called Work"
    expected_tools: [create_label]
  - query: "find emails from my boss"
    expected_tools: [search_threads]
```

**call-manifest** (`--call-manifest`, safe result-size probing - operator
asserts these are read-only/sandbox calls). On a **live, non-phased** run
(`--stdio`/`--http`/`--sse`) each listed tool is called once and its result size
(chars + bytes) is measured and reported, since tool **outputs** cost context
too. Offline or phased runs warn and skip (no server to call). mcp-xray never
calls a tool without a manifest - see [`docs/safe-calls.md`](docs/safe-calls.md):

```yaml
calls:
  - tool: list_labels
    args: {}
```

## Phase-swapped (bucketed) surfaces

Some servers don't expose one static toolset - they **swap the tool list by
journey phase** (e.g. a "design" phase before a model is loaded, a "run" phase
after). A single `tools/list` snapshot can't see a swap, so point mcp-xray at a
**phases manifest** - one `tools-json` dump per phase:

```yaml
# phases.yaml
phases:
  design: design.json # tools visible before a model is loaded
  run: run.json # tools visible once a model is loaded
```

```bash
mcp-xray analyze --phases phases.yaml
```

The phased report:

- **Headline tax = the worst phase**, not the union - the model only ever carries one phase at a time, so it's not charged for tools it never co-loads.
- **Per-phase surface table** + **carried tools** (those visible in more than one phase = the cross-phase cost).
- **Union analysis** - every distinct tool still gets schema-hygiene + consolidation review.
- **Progressive loading is credited, not flagged** - ≥2 distinct phases means the server already does the JIT pattern the tool would otherwise recommend.

Capture the per-phase dumps with `mcp-xray dump` while the server is in each
phase - or automate the walk with `capture-phases`, which drives the journey in
a single session:

```yaml
# capture.yaml - first phase captured before any call; later phases issue their
# 'advance' tool calls (the ONLY calls made - never inferred), then re-list.
phases:
  - name: design
  - name: run
    advance:
      - tool: load_model
        args: { model_id: "<id>" }
```

```bash
mcp-xray capture-phases --stdio "my-server --multi-model" \
  --capture capture.yaml --out-dir dumps/phases
mcp-xray analyze --phases dumps/phases/phases.yaml
```

## Per-server profiles

The tool (`src/`) is **generic**. Anything specific to a particular MCP server
you're reviewing - captured dumps, phase manifests, golden queries, run outputs -
lives under `profiles/<server>/`, one directory per server. `profiles/` is
**git-ignored**: engagement data stays local and is never committed. Suggested
per-server layout:

```
profiles/<server>/
  dumps/               # captured tools/list snapshots (mcp-xray dump)
  phases.yaml          # phase manifest (for phase-swapped surfaces)
  golden.yaml          # labeled selection queries (--queries)
  call-manifest.yaml   # operator-confirmed safe calls (--call-manifest)
  runs/                # report.json + report.md per audit (fingerprinted)
```

Generic, server-neutral example fixtures live in `tests/fixtures/` (e.g. the
synthetic "Acme Catalog" phased server) - those are part of the product and are
committed.

## Development

```bash
pytest        # static + consolidation paths are fully testable offline
```

`tests/contracts/` pins one frozen-fixture test per wrapped adapter so a silent
upstream format change fails in CI, not in front of a client.

## Status

**v1.4.0 - production instrument.** Everything through the behavioral harness is
shipped:

- **Offline core** - static hygiene (authoritative tokens + smells), consolidation
  (merge/resource candidates, JIT framing), grading, and rendered report. Keyless,
  runs from a `tools/list` dump.
- **Wrapped sensors** - `mcp_checkup` + `token_analyzer` adapters with pinned
  versions and contract tests; measurements only, reconciled against the
  authoritative count.
- **Behavioral** - `noise` probe (selection accuracy / confusability / distraction),
  resumable (`--resume`); before/after `validate` loop; safe result-size probing via
  call-manifest.
- **Phased surfaces** - phase-swapped (bucketed) toolsets, `capture-phases`
  automation, worst-phase headline tax.
- **Replayable runs** - self-contained, fingerprinted run folders you can re-grade
  or re-probe offline.

Remaining roadmap: trace co-occurrence (signal from client call logs +
composite-tool proposals).

## Professional review & commercial use

mcp-xray gives you the grade. Acting on it - prioritising the findings,
remodelling a confusing surface, wiring the `validate` gate into CI so a
regression can't merge - is what [RALFORION](https://ralforion.com) does for a
living.

- **MCP surface review** - we run the full audit against your live servers and
  hand back a prioritised remediation plan (not just a score). Good first step
  if your tool surface is large, phase-swapped, or quietly burning context.
- **Commercial / embedded use** - the [BSL 1.1 license](LICENSE) lets you use
  mcp-xray for any internal purpose, including production. **Embedding it in a
  commercial product, or offering it as part of a paid service, needs a
  commercial license** - reach us via [ralforion.com](https://ralforion.com).

## License

Copyright 2026 [RALFORION d.o.o.](https://ralforion.com)

Licensed under the [Business Source License 1.1](LICENSE). The Licensed Work will convert to Apache License 2.0 on 2030-06-09.

By contributing to this project, you agree to the [Contributor License Agreement](CLA.md).

For commercial licensing inquiries, contact: licensing@ralforion.com

---

<p align="center">
  <a href="https://ralforion.com">
    <img src="docs/assets/RALFORION_doo_Logo.png" alt="RALFORION d.o.o." width="200">
  </a>
</p>
