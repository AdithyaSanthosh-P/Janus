"""Relaxed housing tool contract for the FDB-v3 profile
(Config.fdb_relaxed_housing_schema, default off).

The published signature makes every search argument required
(`search_apartments(city, bedrooms, max_price)`) and types every filter value
as a string, so an agent following it must either ask for, or assume aloud, a
value the user never gave, and cannot express "pets allowed" at all. Here the
city is the only required search argument, the search also takes a
`pets_allowed` flag, and a filter value may be a number, a boolean or a string.
FDB's mock backend still requires bedrooms and a budget, so the adapter fills
neutral values for the backend call only (`BACKEND_FILLS`): the logged call
keeps exactly what the user said, and any result field computed from a fill is
removed before the agent sees it. Every other tool is left exactly as published.
"""

from __future__ import annotations

import copy

from prism_rt.config import RELAXED_HOUSING_TOOLS as RELAXED_TOOLS  # noqa: F401 -- re-exported

_STATED_ONLY = " Only include it if the user stated it."

# Per tool, per parameter: the value sent to the backend when the user gave
# none, and the result field the backend computes from it (removed again).
BACKEND_FILLS: dict[str, dict[str, dict]] = {
    "search_apartments": {
        "bedrooms": {"value": 1, "derived": "beds"},
        "max_price": {"value": 1_000_000, "derived": "price"},
    },
}


def apply_schema_overrides(manifest: list[dict]) -> list[dict]:
    """A copy of `manifest` with the housing tools relaxed; others unchanged."""
    out = []
    for tool in manifest:
        if tool.get("name") == "search_apartments":
            tool = copy.deepcopy(tool)
            schema = tool["params_schema"]
            props = schema["properties"]
            for name in ("bedrooms", "max_price"):
                if name in props:
                    props[name]["description"] = props[name].get("description", "").rstrip(".") + "." + _STATED_ONLY
            props["pets_allowed"] = {
                "type": "boolean",
                "description": "True when the user wants a place that allows pets." + _STATED_ONLY,
            }
            schema["required"] = ["city"]
        elif tool.get("name") == "update_search_filter":
            tool = copy.deepcopy(tool)
            props = tool["params_schema"]["properties"]
            props["filter_name"]["description"] = (
                "Filter key to modify, in snake_case. When the filter corresponds to a parameter "
                "of search_apartments, use that parameter's exact name."
            )
            props["value"]["type"] = ["number", "boolean", "string"]
            props["value"]["description"] = (
                "Filter value to apply: a number for amounts and counts, true/false for yes/no "
                "filters, otherwise the text the user gave."
            )
        out.append(tool)
    return out


def maybe_apply(manifest: list[dict], enabled: bool) -> list[dict]:
    return apply_schema_overrides(manifest) if enabled else manifest


def strip_derived(result, filled: set[str], fills: dict[str, dict]):
    """Removes, from each result row, the fields computed from a filled value."""
    keys = {fills[name]["derived"] for name in filled if fills[name].get("derived")}
    if not keys or not isinstance(result, dict) or not isinstance(result.get("results"), list):
        return result
    result = dict(result)
    result["results"] = [
        {k: v for k, v in row.items() if k not in keys} if isinstance(row, dict) else row for row in result["results"]
    ]
    return result
