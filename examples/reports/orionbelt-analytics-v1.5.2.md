# MCP Surface Review - OrionBelt Analytics

_Generated 2026-06-09T19:20:21+02:00 · MCP server v1.5.2 · mcp-xray v1.4.0 · 23 tools · transport `http` · fingerprint `98764dc9c4b04ff0`_

## Result - Grade B (83.8 / 100)

<details><summary>How the grade works</summary>

- The **overall score** (0–100) is the weighted average of the five scorecard dimensions, over only the ones actually measured - a skipped probe drops its weight and is reported "not measured," never counted as zero. So a grade with selection robustness unmeasured reflects ~75% of the rubric.
- **Letter bands:** A = 90–100 (A+ ≥97, A- 90–92), B = 80–89, C = 70–79, D = 60–69, F = below 60.
- A grade is a *relative* read on surface quality, not a pass/fail - the actionable detail is in the findings below (per-tool cost, consolidation, behavioral), and every recommendation traces back to one.

</details>

**Context tax:** 6,777 tokens/turn (~\$33.88/1k turns at \$5.00/Mtok input) - _authoritative_
_Priced against model: `claude-opus-4-8` (list price \$5.00/Mtok input, built-in table)._

## Scorecard

| Dimension | Score | Weight | Notes |
|---|---|---|---|
| Context Efficiency | 56 | 30% | 6777 surface tokens, 294.7/tool (authoritative) |
| Surface Redundancy | 80 | 15% | 2 merge candidate(s), 0 duplicate(s) |
| Schema Hygiene | 100 | 15% | 0 structural smell(s) over 23 tools |
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

## Per-tool context cost - all 23 tools

| Tool | Tokens | Share |
|---|---:|---:|
| `generate_chart` | 731 | 11% |
| `apply_semantic_names` | 554 | 8% |
| `execute_sql_query` | 504 | 7% |
| `load_my_ontology` | 412 | 6% |
| `generate_ontology` | 380 | 6% |
| `query_sparql` | 351 | 5% |
| `graphrag_search` | 327 | 5% |
| `save_semantic_model` | 290 | 4% |
| `suggest_semantic_names` | 264 | 4% |
| `get_table_details` | 254 | 4% |
| `discover_schema` | 235 | 4% |
| `download_artifact` | 231 | 3% |
| `add_rdf_knowledge` | 230 | 3% |
| `connect_database` | 228 | 3% |
| `sample_table_data` | 226 | 3% |
| `cleanup_workspace` | 208 | 3% |
| `store_ontology_in_rdf` | 205 | 3% |
| `graphrag_query_context` | 187 | 3% |
| `graphrag_find_join_path` | 178 | 3% |
| `get_semantic_model` | 157 | 2% |
| `reset_cache` | 149 | 2% |
| `list_semantic_models` | 97 | 1% |
| `list_schemas` | 89 | 1% |

## Hidden context injectors

- **instructions** (`server.instructions`), ~1005 tokens - _per turn_
- **resources** (`server.resources`), ~498 tokens - _on-demand (listing only, not graded)_

## Consolidation proposals

_Framed as one of three: **merge**, **convert to resource**, or **switch to JIT loading**._

### Merge candidates

| Tools | Proposal | Tokens saved | Complexity | Flag |
|---|---|---:|---:|---|
| `suggest_semantic_names`, `apply_semantic_names` | manage_semantic_names(action=apply/suggest) | 554 | 0.18 | ⚠ splits read+write |
| `save_semantic_model`, `get_semantic_model` | manage_semantic_model(action=get/save) | 447 | 0.18 | ⚠ splits read+write |

### Read-only tools - could be MCP resources

_**Advisory, not a checklist.** 3 pure-read tool(s) take **no key parameter**, so they could map to static MCP resources - removing them from the tool-selection space - **if** the data is host-surfaced or the client supports resources. Reads parameterized over a **dynamic keyspace** (e.g. `get_x(model_id)` where ids are discovered at runtime - the common case for data / semantic-layer servers) are **not listed**: they should stay tools. Treat this as a lens, not a to-do._

<details><summary>The 3 pure-read tools</summary>

- `list_schemas` (list) - no parameters; maps to a static `resource://` document
- `list_semantic_models` (list) - no parameters; maps to a static `resource://` document
- `query_sparql` (query)

</details>

### Architectural alternative - JIT / progressive loading

Large static surface -- consider just-in-time / progressive tool loading as an alternative to (or alongside) merging.

---

## Methodology & coverage

- **Probes run:** static_hygiene, consolidate, noise
- **Skipped (not measured):** mcp_checkup (missing: config_path); token_analyzer (missing: config_path)
- **Token figure:** api backend - authoritative (Anthropic count_tokens)
- **Model used:** `claude-opus-4-8`: token counting; behavioral probe (7 samples)
- **Server fingerprint:** `98764dc9c4b04ff0` (re-audits keyed to this for drift)
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
