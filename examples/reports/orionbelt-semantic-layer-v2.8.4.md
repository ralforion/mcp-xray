# MCP Surface Review - OrionBelt Semantic Layer

_Generated 2026-06-09T19:09:52+02:00 · MCP server v2.8.4 · mcp-xray v1.4.0 · 18 tools · transport `phases` · fingerprint `bb339b9532ececb5`_

## Result - Grade B- (82.7 / 100)

<details><summary>How the grade works</summary>

- The **overall score** (0–100) is the weighted average of the five scorecard dimensions, over only the ones actually measured - a skipped probe drops its weight and is reported "not measured," never counted as zero. So a grade with selection robustness unmeasured reflects ~75% of the rubric.
- **Letter bands:** A = 90–100 (A+ ≥97, A- 90–92), B = 80–89, C = 70–79, D = 60–69, F = below 60.
- A grade is a *relative* read on surface quality, not a pass/fail - the actionable detail is in the findings below (per-tool cost, consolidation, behavioral), and every recommendation traces back to one.

</details>

**Context tax:** 5,607 tokens/turn (~\$28.04/1k turns at \$5.00/Mtok input) - _authoritative_
_Priced against model: `claude-opus-4-8` (list price \$5.00/Mtok input, built-in table)._
_Phased surface: tax shown is the **worst phase** (`run`) - the 18-tool union is never co-loaded._

## Scorecard

| Dimension | Score | Weight | Notes |
|---|---|---|---|
| Context Efficiency | 58 | 30% | 5607 surface tokens, 350.4/tool, worst phase `run` (authoritative) |
| Surface Redundancy | 69 | 15% | 2 merge candidate(s), 0 duplicate(s) |
| Schema Hygiene | 100 | 15% | 0 structural smell(s) over 16 tools |
| Description Quality | 100 | 15% | 0 description smell(s), 0 confusable |
| Selection Robustness | 100 | 25% | 0 labeled, 0 distraction failure(s) |

<details><summary>What the dimensions mean</summary>

_Each is 0–100; the grade is their weighted average over the dimensions actually measured (skipped probes drop their weight, never scored zero)._

- **Context Efficiency** (30%) - Tokens the tool surface injects into context every turn, before any work - scored against per-tool and total-surface thresholds. Lower is better.
- **Surface Redundancy** (15%) - Overlapping or duplicate tools the model must disambiguate. Merge candidates and duplicate detections lower it.
- **Schema Hygiene** (15%) - Structural quality of input schemas - deep nesting, oversized enums, or too many parameters drag it down.
- **Description Quality** (15%) - Whether tool descriptions are present, specific, and distinct. Missing/vague descriptions and model confusability lower it.
- **Selection Robustness** (25%) - Whether the model picks the right tool and stays quiet on off-domain prompts. Needs the LLM probe; 'not measured' without one.

</details>

## Phase surfaces

_Tool list swaps by journey phase - the model carries one phase at a time._

| Phase | Tools | Tokens/turn | Tokens/tool |
|---|---:|---:|---:|
| `run` | 16 | 5,607 | 350.4 |
| `design` | 6 | 2,421 | 403.5 |

**Carried across phases** (4): `get_json_schema`, `load_model`, `remove_model`, `run_batch`

## Per-tool context cost - all 18 tools

| Tool | Tokens | Share | Phases |
|---|---:|---:|---|
| `run_batch` | 864 | 15% | design, run |
| `load_model` | 748 | 13% | design, run |
| `execute_query` | 658 | 11% | run |
| `find_artefacts` | 613 | 10% | run |
| `export_model_to_osi` | 294 | 5% | run |
| `list_examples` | 284 | 5% | run |
| `get_model_diagram` | 273 | 5% | run |
| `query_model_graph_by_sparql` | 243 | 4% | run |
| `explain_artefact` | 239 | 4% | run |
| `get_model_graph` | 207 | 4% | run |
| `get_json_schema` | 202 | 4% | design, run |
| `describe_model` | 182 | 3% | run |
| `get_join_graph` | 178 | 3% | run |
| `get_example` | 168 | 3% | run |
| `get_obml_reference` | 148 | 2% | design |
| `remove_model` | 100 | 2% | design, run |
| `list_dialects` | 69 | 1% | design |
| `list_models` | 64 | 1% | run |

## Hidden context injectors

- **prompts** (`server.prompts`), ~523 tokens - _on-demand (listing only, not graded)_
- **resources** (`server.resources`), ~376 tokens - _on-demand (listing only, not graded)_

