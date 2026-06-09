"""Contract test: frozen token-analyzer sample -> expected Finding shape."""

from pathlib import Path

from mcp_xray.probes.token_tax.token_analyzer import TokenAnalyzerAdapter

SAMPLE = Path(__file__).parent / "fixtures" / "token_analyzer_sample.json"


def test_parse_shape():
    findings = TokenAnalyzerAdapter().parse(SAMPLE.read_text())
    assert {f.target for f in findings} == {"create_label", "search_threads"}
    for f in findings:
        assert f.kind == "token_cost"
        assert f.measurement["authoritative"] is False
        assert f.measurement["backend"] == "token-analyzer"
        # recommendation/summary prose discarded; only numbers survive.
        assert "note" not in f.measurement
        assert "schema_tokens" in f.detail
