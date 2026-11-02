import json

from mcp_xray import orchestrator
from mcp_xray.grade import Grader
from mcp_xray.report import build_report, render_markdown, write_run


def test_offline_run_grades_and_reports(crud_inventory):
    result = orchestrator.run(crud_inventory, token_backend="offline")
    # static + consolidate run offline; noise + adapters skip.
    assert "static_hygiene" in result.ran
    assert "consolidate" in result.ran
    assert any(s["probe"] == "noise" for s in result.skipped)

    grade = Grader().grade(result.findings, ran=result.ran)
    assert 0 <= grade.overall <= 100
    # Selection robustness not measured offline -> dropped weight.
    assert grade.subscores["selection_robustness"].measured is False
    assert grade.subscores["context_efficiency"].measured is True


def test_report_round_trip(tmp_path, crud_inventory):
    result = orchestrator.run(crud_inventory, token_backend="offline")
    grade = Grader().grade(result.findings, ran=result.ran)
    paths = write_run(tmp_path / "run1", result, grade)
    assert paths["json"].exists() and paths["md"].exists()

    stored = json.loads(paths["json"].read_text())
    assert stored["grade"]["letter"]
    assert stored["server"]["fingerprint"] == crud_inventory.fingerprint()

    md = render_markdown(stored)
    assert "MCP Surface Review" in md
    assert "Methodology & coverage" in md
    assert "not measured" in md  # selection robustness


def test_schema_issues_section_names_tools_and_fixes(bloated_inventory):
    # The hygiene/quality scores are driven by schema_smell findings; the report
    # must name the offending tool and give a fix, not just a count.
    result = orchestrator.run(bloated_inventory, token_backend="offline")
    grade = Grader().grade(result.findings, ran=result.ran)
    md = render_markdown(build_report(result, grade))
    smells = [f for f in result.findings if f.kind == "schema_smell"]
    assert smells  # fixture is bloated on purpose
    assert "## Schema & description issues" in md
    # every flagged tool is named in the section, with a Fix column
    assert "| Fix |" in md
    for f in smells:
        assert f"`{f.target}`" in md


def test_clean_scores_higher_than_bloated(clean_inventory, bloated_inventory):
    rc = orchestrator.run(clean_inventory, token_backend="offline")
    rb = orchestrator.run(bloated_inventory, token_backend="offline")
    gc = Grader().grade(rc.findings, ran=rc.ran)
    gb = Grader().grade(rb.findings, ran=rb.ran)
    assert gc.subscores["schema_hygiene"].score > gb.subscores["schema_hygiene"].score


def test_headline_cost_present(crud_inventory):
    result = orchestrator.run(crud_inventory, token_backend="offline")
    grade = Grader().grade(result.findings, ran=result.ran)
    report = build_report(result, grade)
    assert report["headline"]["surface_tokens_per_turn"] > 0
    assert report["headline"]["authoritative"] is False


def _cov(mode, units, measured, samples, errors):
    from mcp_xray.finding import Finding

    return Finding(
        probe="noise", kind="probe_coverage", target=[],
        measurement={"mode": mode, "units": units, "units_measured": measured, "samples": samples, "errors": errors},
    )


def test_noise_run_stored_without_coverage_grades_as_before():
    # Runs stored before the probe recorded coverage keep their grade.
    sub = Grader().grade([], ran=["noise"]).subscores["selection_robustness"]
    assert sub.measured is True and sub.score == 100.0


def test_proxy_selection_scored_over_measured_tools_only():
    from mcp_xray.finding import Finding

    proxy_miss = Finding(
        probe="noise", kind="selection_error", target="a",
        measurement={"mode": "confusability_proxy", "pass_rate": 0.0, "samples": 2, "errors": 0},
    )
    findings = [
        proxy_miss,
        _cov("confusability_proxy", units=10, measured=5, samples=10, errors=10),
        _cov("distraction", units=4, measured=0, samples=0, errors=8),
    ]
    sub = Grader().grade(findings, ran=["noise"]).subscores["selection_robustness"]
    # 1 confusable of the 5 tools measured, not of all 10 (or of 1, the fallback)
    assert sub.measured is True and sub.score == 88.0
    assert "5 of 10 tools measured" in sub.rationale
    assert "distraction not measured" in sub.rationale
    assert "18 sample(s) failed" in sub.rationale


def test_report_states_behavioral_coverage():
    from mcp_xray.report import _behavioral_coverage

    line = _behavioral_coverage([
        _cov("confusability_proxy", units=10, measured=9, samples=27, errors=3),
        _cov("distraction", units=4, measured=4, samples=12, errors=0),
    ])
    assert line == (
        "confusability_proxy 9 of 10 tools, distraction 4 of 4 tasks measured; "
        "3 of 42 samples failed (API error or cut off) and were not scored"
    )
    assert _behavioral_coverage([]) is None
