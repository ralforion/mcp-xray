# The consolidation probe - can the surface be smaller?

`consolidate` is the differentiator: it answers *"does this server need all these
tools?"* It is **owned**, **deterministic**, and needs **no LLM** in v0.1 - merge
signals are structural and lexical (names, schemas, type signatures), not
semantic. It needs only `{inventory}`.

Every proposal is framed as one of three architectural moves: **merge**,
**resource**, or **JIT** (just-in-time loading).

---

## 1. Capability lens → merge

### Resource families (CRUD collapse)

Tools are grouped by the **resource** they act on (parsed from the name, e.g.
`create_label`, `delete_label`, `update_label` → resource `label`). A family of
≥2 tools on one resource is proposed for collapse into a single polymorphic
tool:

```
manage_label(action=create/delete/update)
```

Each merge finding records `tokens_saved_est`, the action-enum `union_width`, a
`complexity_delta` (how much call-difficulty the polymorphic enum adds), and a
`wide_union` flag when the enum gets unwieldy (`≥ 4` actions). If any member is
`destructive`, that's flagged too - you usually don't want delete hiding behind
the same tool as read.

### Pairwise (cross-resource shape)

Some redundant tools don't share a resource but have an **identical call shape**.
A pairwise pass proposes merges only on the *strong* signal:

```python
score = 0.4*name_aff + 0.4*schema_aff + 0.2*sig_aff   # merge_score()
# name_aff : 1 if same resource OR same verb
# schema_aff: Jaccard overlap of property names
# sig_aff  : 1 if identical type signature
```

A pair must clear `MERGE_THRESHOLD = 0.45` **and** share an exact type signature
**and** share a verb or resource. The last guard matters: identical shape alone
pairs unrelated tools that merely share an `{id}` param (`delete_label` +
`get_thread`) - synonym-verb redundancy (`get`/`fetch`/`lookup`) is left to the
LLM confusability proxy, not claimed as a structural merge.

---

## 2. Behavioral lens → resource candidates

A **pure read** (`get_`/`list_` with no side effects) could often be an MCP
*resource* rather than a tool - removing it from the tool-selection space
entirely. The probe flags these, but with two important exclusions:

- **Dynamic keyspace reads stay tools.** A read whose schema carries an `id`,
  `uuid`, `key`, `name`, `model`, `session`, `dataset`, … param
  (`_KEY_PARAM` regex) is keyed over runtime-discovered values that a static
  `resource://{id}` template can't enumerate. These are **excluded** so the list
  isn't a misleading to-do.
- **Clean map** = a *parameterless* read maps cleanly to a static URI
  (`confidence 0.75`); a read with params is a weaker candidate (`0.55`).

This is advisory - it depends on whether the host surfaces the data and whether
the client supports resources.

---

## 3. Architectural alternative → JIT

Merging shrinks a surface; **just-in-time loading** is the other answer - expose
tools on demand per phase so the model never carries the whole union. `_jit_detect`
is a *framed signal, not a verdict*:

- `looks_dynamic` if the inventory advertises dynamic loading **or** has
  meta-tools (`enable_tool`, `load_tool`, `list_tools`, `activate`, `toolset`).
- `recommend_jit` when a surface is **big and static**: `≥ 15` tools and no
  dynamic markers.

For a phase-swapped server, JIT flips from *recommendation* to *credit* - the
server already does the pattern (see the phased-audit section in the main
README).

---

## 4. Token-savings estimate

```python
# keep the single largest tool's cost; the rest is what you save
tokens_saved = sum(per_tool_costs) - max(per_tool_costs)
```

Computed on the **same counter** the static-hygiene probe uses, so the savings
reconcile with the headline context tax.

---

## 5. Phase awareness

Merging is only safe when every merged tool is surfaced in the **same phases** -
otherwise the merged tool would over- or under-expose an action in some phase
(e.g. forcing a run-only `describe` into the design phase). On a phased run the
probe partitions tools by identical phase membership and only proposes merges
*within* a group. A non-phased run is one group, so this is a no-op there.

---

## 6. How it feeds the grade

All consolidation findings roll into **Surface Redundancy** (15%): merge
candidates and duplicates lower it by summed severity (saturating); no
redundancy → 100. `resource_candidate` and `jit_candidate` are advisory framing
and carry low severity. See [`grading.md`](grading.md).

```bash
# just the capability-reduction analysis
mcp-xray consolidate --tools-json dump.json

# validate a proposed merge: tokens + selection accuracy, before vs after
mcp-xray validate --before base.json --after merged.json --queries golden.yaml --model claude-opus-4-8
```
