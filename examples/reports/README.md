# Example reports

Real `mcp-xray` audits of RALFORION's own production MCP servers - rendered
exactly as the tool emits them (`report.md`). Use them to see what a graded
review actually looks like before running your own.

| Server | Tools | Transport | Grade | Report |
|---|---:|---|---|---|
| OrionBelt Semantic Layer v2.8.4 | 18 | phase-swapped | **B- (82.7)** | [report](orionbelt-semantic-layer-v2.8.4.md) |
| OrionBelt Analytics v1.5.2 | 23 | http | **B (83.8)** | [report](orionbelt-analytics-v1.5.2.md) |

Both audited with mcp-xray v1.4.0 against `claude-opus-4-8` (authoritative token
counting + the behavioral probe). Each report carries its own grade, per-tool
context cost, consolidation proposals (merge / convert-to-resource / JIT), and a
methodology footer naming the model used. The Semantic Layer report also shows
the **phase-swapped** surface analysis (the tool list swaps by journey phase, so
the headline tax is the worst single phase, not the union).

> These are honest B grades on shipping servers, not cherry-picked A+s: the
> value is in the findings, not the letter. Reproduce the offline half yourself
> from any `tools/list` dump with `mcp-xray analyze --tools-json dump.json`.
