from mcp_xray.counting import OfflineCounter
from mcp_xray.probes.base import RunContext
from mcp_xray.probes.static_hygiene import StaticHygieneProbe


def _ctx(inv):
    return RunContext(inventory=inv, counter=OfflineCounter(), available={"inventory"})


def test_surface_and_per_tool_costs(crud_inventory):
    findings = StaticHygieneProbe().run(_ctx(crud_inventory))
    surface = [f for f in findings if f.kind == "token_cost" and f.measurement["scope"] == "surface"]
    per_tool = [f for f in findings if f.kind == "token_cost" and f.measurement["scope"] == "tool"]
    assert len(surface) == 1
    assert len(per_tool) == len(crud_inventory.tools)
    # Leave-one-out shares should roughly sum to the surface (offline is additive).
    assert surface[0].measurement["tokens"] > 0
    assert all(f.measurement["tokens"] > 0 for f in per_tool)


def test_offline_flagged_not_authoritative(crud_inventory):
    findings = StaticHygieneProbe().run(_ctx(crud_inventory))
    surface = next(f for f in findings if f.measurement.get("scope") == "surface")
    assert surface.measurement["authoritative"] is False
    assert surface.measurement["backend"] == "offline"


def test_hidden_injector_detected(crud_inventory):
    findings = StaticHygieneProbe().run(_ctx(crud_inventory))
    injectors = [f for f in findings if f.kind == "hidden_injector"]
    assert any(f.measurement["kind"] == "instructions" for f in injectors)


def test_schema_smells_fire_on_bloated(bloated_inventory):
    findings = StaticHygieneProbe().run(_ctx(bloated_inventory))
    smells = {f.measurement["smell"] for f in findings if f.kind == "schema_smell"}
    assert "missing_description" in smells
    assert "deep_nesting" in smells
    assert "enum_bloat" in smells
    assert "wide_schema" in smells
    assert "vague_description" in smells or "tiny_description" in smells


def test_clean_surface_few_smells(clean_inventory):
    findings = StaticHygieneProbe().run(_ctx(clean_inventory))
    smells = [f for f in findings if f.kind == "schema_smell"]
    assert smells == []


def _vague_terms(inv):
    findings = StaticHygieneProbe().run(_ctx(inv))
    return {
        tuple(f.measurement.get("terms", []))
        for f in findings
        if f.measurement.get("smell") == "vague_description"
    }


def test_prompts_and_resources_are_token_costed():
    # Prompts/resources are hidden injectors too - their listing footprint must
    # carry a tokens_est so it factors into the Context Efficiency penalty, not
    # just a bare count.
    from mcp_xray.inventory import Inventory

    inv = Inventory.from_tool_dicts(
        [{"name": "a", "description": "x", "inputSchema": {}}],
        prompts=[{"name": "write_model", "description": "OBML syntax reference for writing a model."}],
        resources=[{"uri": "obml://reference", "name": "obml_reference"}],
    )
    findings = StaticHygieneProbe().run(_ctx(inv))
    inj = {f.measurement["kind"]: f.measurement for f in findings if f.kind == "hidden_injector"}
    assert inj["prompts"]["tokens_est"] > 0
    assert inj["resources"]["tokens_est"] > 0


def test_prompts_resources_do_not_grade_but_instructions_do():
    # Lazy injectors (prompts/resources) are reported but must NOT shave Context
    # Efficiency; instructions (per-turn) must.
    from mcp_xray.grade import Grader
    from mcp_xray.inventory import Inventory

    tools = [{"name": "a", "description": "x", "inputSchema": {}}]
    lazy = Inventory.from_tool_dicts(
        tools,
        prompts=[{"name": "p", "description": "big prompt " * 50}],
        resources=[{"uri": "r://x", "name": "res"}],
    )
    instr = Inventory.from_tool_dicts(tools, instructions="big instructions blob " * 50)

    def ce(inv):
        f = StaticHygieneProbe().run(_ctx(inv))
        return Grader().grade(f, ran=["static_hygiene"]).subscores["context_efficiency"].score

    # Same tools; lazy injectors leave CE untouched, instructions drag it down.
    base = ce(Inventory.from_tool_dicts(tools))
    assert ce(lazy) == base
    assert ce(instr) < base


def test_negated_do_is_not_vague():
    from mcp_xray.inventory import Inventory

    inv = Inventory.from_tool_dicts(
        [
            {
                "name": "get_table_details",
                "description": (
                    "Get detailed metadata for a single table. Only use when you "
                    "need to inspect a specific table the user asked about - do NOT "
                    "call this for every table; discover_schema already has it."
                ),
                "inputSchema": {"type": "object", "properties": {"table": {"type": "string"}}},
            }
        ]
    )
    assert _vague_terms(inv) == set()  # "do" inside "do NOT" must not flag


def test_filler_do_still_flags():
    from mcp_xray.inventory import Inventory

    inv = Inventory.from_tool_dicts(
        [
            {
                "name": "table_tool",
                "description": "Use this to do stuff with the table when needed by callers.",
                "inputSchema": {"type": "object", "properties": {"table": {"type": "string"}}},
            }
        ]
    )
    assert _vague_terms(inv) == {("do", "stuff")}  # genuine filler still caught


class _FakeChat:
    """Stub ChatClient.ask_yes_no -> fixed verdict; records the prompts."""

    def __init__(self, verdict):
        self._verdict = verdict
        self.calls = []

    def ask_yes_no(self, system, query):
        self.calls.append((system, query))
        return self._verdict


def _ctx_llm(inv, client):
    return RunContext(
        inventory=inv,
        counter=OfflineCounter(),
        available={"inventory", "api_key", "llm"},
        client=client,
    )


def _vague_inv():
    from mcp_xray.inventory import Inventory

    return Inventory.from_tool_dicts(
        [
            {
                "name": "thing_tool",
                "description": "Use this to manage various things for the caller as needed.",
                "inputSchema": {"type": "object", "properties": {"x": {"type": "string"}}},
            }
        ]
    )


def test_llm_confirmer_drops_false_positive():
    inv = _vague_inv()
    client = _FakeChat(verdict=False)  # model says: precise, not vague
    findings = StaticHygieneProbe().run(_ctx_llm(inv, client))
    vague = [f for f in findings if f.measurement.get("smell") == "vague_description"]
    assert vague == []
    assert client.calls  # the model was actually consulted


def test_llm_confirmer_keeps_and_marks_confirmed():
    inv = _vague_inv()
    client = _FakeChat(verdict=True)  # model agrees: vague
    findings = StaticHygieneProbe().run(_ctx_llm(inv, client))
    vague = [f for f in findings if f.measurement.get("smell") == "vague_description"]
    assert len(vague) == 1
    assert vague[0].measurement["llm_confirmed"] is True


def test_llm_error_keeps_deterministic_nomination():
    inv = _vague_inv()
    client = _FakeChat(verdict=None)  # call failed / undecided
    findings = StaticHygieneProbe().run(_ctx_llm(inv, client))
    vague = [f for f in findings if f.measurement.get("smell") == "vague_description"]
    assert len(vague) == 1
    assert "llm_confirmed" not in vague[0].measurement  # offline behavior preserved
