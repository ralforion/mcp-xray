from mcp_xray.counting import OfflineCounter
from mcp_xray.inventory import Tool
from mcp_xray.probes.base import RunContext
from mcp_xray.probes.consolidate import ConsolidateProbe, jaccard, merge_score


def _ctx(inv, tool_phases=None):
    return RunContext(
        inventory=inv, counter=OfflineCounter(), available={"inventory"}, tool_phases=tool_phases
    )


def test_merge_score_same_resource():
    a = Tool("create_label", "Create a label", {"type": "object", "properties": {"name": {"type": "string"}}})
    b = Tool("delete_label", "Delete a label", {"type": "object", "properties": {"id": {"type": "string"}}})
    score, parts = merge_score(a, b)
    assert parts["same_resource"]
    assert score >= 0.4


def test_jaccard():
    assert jaccard(frozenset({"id"}), frozenset({"id"})) == 1.0
    assert jaccard(frozenset({"a"}), frozenset({"b"})) == 0.0


def test_merge_respects_phase_membership():
    # load/remove_model live in both phases; describe_model is run-only. A
    # phase-aware run must NOT merge describe_model in with load/remove.
    tools = [
        Tool("load_model", "Load a model", {"type": "object", "properties": {"model": {"type": "object"}}}),
        Tool("remove_model", "Remove a model", {"type": "object", "properties": {"model_id": {"type": "string"}}}),
        Tool("describe_model", "Describe a model", {"type": "object", "properties": {"model_id": {"type": "string"}}}),
    ]
    from mcp_xray.inventory import Inventory

    inv = Inventory(tools=tools)
    tool_phases = {
        "load_model": {"design", "run"},
        "remove_model": {"design", "run"},
        "describe_model": {"run"},
    }
    merges = [
        f for f in ConsolidateProbe().run(_ctx(inv, tool_phases))
        if f.kind == "merge_candidate" and f.measurement["resource"] == "model"
    ]
    assert len(merges) == 1
    m = merges[0]
    assert set(m.target) == {"load_model", "remove_model"}  # describe_model excluded
    assert "describe_model" not in m.target
    assert m.measurement["phases"] == ["design", "run"]


def test_merge_unphased_still_groups_whole_family():
    # Without a phase map (snapshot run) behavior is unchanged: full family merges.
    from mcp_xray.inventory import Inventory

    tools = [
        Tool("load_model", "Load a model", {"type": "object", "properties": {"model": {"type": "object"}}}),
        Tool("remove_model", "Remove a model", {"type": "object", "properties": {"model_id": {"type": "string"}}}),
        Tool("describe_model", "Describe a model", {"type": "object", "properties": {"model_id": {"type": "string"}}}),
    ]
    merges = [
        f for f in ConsolidateProbe().run(_ctx(Inventory(tools=tools)))
        if f.kind == "merge_candidate" and f.measurement["resource"] == "model"
    ]
    assert len(merges) == 1
    assert set(merges[0].target) == {"load_model", "remove_model", "describe_model"}
    assert "phases" not in merges[0].measurement


def test_crud_families_found(crud_inventory):
    findings = ConsolidateProbe().run(_ctx(crud_inventory))
    merges = [f for f in findings if f.kind == "merge_candidate" and f.measurement["lens"] == "capability"]
    resources = {f.measurement["resource"] for f in merges}
    assert "label" in resources
    assert "thread" in resources
    # label family should propose manage_label
    label = next(f for f in merges if f.measurement["resource"] == "label")
    assert label.detail["proposal"].startswith("manage_label")
    assert label.measurement["tokens_saved_est"] > 0


def test_resource_candidates_for_reads(crud_inventory):
    findings = ConsolidateProbe().run(_ctx(crud_inventory))
    rc = {f.target for f in findings if f.kind == "resource_candidate"}
    # A parameterless read maps cleanly to a static resource.
    assert "list_labels" in rc
    # id-keyed reads are over a dynamic keyspace -> stay tools, not listed.
    assert "get_thread" not in rc
    assert "get_label" not in rc


