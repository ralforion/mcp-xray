"""Contract test: a frozen mcp-checkup sample must parse to the expected
Finding shape. A silent upstream format change fails here, not at a client."""

from pathlib import Path

from mcp_xray.probes.token_tax.mcp_checkup import McpCheckupAdapter

SAMPLE = Path(__file__).parent / "fixtures" / "mcp_checkup_sample.json"


def test_parse_shape():
    findings = McpCheckupAdapter().parse(SAMPLE.read_text())
    token_costs = [f for f in findings if f.kind == "token_cost"]
    dups = [f for f in findings if f.kind == "duplicate"]

    assert {f.target for f in token_costs} == {"create_label", "search_threads"}
    assert all(f.measurement["authoritative"] is False for f in token_costs)
    assert all(f.measurement["backend"] == "mcp-checkup" for f in token_costs)
    # grades / advice prose must be discarded -- measurements only.
    assert all("grade" not in f.measurement and "advice" not in f.measurement for f in token_costs)

    assert len(dups) == 1
    assert dups[0].target == ["get_user", "get_account"]
