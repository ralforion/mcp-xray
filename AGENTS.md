# AGENTS.md

This file provides guidance to coding agents when working with code in this repository.

## What this is

`mcp-xray` audits MCP servers. Point it at a live server (stdio/http/sse) or an
offline `tools/list` dump and it produces **one graded report** (0-100 + letter)
covering: per-turn token tax, behavioral noise (wrong-tool selection /
distraction), and capability reduction (merge / resource / JIT candidates).

The PyPI distribution is `mcp-xray-audit`; the import package is `mcp_xray` and
the CLI is `mcp-xray`. The tool in `src/` is **generic** - anything specific to a
server under review lives under `profiles/<server>/`, which is git-ignored.

## Commands

```bash
pip install -e ".[dev]"      # everything + pytest (api, openai, live extras)
pytest                       # full suite; static + consolidation paths run offline/keyless
pytest tests/test_noise.py   # single file
pytest tests/test_noise.py::test_name -q   # single test
ruff check src tests         # lint (CI runs exactly this)
```

CI (`.github/workflows/ci.yml`) runs `pytest -q` and `ruff check src tests`.

Extras gate functionality, not just deps: `[api]` (anthropic) enables
authoritative token counting + LLM probes; `[openai]` (openai+tiktoken) enables
the OpenAI vendor; `[live]` (mcp) enables stdio/http/sse transports. Core install
is intentionally dependency-light (PyYAML only) so the offline half needs no key
and no server.

## Architecture

The pipeline is **sensors -> normalized findings -> single grading voice**. The
load-bearing invariant: **probes emit measurements only; all interpretation
happens in `grade.py`.** Never put recommendation prose or severity into a probe.

Data flow (`orchestrator.py` is the spine):

1. **`connect.py`** collapses any transport (tools-json dump, stdio, http, sse)
   into an **`Inventory`** of `Tool`s.
2. **`inventory.py`** derives schema features once (verb/resource decomposition
   via `READ_VERBS`/`WRITE_VERBS`/`DESTRUCTIVE_VERBS`, nesting depth, enum sizes)
   so probes never re-parse raw schemas.
3. **`orchestrator.build_context`** assembles a `RunContext` and a token
   **`counter`** (`counting.py`). `run` / `run_phased` discover probes, run only
   those whose `requires()` capabilities are satisfied, and record the rest as
   skipped-with-reason ("not measured," **never scored zero**).
4. Probes emit **`Finding`** objects (`finding.py`) - the universal currency.
   `measurement` is **numbers only, no prose**; `severity` is left at 0 for the
   grader to assign.
5. **`grade.py`** assigns severity, rolls findings into five weighted dimensions
   (context_efficiency 30%, selection_robustness 25%, surface_redundancy 15%,
   schema_hygiene 15%, description_quality 15%) -> 0-100 -> letter.
6. **`report.py`** renders markdown and writes self-contained, fingerprinted run
   folders.

### Probes (`probes/`)

- **`static_hygiene.py`** (owned, authoritative): per-tool leave-one-out token
  cost, hidden injectors, schema smells. Needs only `inventory`.
- **`consolidate.py`** (owned): merge / resource / JIT candidates. Needs only
  `inventory`.
- **`noise.py`** (owned): behavioral selection accuracy / confusability /
  distraction. Needs `llm` (+ `api_key`); resumable via `--resume`.
- **`token_tax/mcp_checkup.py`, `token_tax/token_analyzer.py`** (wrapped): adapt
  external binaries; **measurements only**, reconciled against the in-house
  authoritative count. They run only with `--client-config` and the binary
  installed. Each is pinned by a frozen-fixture contract test in
  `tests/contracts/` so a silent upstream format change fails in CI.

### The probe contract (`probes/base.py`)

Every sensor implements `requires() -> set[str]` over the `CAPABILITIES` token
set (`inventory`, `live_server`, `api_key`, `llm`, `config_path`,
`call_manifest`, `queries`). To add a probe: subclass `Probe`, declare
`requires()`, emit `Finding`s with a `kind` from `finding.KINDS`, register it in
`orchestrator.default_probes()`. If grading should react to it, extend `grade.py`
- the probe itself stays interpretation-free.

### Multi-vendor

`vendors.py` maps a model id to a vendor; `counting.py` has per-vendor counters
(`ApiCounter` for Anthropic `count_tokens`, `TiktokenCounter` for OpenAI,
`OfflineCounter` as a flagged heuristic ESTIMATE that is never the headline
number); `chat.py` has per-vendor `ChatClient`s for the behavioral probe. The
`--model` must match the client's production model for token figures to be
authoritative.

### Phased (bucketed) surfaces

`phases.py` models servers that swap their tool list by journey phase. Headline
tax = **worst single phase**, not the union (the model only ever carries one
phase). `capture-phases` automates walking a live server through phases. Progressive
loading is **credited, not flagged**.

## CLI subcommands (`cli.py`)

`analyze` (full audit), `consolidate` (capability-reduction only), `validate`
(before/after merge harness: tokens + selection accuracy), `dump` (live server ->
tools-json), `capture-phases` (drive a live server through phases), `report`
(re-render markdown from a stored run). Each `cmd_*` function maps to a subparser.

Auth headers for live http/sse: repeatable `--header "Name: value"`, or the
`MCP_XRAY_HTTP_HEADER` env var (keeps tokens out of `ps`/shell history).

## Conventions

- Run folders are self-contained and replayable: `analyze` writes the input under
  `<run>/dumps/`, so a past run can be re-graded fully offline. Runs are
  fingerprinted for drift.
- The grading dimension weights in `grade.py` (`DIMENSIONS`) and their help text
  (`DIMENSION_HELP`) are kept adjacent - update both together.
- Generic, server-neutral fixtures (e.g. the synthetic "Acme Catalog") live in
  `tests/fixtures/` and are committed; real engagement data goes in git-ignored
  `profiles/`.
- License headers / commercial framing: this is BSL-1.1, not open-source-default.
- Never use em-dashes in prose, docs, comments, or generated reports.

## Reference

Design intent lives in `design/MCP_XRAY_PLAN.md` (the `PLAN S*` markers in
docstrings point here). Per-probe deep-dives are in `docs/`.
