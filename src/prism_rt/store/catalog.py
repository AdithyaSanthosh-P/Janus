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


def _type_names(declared) -> list[str]:
    """A property's declared `type` as a list: a JSON-schema type may be one
    name or a list of names (any one of which the value may match)."""
    if isinstance(declared, list):
        return [t for t in declared if isinstance(t, str)]
    return [declared] if isinstance(declared, str) else []


def value_matches_schema_type(prop_schema: dict, value) -> bool:
    """True if `value` fits the property's declared JSON-schema `type`
    (any member of a list of types; or the property declares none) -- the
    same table `validate_args` uses."""
    checks = [_PY_TYPE_CHECKS[t] for t in _type_names((prop_schema or {}).get("type")) if t in _PY_TYPE_CHECKS]
    return not checks or any(check(value) for check in checks)


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
    # A device command ("pause", "resume"): each request is its own action,
    # so a later request for the same tool is not a repeat of an earlier one
    # that went through. Declared in the manifest; the benchmark's never is.
    repeatable: bool = False
    # A repeatable action whose result Janus cannot see afterwards (a phone
    # alarm): the same action with the same arguments in a later request is
    # asked about ("shall I set it again?"), neither refused nor just re-sent.
    ask_before_repeat: bool = False
    # Security-sensitive write: CommitGate G12 holds it until the user says yes.
    requires_confirmation: bool = False
    # Evidence-bound confirmation (kernel/evidence.py): the argument naming
    # the device the write targets, and the device value at which the write
    # has nothing left to do ("locked", "{state}"). Declared in the manifest.
    watch: str | None = None
    done_when: str | None = None
    # How the action is said ("Lock the {device}"), from the manifest; also the
    # companion app's card title. replies.action_phrase.
    label: str | None = None
    # Spoken after the device's new state ("21" -> "21 degrees"); manifest.
    state_unit: str | None = None
    # The final for a request made only of such tools is the result's `say`
    # text, word for word (demo speech polish; replies.said_result_final).
    say_result: bool = False
    # What is said, and recorded on the card, when a held write is withdrawn
    # because the device reached `done_when` by itself (kernel/evidence.py);
    # {device} and {value} as in EVIDENCE_WITHDRAWN. Manifest; None = the
    # generic wording.
    withdrawn_text: str | None = None
    withdrawn_reason: str | None = None


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
                names = prop_type if isinstance(prop_type, list) else [prop_type]
                if prop_type is not None and (not names or any(t not in _JSON_SCHEMA_TYPES for t in names if isinstance(t, str))
                                              or not all(isinstance(t, str) for t in names)):
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
            repeatable=raw.get("repeatable") is True,
            ask_before_repeat=raw.get("ask_before_repeat") is True,
            requires_confirmation=raw.get("requires_confirmation") is True,
            watch=raw.get("watch") if isinstance(raw.get("watch"), str) else None,
            done_when=raw.get("done_when") if isinstance(raw.get("done_when"), str) else None,
            label=raw.get("label") if isinstance(raw.get("label"), str) and raw.get("label") else None,
            state_unit=raw.get("state_unit") if isinstance(raw.get("state_unit"), str) else None,
            say_result=raw.get("say_result") is True,
            withdrawn_text=raw.get("withdrawn_text") if isinstance(raw.get("withdrawn_text"), str) else None,
            withdrawn_reason=raw.get("withdrawn_reason") if isinstance(raw.get("withdrawn_reason"), str) else None,
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
            if prop_schema.get("type") and not value_matches_schema_type(prop_schema, value):
                errors.append(f"type_mismatch:{key}")
            enum_values = prop_schema.get("enum")
            if enum_values is not None and value not in enum_values:
                errors.append(f"enum_mismatch:{key}")

        return ArgValidationResult(len(errors) == 0, tuple(errors))
