from mcp_xray.inventory import Tool


def test_name_decomposition_snake():
    t = Tool("create_label", "Create a label", {"type": "object", "properties": {"name": {"type": "string"}}})
    assert t.verb == "create"
    assert t.resource == "label"
    assert t.behavior == "write"


def test_name_decomposition_camel():
    t = Tool("searchThreads", "Search threads", {"type": "object", "properties": {}})
    assert t.verb == "search"
    assert t.resource == "threads"
    assert t.behavior == "read"


def test_destructive_classification():
    t = Tool("delete_thread", "Delete a thread", {"type": "object", "properties": {"id": {"type": "string"}}})
    assert t.behavior == "destructive"


def test_pure_read():
    t = Tool("get_user", "Get user", {"type": "object", "properties": {"id": {"type": "string"}}})
    assert t.is_pure_read


def test_schema_features(bloated_inventory):
    run = bloated_inventory.by_name("run")
    assert run.features.property_count >= 15
    assert run.features.max_depth >= 4
    assert max(run.features.enum_sizes) >= 12


def test_fingerprint_stable(crud_inventory):
    fp1 = crud_inventory.fingerprint()
    fp2 = crud_inventory.fingerprint()
    assert fp1 == fp2 and len(fp1) == 16
