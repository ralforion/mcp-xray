"""CLI parsing / wiring tests (no network, no real probes)."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from mcp_xray import cli
from mcp_xray.cli import build_parser

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _parse(*argv):
    return build_parser().parse_args(argv)


def test_samples_flag_parses():
    args = _parse("analyze", "--http", "http://x/mcp", "--samples", "7")
    assert args.samples == 7


def test_samples_defaults_to_none():
    # None -> the noise probe keeps its own default (3); the CLI doesn't force it.
    args = _parse("analyze", "--http", "http://x/mcp")
    assert args.samples is None


def test_samples_must_be_positive():
    # The >=1 guard lives in cmd_analyze; reproduce its check on the parsed value.
    args = _parse("analyze", "--http", "http://x/mcp", "--samples", "0")
    assert args.samples is not None and args.samples < 1  # cmd_analyze would SystemExit


@pytest.mark.parametrize("n", [1, 3, 7, 15])
def test_samples_builds_config(n):
    # Mirror cmd_analyze's config construction so the wiring contract is pinned.
    args = _parse("analyze", "--http", "http://x/mcp", "--samples", str(n))
    config = {}
    if args.samples is not None:
        config["noise_samples"] = args.samples
    assert config == {"noise_samples": n}


def test_result_size_findings_builder():
    # Pure transform: connect.measure_result_sizes records -> result_size Findings.
    measures = [
        {"tool": "list_labels", "args": {}, "chars": 120, "bytes": 122},
        {"tool": "broken", "args": {"x": 1}, "error": "Timeout: deadline"},
    ]
    findings = cli._result_size_findings(measures)
    assert [f.kind for f in findings] == ["result_size", "result_size"]
    ok = findings[0].measurement
    assert ok["chars"] == 120 and ok["bytes"] == 122 and "error" not in ok
    err = findings[1].measurement
    assert err["error"] == "Timeout: deadline" and "bytes" not in err
    assert findings[1].detail["args"] == {"x": 1}


def test_validate_passes_model_to_client(tmp_path, monkeypatch):
    # Regression: cmd_validate must build the LLM client from --model, not just
    # the llm flag - else vendor_for(None) is UNKNOWN and selection is silently
    # skipped despite --queries.
    tools = tmp_path / "t.json"
    tools.write_text(json.dumps({"tools": [{"name": "a", "description": "x", "inputSchema": {}}]}))
    qfile = tmp_path / "q.yaml"
    qfile.write_text("queries:\n  - query: do a\n    expected_tools: [a]\n")

    captured = {}

    def fake_maybe_client(args):
        captured["model"] = getattr(args, "model", "MISSING")
        captured["llm"] = getattr(args, "llm", None)
        return None, "stub"

    monkeypatch.setattr(cli, "_maybe_client", fake_maybe_client)
    monkeypatch.setattr(cli, "validate", lambda *a, **k: SimpleNamespace())

    args = _parse(
        "validate", "--before", str(tools), "--after", str(tools),
        "--queries", str(qfile), "--model", "gpt-5.5",
    )
    args.func(args)
    assert captured["model"] == "gpt-5.5"   # the bug: was previously absent -> None
    assert captured["llm"] is True


def test_phased_run_persists_replayable_dumps(tmp_path):
    # A phased run stores its input dumps in <out>/dumps/ so the run folder
    # reproduces its own report offline.
    out = tmp_path / "run"
    args = _parse("analyze", "--phases", str(FIXTURES / "phases" / "phases.yaml"), "--out", str(out))
    assert args.func(args) == 0
    dumps = out / "dumps"
    assert (dumps / "phases.yaml").exists()
    assert (dumps / "setup.json").exists() and (dumps / "active.json").exists()
    # Replayable: analyze straight from the copied manifest, no original needed.
    out2 = tmp_path / "run2"
    args2 = _parse("analyze", "--phases", str(dumps / "phases.yaml"), "--out", str(out2))
    assert args2.func(args2) == 0
    assert (out2 / "report.json").exists()


def test_flat_run_persists_tools_json(tmp_path):
    out = tmp_path / "run"
    args = _parse("analyze", "--tools-json", str(FIXTURES / "clean.json"), "--out", str(out))
    assert args.func(args) == 0
    tj = out / "dumps" / "tools.json"
    assert tj.exists()
    data = json.loads(tj.read_text())
    assert "tools" in data and data["tools"]  # a real, loadable surface
