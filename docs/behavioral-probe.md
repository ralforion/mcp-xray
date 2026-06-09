# The behavioral probe - how tool-selection is sampled and measured

The `noise` probe is the behavioral half of an mcp-xray audit. The static and
consolidation probes read the tool *surface*; the behavioral probe asks a real
model to *use* it, and measures how well the surface steers tool selection.

It needs an LLM (`--llm` + an API key). Without one it is reported **"not
measured"** - never scored zero.

> **One-line mental model:** every "sample" is one real model API call with all
> the tools attached; we only record *which tool the model picks* (the tool is
> never executed), repeat it N times, and report the **pass rate** - not a
> single yes/no.

---

## 1. A sample is one API call - and the tool is never run

Each sample is a single `messages.create` call (`chat.py → AnthropicChat.pick_tool`)
with the **entire tool surface** attached and a forced choice:

```python
resp = client.messages.create(
    model=...,
    max_tokens=256,
    system=system,                 # see §3
    tools=tools,                   # all N tool definitions
    tool_choice={"type": "any"}    # MUST call exactly one tool
              if not allow_none    #   ... or, for distraction:
              else {"type": "auto"},  # MAY decline (call nothing)
    messages=[{"role": "user", "content": query}],
)
# measured signal = the NAME of the returned tool_use block (or None)
```

The **only** thing recorded is *which tool name* comes back. Arguments are
ignored and **no tool is ever invoked** - so the probe needs no database, no
credentials, and no side effects. It measures *selection*, not *execution*.

For OpenAI models the same contract is implemented with
`tool_choice="required"` / `"auto"` (`chat.py → OpenAIChat`).

---

## 2. Three modes

| Mode | When | User prompt | Correct outcome |
|---|---|---|---|
| **Labeled selection** | `--queries` supplied | your golden prompt verbatim | the model picks an `expected_tool` |
| **Confusability proxy** | no labels (default) | synthesized from each tool's *own* description | the model picks *that same* tool |
| **Distraction** | always | 4 fixed off-domain tasks | the model calls **nothing** |

### Labeled selection (`--queries`)
Real user phrasings with known answers. The strongest signal, because the
prompts are realistic rather than synthesized:

```yaml
queries:
  - query: "create a new label called Work"
    expected_tools: [create_label]
```

### Confusability proxy (no ground truth)
When you have no labeled queries, the probe manufactures one *per tool* from the
tool's **own description**, then checks whether the model picks that tool back.
If, handed a tool's own words, the model reaches for a *different* tool, the two
descriptions objectively fail to disambiguate - they are **confusable**.

```python
# noise.py
desc = (tool.description or tool.name).strip().rstrip(".")
query = f"I need to: {desc}. Which tool should I use?"
```

### Distraction
Four off-domain prompts a focused server should ignore entirely
(`tool_choice: auto`, so declining is allowed):

```
What's 17 times 23?
Translate 'good morning' into French.
Write a haiku about the ocean.
Convert 5 miles to kilometers.
```

Any tool firing here is a **distraction** finding - a sign of over-broad
descriptions.

---

## 3. The exact prompts

**System prompt** (identical for every selection call):

```
You are choosing whether and which tool to call for the user's request. If a
tool fits, call exactly one. If none fits, do not call any tool.
```

That is the whole instruction. The tool definitions are not pasted into the
prompt text - they ride in the API's `tools` field, and `tool_choice` enforces
"exactly one" vs. "may decline."

**Worked example** - the confusability query for a tool described as:

> *Get detailed metadata for a single table. Only use when you need to inspect a
> specific table that the user asked about - do NOT call this for every table.
> discover_schema() and the ontology already contain full schema structure…*

becomes the literal user message:

```
I need to: Get detailed metadata for a single table. Only use when you need to
inspect a specific table that the user asked about - do NOT call this for every
table. discover_schema() and the ontology already contain full schema
structure… Which tool should I use?
```

Expected pick: that same tool. Any other pick → confusable.

---

## 4. From samples to a number

Each query is asked **N times** (`--samples`, default **3**); the pick list
becomes a rate:

```python
picks     = [ask_tool_choice(...) for _ in range(N)]
hits      = [p for p in picks if p == expected]
pass_rate = len(hits) / len(picks)
```

- **Confusability / labeled:** `pass_rate < 1.0` (any sample picked wrong) emits
  a `selection_error` finding, with a `confused_with` histogram of where it
  strayed.
- **Distraction:** `fire_rate` = fraction of samples where any tool fired; any
  firing emits a `distraction` finding.

### How that rolls into the grade (`grade.py`)

**Selection Robustness** (25% of the grade):

