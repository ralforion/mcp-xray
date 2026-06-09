# Contributing to mcp-xray

Thanks for your interest in improving mcp-xray. This document covers how to get
set up, the bar for a change, and the legal basics.

## Contributor License Agreement

By submitting a contribution (pull request, patch, or otherwise) you agree to the
[Contributor License Agreement](CLA.md). In short: you grant RALFORION d.o.o. the
rights to use and relicense your contribution, and you confirm the work is yours
to give. No separate signing step is required - opening a PR signals agreement.

## Development setup

```bash
git clone https://github.com/ralfbecher/mcp-xray
cd mcp-xray
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"     # everything + pytest
```

The static + consolidation + grading paths are fully testable **offline** - no
API key and no live server. The behavioral probe and authoritative token counts
need an `ANTHROPIC_API_KEY` (or `OPENAI_API_KEY` for gpt/o-series); copy
`.env.template` to `.env` and fill in what you need.

## Before you open a PR

```bash
pytest            # the whole offline suite must stay green
ruff check src tests
```

- **Keep the offline path keyless.** Anything that needs a key must degrade to a
  flagged ESTIMATE or a "not measured" result, never a hard failure.
- **Measurements vs. interpretation.** Wrapped/added sensors contribute
  *measurements only*; the grading engine owns all interpretation and scoring.
- **Wrapped adapters need a contract test.** Each adapter under
  `src/mcp_xray/probes/` should pin a frozen-fixture test under `tests/contracts/`
  so a silent upstream format change fails in CI, not in front of a user.
- **Match the surrounding style** - naming, comment density, and idiom.

## Reporting bugs / requesting features

Open an issue at https://github.com/ralfbecher/mcp-xray/issues. For anything
security-sensitive, follow [SECURITY.md](SECURITY.md) instead of filing a public
issue.

## Local-only data

`profiles/` and `design/` are git-ignored on purpose - per-server engagement data
and internal planning stay local and must never be committed. Generic, reusable
example data belongs in `tests/fixtures/`.
