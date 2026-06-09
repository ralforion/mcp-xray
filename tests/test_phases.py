from pathlib import Path

from mcp_xray import orchestrator
from mcp_xray.grade import Grader
from mcp_xray.phases import build_phased, load_phases_manifest
from mcp_xray.report import build_report, render_markdown

MANIFEST = Path(__file__).resolve().parent / "fixtures" / "phases" / "phases.yaml"


def _phased():
    return build_phased(load_phases_manifest(MANIFEST))


def test_manifest_loads_two_phases():
    invs = load_phases_manifest(MANIFEST)
    assert set(invs) == {"setup", "active"}
    assert len(invs["setup"].tools) == 7
    assert len(invs["active"].tools) == 16


def test_union_and_overlap():
    p = _phased()
    # union = 3 always + 4 setup-only + 13 active-only = 20 distinct
    assert len(p.union.tools) == 20
    assert p.union.dynamic is True
    # carried = the always-tools present in both phases
    assert p.carried == ["close_session", "open_session", "run_batch"]
    excl = p.exclusive()
    assert "get_reference" in excl["setup"]
    assert "run_query" in excl["active"]
    assert "open_session" not in excl["setup"] and "open_session" not in excl["active"]


def test_run_phased_emits_phase_tax_and_headline():
    p = _phased()
    result = orchestrator.run_phased(p, token_backend="offline")
    phase_costs = {
        f.measurement["phase"]: f.measurement["tokens"]
        for f in result.findings
        if f.kind == "token_cost" and f.measurement.get("scope") == "phase"
    }
    assert set(phase_costs) == {"setup", "active"}
    # active phase is heavier than setup
    assert phase_costs["active"] > phase_costs["setup"]

    surface = next(f for f in result.findings if f.kind == "token_cost" and f.measurement.get("scope") == "surface")
    # headline tax = worst (active) phase, NOT the union
    assert surface.measurement["phased"] is True
    assert surface.measurement["worst_phase"] == "active"
    assert surface.measurement["tokens"] == phase_costs["active"]


def test_per_tool_findings_tagged_with_phases():
    result = orchestrator.run_phased(_phased(), token_backend="offline")
    tool_costs = {
        f.target: f.measurement.get("phases")
        for f in result.findings
        if f.kind == "token_cost" and f.measurement.get("scope") == "tool"
    }
    assert sorted(tool_costs["open_session"]) == ["active", "setup"]
    assert tool_costs["run_query"] == ["active"]
    assert tool_costs["get_reference"] == ["setup"]


def test_jit_credits_progressive_loading():
    result = orchestrator.run_phased(_phased(), token_backend="offline")
    jit = [f for f in result.findings if f.kind == "jit_candidate"]
    assert len(jit) == 1
    m = jit[0].measurement
    assert m["progressive"] is True
    assert m["looks_dynamic"] is True
    assert m["recommend_jit"] is False
    assert m["phase_count"] == 2


def test_report_renders_phase_section():
    result = orchestrator.run_phased(_phased(), token_backend="offline")
    grade = Grader().grade(result.findings, ran=result.ran)
    report = build_report(result, grade)
    assert report["headline"]["phased"] is True
    assert report["headline"]["union_tool_count"] == 20
    md = render_markdown(report)
    assert "## Phase surfaces" in md
    assert "Carried across phases" in md
    assert "already in place" in md  # progressive-loading credit
    assert "worst phase" in md


def test_context_efficiency_scored_and_labeled_by_worst_phase():
    result = orchestrator.run_phased(_phased(), token_backend="offline")
    grade = Grader().grade(result.findings, ran=result.ran)
    ce = grade.subscores["context_efficiency"]
    # scored on the worst (active) phase tax, and the row names that phase
    active_tax = next(
        f.measurement["tokens"] for f in result.findings
        if f.kind == "token_cost" and f.measurement.get("scope") == "phase"
        and f.measurement.get("phase") == "active"
    )
    assert f"{active_tax} surface tokens" in ce.rationale
    assert "worst phase `active`" in ce.rationale


def test_union_runs_hygiene_and_consolidation():
    # all distinct tools get reviewed via the union, even phase-exclusive ones
    result = orchestrator.run_phased(_phased(), token_backend="offline")
    assert "static_hygiene" in result.ran and "consolidate" in result.ran
    # a setup-only tool is present in the union inventory
    assert "import_config" in result.inventory.names


def test_phased_union_carries_injectors():
    # Regression: server instructions are server-level; they must survive onto
    # the union so the hidden-injector probe measures them on phased servers too
    # (previously the capture path dropped them -> false "no injectors").
    from mcp_xray.inventory import Inventory

    tool = {"name": "a", "description": "x", "inputSchema": {}}
    design = Inventory.from_tool_dicts(
        [tool], instructions="Use this server to do X. " * 20, prompts=[{"name": "p"}]
    )
    run = Inventory.from_tool_dicts([tool, {"name": "b", "description": "y", "inputSchema": {}}])
    p = build_phased({"design": design, "run": run})
    assert p.union.instructions == design.instructions
    assert p.union.prompts == [{"name": "p"}]

    # ...and the static probe actually emits a hidden_injector for it.
    result = orchestrator.run_phased(p, token_backend="offline")
    inj = [f for f in result.findings if f.kind == "hidden_injector"]
    assert any(f.measurement.get("kind") == "instructions" for f in inj)
