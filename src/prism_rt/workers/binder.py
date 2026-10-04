"""Binder worker: earlier results + the user's request -> chained arguments.

S3 late binding (`kernel/binder.py`): once the step a chained argument
depends on has actually run, pick that argument's value out of the real
result -- "the cheapest one", "from there", "whatever you find". Same
`run_*`/`build_prompt` split every other worker module uses. The value
must come from the results: when the field the user's words point at
doesn't exist there (e.g. an address the result never includes), the
chosen item's own identifier is the honest pick, never an invented value.

A conditional step (`view["condition"]`) is also checked here: `proceed`
says whether the user's condition holds for the earlier results. A
condition the results cannot settle (a matter of the user's own taste)
does not hold -- the kernel then skips the action and the answer says so,
rather than doing something the user only might have wanted.
"""

from __future__ import annotations

import json

from prism_rt.workers.gateway import ModelGateway

BIND_SCHEMA = {
    "type": "object",
    "properties": {"args": {"type": "object"}, "proceed": {"type": "boolean"}},
    "required": ["args"],
}


def build_prompt(view: dict) -> str:
    wanted = "\n".join(
        f"  - {f['param']} ({f.get('type') or 'any'}; {f.get('description') or 'no description'}): "
        f"from {f.get('from')}'s result, the item the user meant by {f.get('which') or 'their request'!r}"
        + (f", probably its {f['field']!r} field" if f.get("field") else "")
        for f in view.get("fill") or []
    )
    condition = view.get("condition")
    if condition:
        check = (
            f"The next call runs ONLY IF this holds: {condition!r}. Check it against "
            "the earlier results' actual values (prices, times, rates, counts) and set "
            "proceed to true if it holds, false if it does not. If the results cannot "
            "settle it (it depends on the user's own opinion, or the value is not in "
            "the results), proceed is false. When proceed is false, args may be {}.\n"
        )
        if not view.get("fill"):
            return (
                f"user request: {view.get('request', '')!r}\n"
                f"next call: {view.get('tool')} with args {json.dumps(view.get('known_args') or {})}\n"
                f"earlier results: {json.dumps(view.get('earlier_results') or [], default=str)}\n"
                + check
                + "Return {\"proceed\": <true|false>, \"args\": {}}."
            )
    else:
        check = ""
    return (
        f"user request: {view.get('request', '')!r}\n"
        f"next call: {view.get('tool')} with already-known args {json.dumps(view.get('known_args') or {})}\n"
        f"earlier results: {json.dumps(view.get('earlier_results') or [], default=str)}\n"
        f"Fill these arguments of the next call from the earlier results:\n{wanted}\n"
        "Rules: pick the item the user described (cheapest, first, the one "
        "found, ...) and copy its value exactly as it appears in the results. "
        "If the named field doesn't exist, use that item's own identifier "
        "(e.g. its id or name) -- never invent a value that is not in the "
        "results. "
        + (check + "Return {\"proceed\": <true|false>, \"args\": {<param>: <value>}}." if check else "Return {\"args\": {<param>: <value>}}.")
    )


def run_bind(gateway: ModelGateway, view: dict) -> dict:
    return gateway.complete_json("bind", build_prompt(view), BIND_SCHEMA)
