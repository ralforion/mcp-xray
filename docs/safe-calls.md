# Safe tool calls - manifests and result-size probing

mcp-xray is a **read-only instrument by default**. This doc explains the one
safety rule that governs every tool invocation, the two manifests that authorize
calls, and what result-size probing measures.

---

## The rule: no tool is ever called without a manifest

By default mcp-xray **never executes a tool**. It reads the tool *list*
(`tools/list`) and asks a model to *pick* tools (selection / confusability /
distraction) - but it does not *call* them. Calling a real tool can write data,
delete things, cost money, or trip rate limits, so the design forbids it unless
**you** explicitly authorize specific calls.

That authorization is a **manifest**: a YAML file where the operator lists exact
tool calls. mcp-xray runs **only** what's listed, **once** each, and never infers
extra calls. A name-based "this looks read-only" heuristic is *advisory only* -
it is never treated as permission.

> Think of a manifest as a signed permission slip: by listing a call you assert
> *"this one is read-only / sandbox-safe - you may run it."*

There are **two** manifests, each authorizing calls for a different purpose.

---

## 1. Call manifest (`--call-manifest`) → result-size probing

Measures how big a tool's **output** is. Tool results are fed back into the
model's context on every call, so a tool that returns a 50 KB blob is a recurring
context cost the static surface scan can't see.

```yaml
# safe.yaml - operator asserts these are read-only / sandbox-safe
calls:
  - tool: list_labels
    args: {}
  - tool: get_table_details
    args: { table_name: "orders" }
```

```bash
mcp-xray analyze --stdio "my-server" --call-manifest safe.yaml
```

- **Live, non-phased runs only** (`--stdio` / `--http` / `--sse`) - offline or
  phased runs warn and skip, because there's no live server to call.
- Each listed tool is called **once**; mcp-xray measures the result's **chars +
  bytes** and renders a *"Result sizes (per call)"* table in the report.
- A call that errors is recorded as an error row, not a crash - one bad call
  doesn't abort the rest.
- **Informational in v1.0** - result sizes are reported but do **not** change the
  grade (no weighted dimension consumes them yet).

## 2. Capture manifest (`--capture`, via `capture-phases`) → phase advance

A *different* manifest, same principle. A phase-swapped server changes its tool
list as you move through a journey (e.g. a "design" phase before a model is
loaded, a "run" phase after). To snapshot each phase, mcp-xray must *advance* the
server - and advancing means calling a tool (e.g. `load_model`). The capture
manifest authorizes those advance calls:

```yaml
# capture.yaml
phases:
  - name: design            # captured first, before any call
  - name: run
    advance:
      - tool: load_model     # the ONLY call made - never inferred
        args: { model: { ... } }
```

```bash
mcp-xray capture-phases --stdio "my-server" --capture capture.yaml --out-dir dumps/phases
mcp-xray analyze --phases dumps/phases/phases.yaml
```

See the phased-audit section of the [main README](../README.md) for the full
phase workflow.

---

## Call manifest vs. capture manifest

| | **call-manifest** | **capture-manifest** |
|---|---|---|
| Flag | `--call-manifest` (on `analyze`) | `--capture` (on `capture-phases`) |
| Purpose | measure tool **output sizes** | **advance** a phase-swapped server to snapshot each phase |
| Shape | `calls: [{tool, args}]` | `phases: [{name, advance: [{tool, args}]}]` |
| When | live, non-phased run | walking a multi-phase server |
| Calls made | each listed tool, once | each phase's `advance` calls, in order |

Both encode the same guarantee: **a tool call happens only because a human wrote
it down.**

---

## Wrapped sensors and `--client-config`

Separately, the wrapped third-party sensors (`mcp_checkup`, `token_analyzer`)
need a client MCP **config path** to run - pass it with `--client-config <path>`.
They run only when that path is given *and* their binary is installed; otherwise
they're reported "not measured." They contribute measurements only, never grades.
This is config, not a call manifest - no tools are executed.
