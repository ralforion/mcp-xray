# Security Policy

## Reporting a vulnerability

Please **do not** open a public issue for security problems.

Report vulnerabilities privately via GitHub's
[Report a vulnerability](https://github.com/ralfbecher/mcp-xray/security/advisories/new)
(Security → Advisories). Include:

- a description of the issue and its impact,
- steps to reproduce (a minimal `tools/list` dump or command line is ideal),
- the mcp-xray version (`mcp-xray --version`) and your Python version.

We aim to acknowledge a report within a few business days and to keep you updated
as we work on a fix.

## Scope notes

mcp-xray is an **analysis** tool. Keep these in mind:

- **API keys** - token counting and behavioral probes read `ANTHROPIC_API_KEY` /
  `OPENAI_API_KEY` from the environment (or a git-ignored `.env`). Keys are never
  written to run folders or reports. Never commit a real key; `.env` is ignored.
- **Calling tools** - mcp-xray never invokes a server's tools unless you pass an
  explicit `--call-manifest` asserting those calls are read-only/sandbox-safe.
  Treat the manifest as a security boundary you own.
- **Untrusted dumps/servers** - a `tools/list` dump or live server you audit may
  contain adversarial tool descriptions (prompt injection, tool poisoning).
  mcp-xray flags hidden-injector patterns as a hygiene signal, but it is **not** a
  security scanner - it does not sandbox or neutralize malicious content. Don't
  feed untrusted output into downstream agents on its say-so.