## Consolidation proposals

_Framed as one of three: **merge**, **convert to resource**, or **switch to JIT loading**._

### Merge candidates

_Only tools surfaced in the same phase(s) are merged - a merge must not change what any phase exposes._

| Tools | Phase | Proposal | Tokens saved | Complexity | Flag |
|---|---|---|---:|---:|---|
| `get_join_graph`, `get_model_graph` | run | merge | 468 | 0.18 |  |
| `load_model`, `remove_model` | design+run | manage_model(action=load/remove) | 390 | 0.18 | ⚠ mixes destructive |

### Read-only tools - could be MCP resources

_**Advisory, not a checklist.** 3 pure-read tool(s) take **no key parameter**, so they could map to static MCP resources - removing them from the tool-selection space - **if** the data is host-surfaced or the client supports resources. Reads parameterized over a **dynamic keyspace** (e.g. `get_x(model_id)` where ids are discovered at runtime - the common case for data / semantic-layer servers) are **not listed**: they should stay tools. Treat this as a lens, not a to-do._

<details><summary>The 3 pure-read tools</summary>

- `get_obml_reference` (get) - no parameters; maps to a static `resource://` document
- `list_dialects` (list) - no parameters; maps to a static `resource://` document
- `list_models` (list) - no parameters; maps to a static `resource://` document

</details>

### Progressive tool loading - ✓ already in place

Server already uses progressive (phase-scoped) tool loading: the model carries one phase at a time, not the full union. This is the recommended pattern -- credited, not flagged.

Phase tool counts: `design`=6, `run`=16

---

## Methodology & coverage

- **Probes run:** static_hygiene, consolidate, noise
- **Skipped (not measured):** mcp_checkup (missing: config_path); token_analyzer (missing: config_path)
- **Token figure:** api backend - authoritative (Anthropic count_tokens)
- **Model used:** `claude-opus-4-8`: token counting; behavioral probe (7 samples)
- **Server fingerprint:** `bb339b9532ececb5` (re-audits keyed to this for drift)
- **mcp-xray v1.4.0**

<details><summary>Glossary</summary>

- **Surface tokens / Context tax** - Tokens the tool definitions (name + description + input schema) add to the model's context on every turn - computed as count_tokens(all tools) − count_tokens(no tools). Paid whether or not any tool is actually called.
- **Per-tool cost (leave-one-out)** - A single tool's share of the surface, measured as count(all tools) − count(all tools except this one).
- **Authoritative vs ESTIMATE** - Authoritative = the model's real tokenizer (Anthropic count_tokens, or OpenAI tiktoken locally). ESTIMATE = an offline heuristic over the serialized schema - fine for quick scans, never the headline number.
- **Schema smell** - A structural input-schema problem: deep nesting, an oversized enum, too many parameters, or a missing/short/vague description.
- **Merge candidate** - Two or more tools similar enough to collapse into one - e.g. a CRUD family (create/update/delete_x) into manage_x(action=…). Scored as tokens saved vs. added call-complexity.
- **Resource candidate** - A pure-read tool (get_/list_) that could be exposed as an MCP resource instead, removing it from the tool-selection space. Advisory - depends on access pattern and a dynamic keyspace may have to stay a tool.
- **Confusability (proxy)** - When the model picks the wrong tool given a tool's own description as the prompt - a no-LLM-ground-truth signal that two tools overlap.
- **Distraction** - A tool firing on an off-domain prompt it has no business handling - a sign of over-broad descriptions.
- **Selection robustness** - Whether the model picks the right tool and stays quiet on off-domain prompts. Requires the LLM probe; otherwise 'not measured'.
- **Phase / carried tools** - A phase-swapped server exposes different tools by journey phase (e.g. setup vs. run). 'Carried' tools are visible in more than one phase - the cross-phase cost.
- **Progressive / JIT loading** - Exposing tools on demand per phase instead of all at once, so the model never carries the full union - the per-turn tax is the worst single phase.
- **Fingerprint** - A hash of the tool inventory; keys a run so re-audits of the same server reveal drift.
- **Call manifest** - An operator-supplied list of tool calls mcp-xray is allowed to actually execute (--call-manifest). No tool is ever called without it - it's your signed permission slip that these calls are read-only/sandbox-safe.
- **Result size** - How big a tool's OUTPUT is (chars + bytes), measured by calling each manifested tool once. Tool outputs are fed back to the model and cost context on every call - a cost the static surface scan can't see.

</details>