def test_resource_candidate_clean_map_only_when_parameterless(crud_inventory):
    findings = ConsolidateProbe().run(_ctx(crud_inventory))
    clean = {f.target for f in findings if f.kind == "resource_candidate" and f.measurement["clean_map"]}
    assert clean == {"list_labels"}  # only the zero-parameter read is a clean map


def test_overlapping_shape_merges(overlapping_inventory):
    findings = ConsolidateProbe().run(_ctx(overlapping_inventory))
    shape = [f for f in findings if f.kind == "merge_candidate" and f.measurement["lens"] == "shape"]
    # get_user / get_account / fetch_customer / lookup_member share {id} signature
    assert len(shape) >= 1
    assert any(f.measurement["sig_aff"] == 1.0 for f in shape)


def test_clean_has_no_merges(clean_inventory):
    findings = ConsolidateProbe().run(_ctx(clean_inventory))
    merges = [f for f in findings if f.kind == "merge_candidate"]
    assert merges == []


def test_jit_framing(crud_inventory, clean_inventory):
    big = next(f for f in ConsolidateProbe().run(_ctx(crud_inventory)) if f.kind == "jit_candidate")
    small = next(f for f in ConsolidateProbe().run(_ctx(clean_inventory)) if f.kind == "jit_candidate")
    # crud_heavy has 12 tools (<15) so not flagged; clean has 3 -- neither recommends JIT.
    assert big.measurement["recommend_jit"] is False
    assert small.measurement["recommend_jit"] is False


def test_merge_flags_read_write_mix():
    # get_x (read) + save_x (write) is a read+write blend -> flagged and
    # down-confidenced so the probe doesn't push a risky merge.
    from mcp_xray.inventory import Inventory

    inv = Inventory.from_tool_dicts([
        {"name": "get_model", "description": "Get the model", "inputSchema": {}},
        {"name": "save_model", "description": "Save the model",
         "inputSchema": {"type": "object", "properties": {"body": {"type": "string"}}}},
    ])
    merges = [f for f in ConsolidateProbe().run(_ctx(inv)) if f.kind == "merge_candidate"]
    assert merges and merges[0].measurement["mixes_read_write"] is True
    assert merges[0].confidence < 0.8  # lowered vs a clean same-resource merge


def test_advisory_verb_counts_as_non_mutating():
    # suggest_x (advisory) + apply_x (write) also blends non-mutating + mutating.
    from mcp_xray.inventory import Inventory

    inv = Inventory.from_tool_dicts([
        {"name": "suggest_names", "description": "Suggest names",
         "inputSchema": {"type": "object", "properties": {"x": {"type": "string"}}}},
        {"name": "apply_names", "description": "Apply names",
         "inputSchema": {"type": "object", "properties": {"x": {"type": "string"}}}},
    ])
    merges = [f for f in ConsolidateProbe().run(_ctx(inv)) if f.kind == "merge_candidate"]
    assert merges and merges[0].measurement["mixes_read_write"] is True


def test_pure_write_family_not_flagged_read_write():
    # create_x + update_x are both mutating -> NOT a read+write mix.
    from mcp_xray.inventory import Inventory

    inv = Inventory.from_tool_dicts([
        {"name": "create_label", "description": "Create a label",
         "inputSchema": {"type": "object", "properties": {"name": {"type": "string"}}}},
        {"name": "update_label", "description": "Update a label",
         "inputSchema": {"type": "object", "properties": {"name": {"type": "string"}}}},
    ])
    merges = [f for f in ConsolidateProbe().run(_ctx(inv)) if f.kind == "merge_candidate"]
    assert merges and merges[0].measurement["mixes_read_write"] is False
    assert merges[0].confidence == 0.8
