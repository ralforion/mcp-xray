# Grading - the single voice

Grading is the **only** place interpretation happens. Probes emit *measurements*
(numbers); the grader assigns every finding a `severity`, rolls findings into
five weighted dimensions, and produces a 0–100 score and letter grade. Wrapped
third-party tools contribute numbers only - their recommendation text never
enters here.

---

## The five dimensions

| Dimension | Weight | Source probe | Lower when… |
|---|---|---|---|
| **Context Efficiency** | 30% | static_hygiene | the surface costs many tokens per turn |
| **Selection Robustness** | 25% | noise (LLM) | the model mis-selects or fires off-domain |
| **Surface Redundancy** | 15% | consolidate | tools overlap / duplicate |
| **Schema Hygiene** | 15% | static_hygiene | structural schema smells (nesting/enum/width) |
| **Description Quality** | 15% | static_hygiene + noise | missing/vague descriptions or confusable tools |

---

## How the overall score is computed

```python
# 1. assign severity to every finding (engine owns this, not probes)
# 2. compute each dimension's 0–100 subscore
# 3. weighted average over MEASURED dimensions only, re-normalized:
measured = {d: s for d, s in subs if s.measured}
overall  = Σ(score · weight) / Σ(weight)   over measured
```

A probe that didn't run (e.g. no LLM → no Selection Robustness) **drops its
weight** and is reported **"not measured"** - never scored zero. So a keyless
offline run is graded on ~75% of the rubric (the 25% selection weight is
removed), not penalized for the missing probe.

### Letter bands

```
A+ ≥97   A 93–96   A- 90–92
B+ 87–89  B 83–86   B- 80–82
C+ 77–79  C 73–76   C- 70–72
D+ 67–69  D 63–66   D- 60–62
F  < 60
```

A grade is a **relative** read on surface quality, not pass/fail - the
actionable detail is always in the findings, and every recommendation traces
back to one.

---

## Severity per finding kind

The grader maps each finding to a 0–1 severity (`Grader._severity`):

| Finding | Severity |
|---|---|
| `token_cost` (surface) | `1 − lerp(per_tool_avg, good=150, bad=600)` |
| `token_cost` (per-tool) | `clamp(share × 3)` - share of total surface |
| `schema_smell` | fixed per smell (missing 0.9, tiny 0.7, vague 0.45, nesting 0.6, enum 0.55, width 0.5, short 0.4) |
| `merge_candidate` | `0.4 + clamp(saved/800)·0.4`, ×0.7 if `wide_union` |
| `selection_error` | `1 − pass_rate` |
| `distraction` | `fire_rate` |
| `hidden_injector` | `clamp(tokens_est / 1000)` |
| `resource_candidate` | `0.3` (advisory) |
| `jit_candidate` | `0.5` if recommended, else `0.1` |

`lerp(value, good, bad)` maps `≤good → 100`, `≥bad → 0`, linear between.

---

## Each dimension's formula

### Context Efficiency (30%)
```python
s_per_tool = lerp(tokens_per_tool_avg, good=150, bad=600)
s_total    = lerp(surface_tokens,      good=1500, bad=12000)
score      = 0.6·s_per_tool + 0.4·s_total
score     −= clamp(injector_tokens / 2000) · 10     # hidden injectors shave points
```
Phased servers are scored on the **worst phase** (what's carried per turn), not
the union - and the row names that phase.

### Selection Robustness (25%) - *requires the LLM probe*
```python
# with labeled queries:
base = avg(pass_rate) · 100
# proxy-only (no labels):
base = 100 − clamp(#confusable_tools / #tools) · 60
score = base − clamp(Σ fire_rate / 2) · 40          # distraction penalty
```
Detail: [`behavioral-probe.md`](behavioral-probe.md).

### Surface Redundancy (15%) - *requires the consolidate probe*
```python
score = 100 − clamp(Σ severity(merges + duplicates) / 4) · 100   # no redundancy → 100
```
Detail: [`merge-candidates.md`](merge-candidates.md).

### Schema Hygiene (15%)
```python
# structural smells only (nesting / enum / width); descriptions go elsewhere
score = 100 − clamp(Σ severity(structural_smells) / #tools) · 100
```

### Description Quality (15%)
```python
score = 100 − clamp((Σ severity(desc_smells) + Σ severity(confusability)) / #tools) · 100
```
Combines static description smells with the LLM confusability signal - a
description is "bad" both when it's vague *and* when it makes the model pick the
wrong tool.

---

## Why this design

One voice, many sensors: a probe can be added, removed, or fail, and the grade
stays coherent because **interpretation lives in exactly one module**. Numbers
in, severity assigned centrally, weights re-normalized over what actually ran.
That's also why the static (deterministic) and behavioral (sampled) halves can
coexist without the sampling noise contaminating the authoritative token math -
they're separate dimensions with separate weights.
