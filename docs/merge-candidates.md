# How merge candidates are elaborated

A *merge candidate* is the consolidation probe's claim that two or more tools
could collapse into one without losing capability. This doc traces the full
derivation - from a raw `tools/list` entry to the rendered proposal - so you can
read (and trust, or challenge) any merge in a report.

It is entirely **structural and lexical**: names, schemas, and call shapes. No
LLM, no semantics. Everything below is deterministic.

---

## Step 0 - derive per-tool signals (`inventory.py`)

Before any pairing, each tool is decomposed once:

### (verb, resource) from the name - `_decompose_name`

```
create_label   → ("create", "label")      # snake_case: head verb, rest = resource
searchThreads  → ("search", "threads")    # camelCase split
ping           → ("ping", "")              # no resource
```

### behavior - `_classify_behavior`

The verb is bucketed against three sets:

| Bucket | Verbs |
|---|---|
| `read` | get, list, read, fetch, search, find, show, describe, lookup, query |
| `write` | create, update, set, add, put, patch, label, unlabel, move, rename, send, draft |
| `destructive` | delete, remove, destroy, purge, drop, clear, wipe, trash |

Unknown verb → defaults to `write` (conservative for safety). Any destructive
keyword anywhere in the name forces `destructive`.

### call shape - `SchemaFeatures`

```python
property_names : frozenset of top-level input property keys
type_signature : sorted tuple of (property_name, json_type) pairs   # the "call shape"
```

`type_signature` is the strong identity: two tools with the **same** signature
take structurally identical arguments.

---

## Lens A - resource families (the common case)

**Goal:** collapse a CRUD family on one resource into `manage_<resource>(action=…)`.

1. **Group** every tool by its `resource` (`get_label`, `create_label`,
   `delete_label` → group `label`).
2. **Phase gate** (phased servers only): split each group by identical phase
   membership - you can only merge tools surfaced in the *same* phases, else the
   merged tool would over-/under-expose an action in some phase. Non-phased runs
   are one group.
3. A group of **≥ 2** tools becomes a `merge_candidate`. The probe records:

   | field | meaning |
   |---|---|
   | `verbs` | the distinct actions → the proposed `action` enum |
   | `union_width` | `len(verbs)` - how many actions the merged tool juggles |
   | `complexity_delta` | `min(1, (union_width−1) × 0.18)` - call-difficulty the enum adds |
   | `wide_union` | `union_width ≥ 4` (a flagged, weaker recommendation) |
   | `tokens_saved_est` | see *Token estimate* below |
   | `mixes_destructive` | true if any member is destructive (you may *not* want delete behind the same tool) |

4. **Proposal** (rendered in the report): `manage_label(action=create/delete/get/update)`.

Confidence is a flat `0.8` for families.

### Worked example

Given `create_label`, `update_label`, `delete_label`, `get_label`:

- resource = `label`, verbs = `[create, delete, get, update]`
- `union_width = 4` → `wide_union = true`, `complexity_delta = min(1, 3×0.18) = 0.54`
- `mixes_destructive = true` (delete_label)
- proposal: `manage_label(action=create/delete/get/update)`

The report shows this as a merge with a *caveat*: wide union + mixes destructive,
so it's a softer recommendation than a clean 2-way merge.

---

## Lens B - pairwise, cross-resource shape matches

Families miss redundancy that doesn't share a resource. A pairwise pass
(`itertools.combinations` over all tools) catches tools with an **identical call
shape** across resources, but only on a strict, layered filter - each gate must
pass or the pair is dropped:

1. **Skip same-resource pairs** - already covered by Lens A.
2. **Phase-neutral only** - `a` and `b` must be surfaced in the same phases.
3. **Identical `type_signature`** - same `(name, type)` pairs, or no pairing.
   This is the strong signal.
4. **Score ≥ `MERGE_THRESHOLD` (0.45)**:

   ```python
   merge_score = 0.4·name_aff + 0.4·schema_aff + 0.2·sig_aff
   #   name_aff   = 1 if same resource OR same verb, else 0
   #   schema_aff = Jaccard(a.property_names, b.property_names)   # |∩| / |∪|
   #   sig_aff    = 1 if identical non-empty type_signature, else 0
   ```

5. **Require `name_aff` > 0** - the decisive guard. Identical shape *alone* pairs
   tools that merely share an `{id}` param (`delete_label` + `get_thread` both
   take one string id). Demanding a shared verb or resource kills those
   coincidences. Synonym-verb redundancy (`get`/`fetch`/`lookup` doing the same
   thing) is deliberately *not* a structural merge - that's the LLM confusability
   proxy's job (see [`behavioral-probe.md`](behavioral-probe.md)).

6. **Dedupe** by tool-name pair so a pair isn't reported twice.

Confidence scales with the score: `min(0.9, 0.4 + score/2)`.

### Worked example

`get_user(id: str)` and `fetch_account(id: str)`:

- different resources (`user` vs `account`) → not caught by Lens A
- identical signature `(("id","string"),)` → gate 3 passes
- `schema_aff = 1.0` (same property set), `sig_aff = 1.0`
- but `name_aff = 0` (different verb `get`/`fetch`, different resource) → **dropped**

So this pair does *not* fire structurally - by design. If instead they were
`get_user(id)` and `get_session(id)` (shared verb `get`), `name_aff = 1`,
`score = 0.4 + 0.4 + 0.2 = 1.0`, and it fires.

---

## Token estimate - what "saved" means

```python
# keep the single largest tool's definition; everything else is recovered
tokens_saved_est = sum(per_tool_cost) − max(per_tool_cost)
```

Computed on the **same counter** the static-hygiene probe uses (authoritative
when a key/model is set, ESTIMATE otherwise), so a merge's savings reconcile
with the report's headline context tax. A 4-tool family where each costs ~250
tokens saves ~750 (keep one, drop three).

---

## From measurement to grade

The grader assigns each merge a severity, then rolls all of them into **Surface
Redundancy** (15% of the grade):

```python
severity = 0.4 + clamp(tokens_saved / 800) · 0.4   # bigger saving → higher severity
if wide_union: severity ·= 0.7                       # wide enum is a weaker rec
...
SurfaceRedundancy = 100 − clamp(Σ severity / 4) · 100   # saturating; no merges → 100
```

So a few small, clean merges barely move the score; several large CRUD families
drag it down hard. See [`grading.md`](grading.md).

---

## What merge candidates are *not*

- **Not a semantic judgment.** Two tools doing "the same thing" with unrelated
  names and shapes won't be caught here - that's the behavioral probe's
  confusability signal.
- **Not an order to merge.** A merge with `mixes_destructive` or `wide_union` is
  surfaced *with* its downside; the call is yours.
- **Not blind to phases.** A tool only live in one journey phase is never merged
  with one from another phase.

Use `mcp-xray validate --before base.json --after merged.json --queries golden.yaml`
to measure a proposed merge's real effect - tokens saved *and* whether selection
accuracy survives the collapse.
