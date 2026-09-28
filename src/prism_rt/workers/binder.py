"""Binder worker: earlier results + the user's request -> chained arguments.

S3 late binding (`kernel/binder.py`): once the step a chained argument
depends on has actually run, pick that argument's value out of the real
result -- "the cheapest one", "from there", "whatever you find". Same
`run_*`/`build_prompt` split every other worker module uses. The value
must come from the results: when the field the user's words point at
doesn't exist there (e.g. an address the result never includes), the
chosen item's own identifier is the honest pick, never an invented value.
"""

from __future__ import annotations

import json

from prism_rt.workers.gateway import ModelGateway

BIND_SCHEMA = {
    "type": "object",
    "properties": {"args": {"type": "object"}},
    "required": ["args"],
}


def build_prompt(view: dict) -> str:
    wanted = "\n".join(
        f"  - {f['param']} ({f.get('type') or 'any'}; {f.get('description') or 'no description'}): "
        f"from {f.get('from')}'s result, the item the user meant by {f.get('which') or 'their request'!r}"
        + (f", probably its {f['field']!r} field" if f.get("field") else "")
        for f in view.get("fill") or []
    )
    return (
        f"user request: {view.get('request', '')!r}\n"
        f"next call: {view.get('tool')} with already-known args {json.dumps(view.get('known_args') or {})}\n"
        f"earlier results: {json.dumps(view.get('earlier_results') or [], default=str)}\n"
        f"Fill these arguments of the next call from the earlier results:\n{wanted}\n"
        "Rules: pick the item the user described (cheapest, first, the one "
        "found, ...) and copy its value exactly as it appears in the results. "
        "If the named field doesn't exist, use that item's own identifier "
        "(e.g. its id or name) -- never invent a value that is not in the "
        "results. Return {\"args\": {<param>: <value>}}."
    )


def run_bind(gateway: ModelGateway, view: dict) -> dict:
    return gateway.complete_json("bind", build_prompt(view), BIND_SCHEMA)
