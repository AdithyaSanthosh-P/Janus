"""ToolCatalog: manifest parsing, mutability detection, and quarantine.

Per `docs/prompt 2.txt` §7.7: each tool is validated independently, a tool
failing validation is quarantined (not the whole manifest), duplicate names
keep the first definition, and missing/unrecognized mutability defaults to
STATE_CHANGING (the safe default — W5) rather than being rejected.
"""

from __future__ import annotations

from dataclasses import dataclass

from prism_rt.model.types import ToolMutability
from prism_rt.store.facts import MutationGuard

_MUTABILITY_ALIASES: dict[str, ToolMutability] = {
    "read_only": ToolMutability.READ_ONLY,
    "read": ToolMutability.READ_ONLY,
    "readonly": ToolMutability.READ_ONLY,
    "read-only": ToolMutability.READ_ONLY,
    "state_changing": ToolMutability.STATE_CHANGING,
    "state-changing": ToolMutability.STATE_CHANGING,
    "write": ToolMutability.STATE_CHANGING,
}

_JSON_SCHEMA_TYPES = {"string", "number", "integer", "boolean", "array", "object", "null"}

_PY_TYPE_CHECKS = {
    "string": lambda v: isinstance(v, str),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "boolean": lambda v: isinstance(v, bool),
    "array": lambda v: isinstance(v, list),
    "object": lambda v: isinstance(v, dict),
    "null": lambda v: v is None,
}


def value_matches_schema_type(prop_schema: dict, value) -> bool:
    """True if `value` fits the property's declared JSON-schema `type`
    (or the property declares none) -- the same table `validate_args` uses."""
    check = _PY_TYPE_CHECKS.get((prop_schema or {}).get("type"))
    return check is None or check(value)


class ToolValidationError(Exception):
    pass


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    params_schema: dict
    output_schema: dict | None
    mutability: ToolMutability
    mutability_source: str  # "DECLARED" | "DEFAULTED"
    status: str  # "USABLE" | "QUARANTINED"
    frame_param: str | None = None
    quarantine_reason: str | None = None


@dataclass(frozen=True)
class ManifestParseResult:
    accepted: tuple[str, ...]
    quarantined: tuple[tuple[str | None, str], ...]  # (name, reason)


@dataclass(frozen=True)
class ArgValidationResult:
    valid: bool
    errors: tuple[str, ...] = ()


class ToolCatalog:
    def __init__(self, guard: MutationGuard) -> None:
        self._guard = guard
        self._tools: dict[str, ToolSpec] = {}
        self.version = 0

    def parse_manifest(self, tools: list[dict]) -> ManifestParseResult:
        self._guard.check()
        accepted: list[str] = []
        quarantined: list[tuple[str | None, str]] = []
        seen_names: set[str] = set()
        new_tools: dict[str, ToolSpec] = {}

        for raw in tools:
            name = raw.get("name") if isinstance(raw, dict) else None
            if not name or not isinstance(name, str):
                quarantined.append((name, "missing_or_invalid_name"))
                continue
            if name in seen_names:
                quarantined.append((name, "duplicate_name"))
                continue
            seen_names.add(name)
            try:
                spec = self._parse_one(name, raw)
                new_tools[name] = spec
                accepted.append(name)
            except ToolValidationError as exc:
                reason = str(exc)
                quarantined.append((name, reason))
                new_tools[name] = ToolSpec(
                    name=name,
                    description=str(raw.get("description", "")),
                    params_schema=raw.get("params_schema") or raw.get("parameters") or {},
                    output_schema=raw.get("output_schema"),
                    mutability=ToolMutability.STATE_CHANGING,
                    mutability_source="DEFAULTED",
                    status="QUARANTINED",
                    frame_param=None,
                    quarantine_reason=reason,
                )

        self._tools = new_tools
        self.version += 1
        return ManifestParseResult(accepted=tuple(accepted), quarantined=tuple(quarantined))

    def _parse_one(self, name: str, raw: dict) -> ToolSpec:
        params_schema = raw.get("params_schema")
        if params_schema is None:
            params_schema = raw.get("parameters")
        if params_schema is None:
            raise ToolValidationError("missing_params_schema")
        if not isinstance(params_schema, dict):
            raise ToolValidationError("params_schema_not_object")
        properties = params_schema.get("properties")
        if properties is not None and not isinstance(properties, dict):
            raise ToolValidationError("properties_not_object")
        if properties:
            for prop_name, prop_schema in properties.items():
                if not isinstance(prop_schema, dict):
                    raise ToolValidationError(f"property_schema_not_object:{prop_name}")
                prop_type = prop_schema.get("type")
                if prop_type is not None and prop_type not in _JSON_SCHEMA_TYPES:
                    raise ToolValidationError(f"unknown_type:{prop_name}:{prop_type}")

        raw_mutability = raw.get("mutability")
        if raw_mutability is not None:
            mutability = _MUTABILITY_ALIASES.get(str(raw_mutability).lower())
        else:
            mutability = None
        if mutability is None:
            mutability = ToolMutability.STATE_CHANGING
            mutability_source = "DEFAULTED"
        else:
            mutability_source = "DECLARED"

        return ToolSpec(
            name=name,
            description=str(raw.get("description", "")),
            params_schema=params_schema,
            output_schema=raw.get("output_schema"),
            mutability=mutability,
            mutability_source=mutability_source,
            status="USABLE",
            frame_param=raw.get("frame_param"),
        )

    def get(self, name: str) -> ToolSpec | None:
        return self._tools.get(name)

    def usable_tools(self) -> list[ToolSpec]:
        return [t for t in self._tools.values() if t.status == "USABLE"]

    def validate_args(self, name: str, args: dict) -> ArgValidationResult:
        spec = self._tools.get(name)
        if spec is None:
            return ArgValidationResult(False, ("unknown_tool",))
        if spec.status != "USABLE":
            return ArgValidationResult(False, ("tool_quarantined",))
        schema = spec.params_schema or {}
        properties = schema.get("properties", {}) or {}
        required = schema.get("required", []) or []
        errors: list[str] = []

        for field_name in required:
            if field_name not in args:
                errors.append(f"missing_required:{field_name}")
            elif isinstance(args[field_name], str) and not args[field_name].strip():
                # A blank string is not a value: found in the 1 Oct audit,
                # calculate_commute(origin_address="") went out as "filled".
                errors.append(f"blank_required:{field_name}")

        if schema.get("additionalProperties") is False:
            for key in args:
                if key not in properties:
                    errors.append(f"unexpected_property:{key}")

        for key, value in args.items():
            prop_schema = properties.get(key)
            if not prop_schema:
                continue
            expected_type = prop_schema.get("type")
            if expected_type:
                check = _PY_TYPE_CHECKS.get(expected_type)
                if check is not None and not check(value):
                    errors.append(f"type_mismatch:{key}")
            enum_values = prop_schema.get("enum")
            if enum_values is not None and value not in enum_values:
                errors.append(f"enum_mismatch:{key}")

        return ArgValidationResult(len(errors) == 0, tuple(errors))
