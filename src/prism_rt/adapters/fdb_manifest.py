"""FDB-v3 tool manifest introspection (docs/fdb_v3_implementation_plan.md §3.3).

Generates a Janus `ToolCatalog.parse_manifest`-compatible tool list by
reading Full-Duplex-Bench's own `AssistantFnc` class in `lk_agent_tool.py`
-- never hand-typed, never special-cased per tool name. Uses Python's
`ast` module on the source text rather than importing `lk_agent_tool.py`
(which needs `livekit-agents` installed and a live LiveKit connection to
import cleanly) -- introspection has no such dependency and stays usable
from a plain venv.

Per tool: `inspect.signature`-equivalent parameter extraction (type from
the annotation, `required` = parameters with no default, `default`
recorded when present) plus the docstring's `Args:` block for per-param
descriptions, and the `@ai_callable_decorator(description=...)`
argument for the tool's own description. Mutability is left undeclared
(FDB publishes none) -- `ToolCatalog`'s safe default (STATE_CHANGING, W5)
applies, intentional under §5 of the plan, not an oversight.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass

_ARGS_LINE_RE = re.compile(r"^\s*(\w+)\s*:\s*(.+?)\s*$")

_TYPE_MAP = {
    "str": "string",
    "int": "integer",
    "float": "number",
    "bool": "boolean",
    "dict": "object",
    "list": "array",
    "Any": None,
}


@dataclass(frozen=True)
class IntrospectedTool:
    name: str
    description: str
    params_schema: dict


def _annotation_type(node: ast.AST | None) -> str | None:
    if node is None:
        return None
    if isinstance(node, ast.Name):
        return _TYPE_MAP.get(node.id)
    if isinstance(node, ast.Constant) and node.value is None:
        return None
    if isinstance(node, ast.Subscript):
        # Optional[X] / X | None spelled as a subscript (typing.Optional)
        base = node.value
        base_name = base.id if isinstance(base, ast.Name) else getattr(base, "attr", None)
        if base_name == "Optional":
            return _annotation_type(node.slice)
        return None
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        # `str | None` (PEP 604)
        left = _annotation_type(node.left)
        right = _annotation_type(node.right)
        return left or right
    return None


def _default_literal(node: ast.AST | None):
    if node is None:
        return None, False
    try:
        return ast.literal_eval(node), True
    except (ValueError, TypeError):
        return None, False


def _parse_args_docstring(docstring: str | None) -> dict[str, str]:
    """Pulls per-parameter descriptions out of a FDB tool method's
    `Args:\\n    name: description` docstring block."""
    out: dict[str, str] = {}
    if not docstring:
        return out
    in_args = False
    for line in docstring.splitlines():
        stripped = line.strip()
        if stripped == "Args:":
            in_args = True
            continue
        if not in_args:
            continue
        if not stripped:
            continue
        m = _ARGS_LINE_RE.match(line)
        if m:
            out[m.group(1)] = m.group(2)
        else:
            break
    return out


def _decorator_description(decorator: ast.AST) -> str | None:
    if not isinstance(decorator, ast.Call):
        return None
    for kw in decorator.keywords:
        if kw.arg == "description":
            value, ok = _default_literal(kw.value)
            if ok and isinstance(value, str):
                return value
    return None


def _method_to_tool(node) -> IntrospectedTool | None:
    description = None
    for dec in node.decorator_list:
        description = _decorator_description(dec)
        if description is not None:
            break
    if description is None:
        return None  # not an @ai_callable_decorator(...)-marked tool method

    docstring = ast.get_docstring(node)
    arg_docs = _parse_args_docstring(docstring)

    args = node.args
    self_offset = 1 if args.args and args.args[0].arg == "self" else 0
    positional = args.args[self_offset:]
    defaults = args.defaults
    default_for = {}
    for i, default_node in enumerate(defaults):
        arg_index = len(positional) - len(defaults) + i
        if 0 <= arg_index < len(positional):
            default_for[arg_index] = default_node

    properties: dict = {}
    required: list[str] = []
    for i, arg in enumerate(positional):
        json_type = _annotation_type(arg.annotation)
        prop: dict = {}
        if json_type is not None:
            prop["type"] = json_type
        if arg.arg in arg_docs:
            prop["description"] = arg_docs[arg.arg]
        if i in default_for:
            value, ok = _default_literal(default_for[i])
            if ok and value is not None:
                prop["default"] = value
        else:
            required.append(arg.arg)
        properties[arg.arg] = prop

    params_schema = {"type": "object", "properties": properties}
    if required:
        params_schema["required"] = required

    return IntrospectedTool(name=node.name, description=description, params_schema=params_schema)


def introspect_source(source: str, class_name: str = "AssistantFnc") -> list[IntrospectedTool]:
    tree = ast.parse(source)
    tools: list[IntrospectedTool] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    tool = _method_to_tool(item)
                    if tool is not None:
                        tools.append(tool)
            break
    return tools


def introspect_file(path: str, class_name: str = "AssistantFnc") -> list[IntrospectedTool]:
    with open(path, "r", encoding="utf-8") as f:
        return introspect_source(f.read(), class_name=class_name)


def to_catalog_manifest(tools: list[IntrospectedTool]) -> list[dict]:
    """`IntrospectedTool` list -> `ToolCatalog.parse_manifest`'s input
    shape. No `mutability` key -- see module docstring."""
    return [
        {"name": t.name, "description": t.description, "params_schema": t.params_schema}
        for t in tools
    ]
