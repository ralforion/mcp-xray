# The static-hygiene probe - the authoritative, keyless core

`static_hygiene` is the **trustworthy core** of an audit. It is *owned* (mcp-xray
computes it directly, not a wrapped third-party tool), **deterministic**, and
runs **keyless and offline** from a `tools/list` dump - no live server, no API
key. Its per-tool token figure is the authoritative number every other cost
estimate reconciles against.

It needs only `{inventory}` (a tool list + a token counter; the counter always
exists, falling back to an offline estimator). It emits three families of
finding: **token cost**, **hidden injectors**, and **schema smells**.

---

## 1. Token cost - the "context tax"

Tool definitions (name + description + input schema) are injected into the
model's context on **every turn**, before any work happens. The probe measures
that surface directly by differencing token counts:

```python
empty       = counter.count([])            # baseline: no tools
full        = counter.count(all_tools)      # the whole surface
surface_cost = full - empty                 # the per-turn context tax
```

### Per-tool attribution (leave-one-out)

To find the expensive tools, it removes each tool and re-counts:

```python
per_tool[name] = full - counter.count(all_tools_except[name])
```

That difference is the tool's marginal share of the surface - this is what
surfaces "the schema monster," the one tool whose bloated input schema dominates
the tax. Each per-tool finding also records `share` (fraction of the surface).

### Authoritative vs ESTIMATE

| Backend | How | Authoritative? |
|---|---|---|
| `api` (Anthropic) | the model's real `count_tokens` endpoint | **yes** |
| `api` (OpenAI) | local `tiktoken` (no key, no credits) | **yes** |
| `offline` | heuristic over the serialized schema | **no - flagged ESTIMATE** |

The offline estimate is fine for quick scans but is **never** the headline
number; the report labels it `ESTIMATE (offline)`. Pick the backend with
`--token-backend api --model <client's production model>` - token counts are
model-specific, so the model must match the client's.

---

## 2. Hidden injectors

Tools aren't the only thing a server pushes into context. The probe also flags:

| Injector | Why it matters | Measured |
|---|---|---|
| `server.instructions` | a free-text blob auto-prepended to context | token cost (counted in isolation) + a preview |
| `server.prompts` | server-defined prompts | count + names |
| `server.resources` | auto-listed resources | count + URIs |

`server.instructions` is the costly one - it carries a real per-turn token
charge that's easy to forget, so it's counted and subtracted in the grade just
like a tool.

---

## 3. Schema smells

Per-tool structural/textual problems. Each emits a `schema_smell` finding with a
severity the grader later applies:

| Smell | Rule (`static_hygiene.py`) | Severity |
|---|---|---|
| `missing_description` | empty description | 0.9 |
| `tiny_description` | `< 3` words (`MIN_DESC_WORDS`) | 0.7 |
| `short_description` | `< 6` words (`SHORT_DESC_WORDS`) | 0.4 |
| `vague_description` | contains a filler word (`handle, process, manage, do, stuff, thing, various, etc, helper, util`) | 0.45 |
| `deep_nesting` | input schema nesting depth `≥ 4` | 0.6 |
| `enum_bloat` | an enum with `≥ 12` values | 0.55 |
| `wide_schema` | `≥ 15` properties on one tool | 0.5 |

### The vague-description nuance

`vague_description` is the crudest check (a word list), so it has two guards:

1. **Negation-aware:** `do` is *not* flagged when every occurrence is part of a
   `do not …` guardrail - that's precise guidance, not filler.
2. **Optional LLM confirmer:** when a chat client is configured, the model
   adjudicates whether the flagged words are genuine filler or precise prose. It
   can only ever **remove** a finding, never add one - so the deterministic core
   and authoritative token costs stay LLM-free. Kept findings are tagged
   `llm_confirmed`. (Detail in [`behavioral-probe.md`](behavioral-probe.md) §7.)

`missing/tiny/short/vague` are *description* smells; `deep_nesting/enum_bloat/
wide_schema` are *structural* smells. They feed different grade dimensions
(below).

---

## 4. How it feeds the grade

| Finding | Grade dimension |
|---|---|
| surface + per-tool token cost | **Context Efficiency** (30%) - scored against per-tool (`150` good / `600` bad) and total-surface (`1500` good / `12000` bad) thresholds |
| hidden injectors | shaves Context Efficiency further |
| structural smells (nesting/enum/width) | **Schema Hygiene** (15%) |
| description smells + confusability | **Description Quality** (15%) |

See [`grading.md`](grading.md) for the exact roll-up math.

---

## 5. Running it

```bash
# Offline ESTIMATE (no key) - static + consolidation, rendered as the artifact
mcp-xray analyze --tools-json dump.json

# Authoritative token numbers (model must match the client's production model)
mcp-xray analyze --tools-json dump.json --token-backend api --model claude-opus-4-8
```

This probe always runs (it only needs an inventory); the behavioral and wrapped
probes layer on top when their prerequisites are met.
