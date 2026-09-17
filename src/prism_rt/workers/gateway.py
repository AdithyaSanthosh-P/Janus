"""ModelGateway: the one seam between kernel-adjacent worker code and an
actual model. Workers call `gateway.complete_json(kind, prompt, schema)`
and never see which provider answered.

`ScriptedProvider` is what every V1 test uses (§8.3: "All tests use
ScriptedProvider — no LLM calls"). `AnthropicProvider` is a real,
optional provider for the live validation pass mentioned in
`docs/sonnet_implementation_plan.md` §12 Day 7 ("Live LLM validation
(optional, if API key available)") — it is not exercised by anything in
this repository's test suite (no network access / API key in this
environment), so treat it as unverified until run against a live key.
"""

from __future__ import annotations

import json
from typing import Any, Callable, Protocol


class Provider(Protocol):
    def complete_json(self, kind: str, prompt: str, schema: dict) -> dict: ...


class ScriptedProvider:
    """Deterministic canned-response provider. Rules are matched in
    registration order by (kind, substring-in-prompt); the first match
    wins. A response may be a literal dict or a callable(prompt) -> dict
    for responses that need to echo something from the prompt."""

    def __init__(self) -> None:
        self._rules: list[tuple[str, str, dict | Callable[[str], dict]]] = []

    def register(self, kind: str, contains: str, response: dict | Callable[[str], dict]) -> None:
        self._rules.append((kind, contains, response))

    def complete_json(self, kind: str, prompt: str, schema: dict) -> dict:
        for rule_kind, contains, response in self._rules:
            if rule_kind == kind and contains in prompt:
                return response(prompt) if callable(response) else dict(response)
        raise LookupError(f"ScriptedProvider: no rule for kind={kind!r} matching prompt={prompt!r}")


class AnthropicProvider:
    """Live provider using the Anthropic API. Requires the `anthropic`
    package and an API key; both are optional (not in pyproject's base
    dependencies) since nothing required for grading depends on this path."""

    def __init__(self, model: str = "claude-sonnet-5", api_key: str | None = None) -> None:
        try:
            import anthropic
        except ImportError as exc:
            raise RuntimeError("AnthropicProvider requires the 'anthropic' package") from exc
        self._client = anthropic.Anthropic(api_key=api_key)
        self._model = model

    def complete_json(self, kind: str, prompt: str, schema: dict) -> dict:
        system = (
            "Respond with ONLY a single JSON object matching this schema, no prose, "
            f"no markdown fences: {json.dumps(schema)}"
        )
        response = self._client.messages.create(
            model=self._model,
            max_tokens=1024,
            temperature=0,
            system=system,
            messages=[{"role": "user", "content": prompt}],
        )
        text = "".join(block.text for block in response.content if getattr(block, "type", None) == "text")
        return json.loads(text)


class ModelGateway:
    def __init__(self, provider: Provider) -> None:
        self._provider = provider

    def complete_json(self, kind: str, prompt: str, schema: dict) -> dict:
        return self._provider.complete_json(kind, prompt, schema)
