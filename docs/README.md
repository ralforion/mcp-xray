# mcp-xray docs

Deep-dives on how each probe works and how the grade is computed. The top-level
[`../README.md`](../README.md) is the quick-start; these explain the mechanics so
a report's numbers are legible and challengeable.

## Probes

- **[static-hygiene-probe.md](static-hygiene-probe.md)** - the authoritative,
  keyless core: surface token cost, per-tool leave-one-out, hidden injectors,
  schema smells.
- **[consolidation-probe.md](consolidation-probe.md)** - can the surface be
  smaller? merge / resource / JIT framing.
  - **[merge-candidates.md](merge-candidates.md)** - full derivation of a merge
    candidate, from name decomposition to the rendered proposal.
- **[behavioral-probe.md](behavioral-probe.md)** - the LLM `noise` probe: how
  tool-selection is sampled (prompts, `--samples`) and measured; confusability,
  distraction, and the vague-description confirmer.

## Safety & calls

- **[safe-calls.md](safe-calls.md)** - the "no tool called without a manifest"
  rule; call-manifest (result sizes) vs capture-manifest (phase advance); and
  `--client-config` for wrapped sensors.

## Grading

- **[grading.md](grading.md)** - the single voice: severity assignment, the five
  weighted dimensions, "not measured" handling, and each dimension's formula.

## Quick map: probe → grade dimension

| Probe | Needs | Feeds |
|---|---|---|
| `static_hygiene` | inventory (keyless) | Context Efficiency, Schema Hygiene, Description Quality |
| `consolidate` | inventory (keyless) | Surface Redundancy |
| `noise` | LLM + key | Selection Robustness (+ Description Quality via confusability) |
