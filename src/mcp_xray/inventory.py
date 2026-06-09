"""Normalized tool inventory + schema feature extraction.

A ``Tool`` is the canonical shape every transport collapses to. ``features``
holds the derived signals (verb/resource decomposition, schema shape) that the
consolidation and hygiene probes read -- computed once here so probes never
re-parse raw schemas.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

# Verbs that denote a pure read -- candidates for MCP resources rather than
# tools (PLAN S10.3). Order-insensitive set; matched against the leading
# name token and as a substring fallback.
READ_VERBS = {"get", "list", "read", "fetch", "search", "find", "show", "describe", "lookup", "query"}
WRITE_VERBS = {"create", "update", "set", "add", "put", "patch", "label", "unlabel", "move", "rename", "send", "draft"}
DESTRUCTIVE_VERBS = {"delete", "remove", "destroy", "purge", "drop", "clear", "wipe", "trash"}


def _word_count(text: str) -> int:
    return len([w for w in (text or "").split() if w.strip()])


def _schema_depth(schema: Any, _depth: int = 0) -> int:
    """Max nesting depth of an inputSchema."""
    if not isinstance(schema, dict):
        return _depth
    best = _depth
    props = schema.get("properties")
    if isinstance(props, dict):
        for v in props.values():
            best = max(best, _schema_depth(v, _depth + 1))
    items = schema.get("items")
    if isinstance(items, dict):
        best = max(best, _schema_depth(items, _depth + 1))
    for key in ("anyOf", "oneOf", "allOf"):
        for v in schema.get(key, []) or []:
            best = max(best, _schema_depth(v, _depth + 1))
    return best


def _collect_enums(schema: Any, out: list[int]) -> None:
    if not isinstance(schema, dict):
        return
    enum = schema.get("enum")
    if isinstance(enum, list):
        out.append(len(enum))
    props = schema.get("properties")
    if isinstance(props, dict):
        for v in props.values():
            _collect_enums(v, out)
    items = schema.get("items")
    if isinstance(items, dict):
        _collect_enums(items, out)
    for key in ("anyOf", "oneOf", "allOf"):
        for v in schema.get(key, []) or []:
            _collect_enums(v, out)


def _type_signature(schema: dict) -> tuple[tuple[str, str], ...]:
    """Sorted (property-name, json-type) pairs over the top level.

    Two tools with the same signature have the same call shape -- a strong
    merge signal (PLAN S10.1).
    """
    props = schema.get("properties") if isinstance(schema, dict) else None
    if not isinstance(props, dict):
        return ()
    sig = []
    for name, spec in props.items():
        t = "any"
        if isinstance(spec, dict):
            t = spec.get("type") or ("enum" if "enum" in spec else "any")
            if isinstance(t, list):
                t = "|".join(sorted(str(x) for x in t))
        sig.append((name, str(t)))
    return tuple(sorted(sig))


@dataclass
class SchemaFeatures:
    property_names: frozenset[str]
    property_count: int
    required: frozenset[str]
    max_depth: int
    enum_sizes: tuple[int, ...]
    type_signature: tuple[tuple[str, str], ...]
    description_words: int


@dataclass
class Tool:
    name: str
    description: str
    input_schema: dict[str, Any] = field(default_factory=dict)
    features: SchemaFeatures = field(init=False)
    verb: str = field(init=False)
    resource: str = field(init=False)
    behavior: str = field(init=False)  # read | write | destructive

    def __post_init__(self) -> None:
        self.features = self._extract_features()
        self.verb, self.resource = self._decompose_name()
        self.behavior = self._classify_behavior()

    def _extract_features(self) -> SchemaFeatures:
        schema = self.input_schema or {}
        props = schema.get("properties") if isinstance(schema, dict) else None
        props = props if isinstance(props, dict) else {}
        required = schema.get("required") if isinstance(schema, dict) else None
        required = required if isinstance(required, list) else []
        enums: list[int] = []
        _collect_enums(schema, enums)
        return SchemaFeatures(
            property_names=frozenset(props.keys()),
            property_count=len(props),
            required=frozenset(required),
            max_depth=_schema_depth(schema),
            enum_sizes=tuple(enums),
            type_signature=_type_signature(schema),
            description_words=_word_count(self.description),
        )

    def _decompose_name(self) -> tuple[str, str]:
        """Split a tool name into (verb, resource).

        ``create_label`` -> ("create", "label"); ``searchThreads`` ->
        ("search", "threads"); a bare ``ping`` -> ("ping", "").
        """
        raw = self.name
        # snake_case
        if "_" in raw:
            head, *rest = raw.split("_")
            return head.lower(), "_".join(rest).lower()
        # camelCase / PascalCase
        parts: list[str] = []
        cur = ""
        for ch in raw:
            if ch.isupper() and cur:
                parts.append(cur)
                cur = ch
            else:
                cur += ch
        if cur:
            parts.append(cur)
        if len(parts) >= 2:
            return parts[0].lower(), "".join(parts[1:]).lower()
        return raw.lower(), ""

    def _classify_behavior(self) -> str:
        v = self.verb
        if v in DESTRUCTIVE_VERBS or any(d in self.name.lower() for d in DESTRUCTIVE_VERBS):
            return "destructive"
        if v in READ_VERBS:
            return "read"
        if v in WRITE_VERBS:
            return "write"
        # Unknown verb: default to write (conservative for safety/manifest seeding).
        return "write"

    @property
    def is_pure_read(self) -> bool:
        return self.behavior == "read"

    def to_api_dict(self) -> dict[str, Any]:
        """Anthropic tools[] shape -- what count_tokens actually serializes."""
        return {
            "name": self.name,
            "description": self.description or "",
            "input_schema": self.input_schema or {"type": "object", "properties": {}},
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
            "verb": self.verb,
            "resource": self.resource,
            "behavior": self.behavior,
        }


@dataclass
class Inventory:
    """Normalized surface: the tools plus anything that silently lands in
    context at session start (server instructions, prompts, auto resources)."""

    tools: list[Tool] = field(default_factory=list)
    server_name: str | None = None
    server_version: str | None = None  # serverInfo.version (for run naming / drift)
    instructions: str | None = None  # hidden injector (PLAN S8)
    prompts: list[dict] = field(default_factory=list)
    resources: list[dict] = field(default_factory=list)
    transport: str | None = None  # stdio | http | sse | tools-json
    source: str | None = None  # original arg for provenance
    dynamic: bool | None = None  # JIT/progressive loading detected? (consolidate sets)

    def by_name(self, name: str) -> Tool | None:
        for t in self.tools:
            if t.name == name:
                return t
        return None

    @property
    def names(self) -> list[str]:
        return [t.name for t in self.tools]

    def to_tools_json(self) -> dict:
        """Serialize to the offline ``tools-json`` shape -- round-trips back
        through ``connect.from_tools_json`` so a live snapshot can be re-audited
        offline (PLAN S5)."""
        out: dict = {
            "tools": [
                {
                    "name": t.name,
                    "description": t.description,
                    "inputSchema": t.input_schema,
                }
                for t in self.tools
            ]
        }
        if self.server_name:
            out["serverName"] = self.server_name
        if self.server_version:
            out["serverVersion"] = self.server_version
        if self.instructions:
            out["instructions"] = self.instructions
        if self.prompts:
            out["prompts"] = self.prompts
        if self.resources:
            out["resources"] = self.resources
        return out

    def fingerprint(self) -> str:
        """Stable hash of the tool inventory -- keys a run for drift tracking
        across re-audits (PLAN S3.8)."""
        import hashlib
        import json

        payload = json.dumps(
            sorted(
                ({"name": t.name, "schema": t.input_schema, "desc": t.description}
                 for t in self.tools),
                key=lambda d: d["name"],
            ),
            sort_keys=True,
            default=str,
        )
        return hashlib.sha256(payload.encode()).hexdigest()[:16]

    @classmethod
    def from_tool_dicts(
        cls,
        raw_tools: Iterable[dict],
        *,
        server_name: str | None = None,
        server_version: str | None = None,
        instructions: str | None = None,
        prompts: list[dict] | None = None,
        resources: list[dict] | None = None,
        transport: str | None = None,
        source: str | None = None,
    ) -> "Inventory":
        tools: list[Tool] = []
        for rt in raw_tools:
            name = rt.get("name")
            if not name:
                continue
            schema = rt.get("inputSchema") or rt.get("input_schema") or {}
            tools.append(
                Tool(
                    name=name,
                    description=rt.get("description") or "",
                    input_schema=schema if isinstance(schema, dict) else {},
                )
            )
        return cls(
            tools=tools,
            server_name=server_name,
            server_version=server_version,
            instructions=instructions,
            prompts=prompts or [],
            resources=resources or [],
            transport=transport,
            source=source,
        )