```
# labeled queries present:
base = avg(pass_rate over labeled findings) * 100
# else (proxy-only):
base = 100 - clamp(#confusable_tools / #tools) * 60

distraction_penalty = clamp(sum(fire_rate) / 2) * 40
SelectionRobustness = max(0, base - distraction_penalty)
```

The same confusability findings also feed **Description Quality** (15%) -
confusable descriptions are a description problem too.

---

## 5. "Isn't which tool gets picked basically random?"

No - and where it *looks* random, that is the finding, not a flaw in the method.

The model's pick is a **probability distribution** over the tools, conditioned
on the prompt and every description. It is **not** uniform:

- A **distinct, well-named** tool → a sharply peaked distribution → picked
  ~100% of the time → no flicker. Effectively deterministic.
- **Overlapping** tools → the probability mass splits between them. *That split
  is the signal.* A tool whose selection flickers run-to-run is telling you its
  description doesn't separate it from a neighbor.

Run-to-run variance has two sources:

1. These calls **don't set `temperature`** → the model *samples* from the
   distribution rather than taking the argmax.
2. LLM APIs aren't bit-deterministic **even at `temperature=0`** (MoE routing,
   batching, float non-associativity).

That is precisely why the probe takes **N samples and reports a rate**, not one
boolean: a single call is one draw from a distribution and tells you little; N
draws estimate the distribution.

---

## 6. Choosing N (`--samples`)

More samples don't make the *model* less random - they make *your estimate* of
its behavior more precise and reproducible. Sampling error shrinks as ~1/√N.

A subtlety worth knowing: a confusability finding fires on **any** off-pick
(`pass_rate < 1.0`), so a tool with true off-pick probability `p` is flagged
with probability `1 − (1−p)^N`, which **grows with N**:

| N | calls/run* | buckets | chance a 10%-flaky tool is flagged |
|---|---|---|---|
| 3  | ~84  | 4  | 27% |
| 5  | ~140 | 6  | 41% |
| 7  | ~196 | 8  | 52% |
| 9  | ~252 | 10 | 61% |
| 15 | ~420 | 16 | 79% |

\* ≈ (number of tools + 4 distraction tasks) × N LLM calls, each carrying the
full tool surface. Cost scales **linearly** with N.

So raising N doesn't push the score *up* - it surfaces borderline-overlapping
tools more reliably and makes the result **reproducible**. The win is truth and
stability, not a kinder grade.

**Guidance:** keep the default `3` for cheap scans; use `7` for a committed
profile capture you want to be steady. Past ~9–11 you hit diminishing returns.

```bash
mcp-xray analyze --stdio "my-server" --llm --model claude-opus-4-8 --samples 7
```

The `rerun.sh` profile scripts expose the same knob as a `SAMPLES` env var (see
`.env.template`).

### Resume (`--resume`) - never re-pay for completed samples

The behavioral probe is the only expensive part of an audit, and a high-N run
can die partway (rate limit, credit-out, network). `--resume` caches each
completed sample to `<runs-dir>/.probe-cache/<fingerprint>-<model>.jsonl`,
keyed by surface fingerprint + model + query + sample-index. A re-run with
`--resume` reuses what's cached and only calls the model for the missing
samples - so a run that failed at 80% costs ~20% to finish.

```bash
mcp-xray analyze --stdio "my-server" --llm --model claude-opus-4-8 --samples 15 --resume
# ...dies at sample 250/300 (credits) -> top up, re-run the SAME command -> ~50 calls, not 300
```

- **Errored samples are never cached**, so they retry on resume (a credit-out
  isn't a model decision).
- **Natural invalidation:** the fingerprint keys the cache, so changing any tool
  description misses the cache and re-probes the changed surface; the model must
  match too (selection is model-specific).
- Samples are independent draws, so reusing ones gathered at different times is
  statistically sound. Delete the cache file to force a fully fresh run.

---

## 7. Related: the vague-description confirmer

A different, non-tool-call LLM use lives in the **static** probe. The
deterministic word-list *nominates* vague wording (`handle`, `do`, `stuff`, …);
when a client is configured, the model *adjudicates* whether the flagged words
are genuine filler or precise prose - e.g. `do` inside a `do NOT call this`
guardrail is fine. It asks a plain yes/no (`ChatClient.ask_yes_no`) and can only
ever **remove** a finding, never add one, so the deterministic core and the
authoritative token costs stay LLM-free. Findings the model keeps are tagged
`llm_confirmed` in the report.

---

## 8. Reproducibility

Selection findings are non-deterministic by nature; the **token costs and schema
smells are not**. A run is keyed by a `fingerprint` (a hash of the tool
inventory), so re-audits of an unchanged surface line up and reveal drift. When
comparing two behavioral runs, expect the selection score to wobble within
sampling error - raise `--samples` to narrow it.

The static + consolidation half is fully deterministic and runs keyless/offline;
only this behavioral probe introduces sampling variance.
