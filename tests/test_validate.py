import json

from mcp_xray import connect
from mcp_xray.validate import validate


def _write(tmp_path, name, tools, instructions=None):
    p = tmp_path / name
    payload = {"tools": tools}
    if instructions:
        payload["instructions"] = instructions
    p.write_text(json.dumps(payload))
    return p


def test_validate_token_savings_offline(tmp_path):
    before = _write(
        tmp_path,
        "before.json",
        [
            {"name": "create_label", "description": "Create a label here", "inputSchema": {"type": "object", "properties": {"name": {"type": "string"}}}},
            {"name": "delete_label", "description": "Delete a label here", "inputSchema": {"type": "object", "properties": {"id": {"type": "string"}}}},
        ],
    )
    after = _write(
        tmp_path,
        "after.json",
        [
            {"name": "manage_label", "description": "Create or delete a label", "inputSchema": {"type": "object", "properties": {"action": {"type": "string", "enum": ["create", "delete"]}, "name": {"type": "string"}, "id": {"type": "string"}}}},
        ],
    )
    delta = validate(connect.from_tools_json(before), connect.from_tools_json(after), token_backend="offline")
    assert delta.tokens_saved > 0
    # No queries / no client -> accuracy unknown but tokens improved.
    assert delta.verdict in {"accept_on_tokens", "inconclusive"}
    assert delta.accuracy_delta is None
