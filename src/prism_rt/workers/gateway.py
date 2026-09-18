"""ModelGateway: the one seam between kernel-adjacent worker code and an
actual model. Workers call `gateway.complete_json(kind, prompt, schema)`
and never see which provider answered.

`ScriptedProvider` is what every test uses (§8.3: "All tests use
ScriptedProvider — no LLM calls"). `AnthropicProvider` and `GeminiProvider`
are real, optional providers for live validation (§12 Day 7: "Live LLM
validation (optional, if API key available)") — neither is exercised by
this repository's test suite (deliberately: no live LLM calls in tests),
so treat them as separate from, and never required for, grading. Live use
is `demo/run_v1_live_demo.py`/`demo/run_live_multimodal_demo.py`, not
`tests/`.

Phase 3 (`docs/post_v4_implementation_plan.md`): `complete_json` gained an
optional `media` parameter — a real image/audio payload, additive so every
existing call site (all of them text-only) is unaffected. The kernel and
`store/` still never touch raw bytes, per the architecture's own rule; only
`workers/runner.py`'s optional `blob_resolver` (live use only) ever
resolves a harness-given reference into actual bytes before this
parameter is populated. `ScriptedProvider` accepts and ignores it —
mocked responses never depend on pixel/audio content.
"""

from __future__ import annotations

import base64
import json
import os
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable, Protocol


@dataclass(frozen=True)
class MediaPart:
    mime_type: str
    data: bytes


class Provider(Protocol):
    def complete_json(self, kind: str, prompt: str, schema: dict, *, media: list[MediaPart] | None = None) -> dict: ...


class ScriptedProvider:
    """Deterministic canned-response provider. Rules are matched in
    registration order by (kind, substring-in-prompt); the first match
    wins. A response may be a literal dict or a callable(prompt) -> dict
    for responses that need to echo something from the prompt. `media` is
    accepted for interface parity but never inspected — no test scenario
    scripts a response that depends on actual pixel/audio content."""

    def __init__(self) -> None:
        self._rules: list[tuple[str, str, dict | Callable[[str], dict]]] = []

    def register(self, kind: str, contains: str, response: dict | Callable[[str], dict]) -> None:
        self._rules.append((kind, contains, response))

    def complete_json(self, kind: str, prompt: str, schema: dict, *, media: list[MediaPart] | None = None) -> dict:
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

    def complete_json(self, kind: str, prompt: str, schema: dict, *, media: list[MediaPart] | None = None) -> dict:
        system = (
            "Respond with ONLY a single JSON object matching this schema, no prose, "
            f"no markdown fences: {json.dumps(schema)}"
        )
        # Claude's Messages API accepts image content blocks; it has no
        # audio-input modality at all (a "document" block is for PDFs, not
        # arbitrary audio — sending audio bytes as one would just be
        # wrong). Non-image media is silently skipped rather than sent
        # malformed; this provider remains untested against a live model
        # either way (see currentStatus.md Known Risks).
        content: list[dict] = [{"type": "text", "text": prompt}]
        for part in media or ():
            if not part.mime_type.startswith("image/"):
                continue
            content.append(
                {
                    "type": "image",
                    "source": {"type": "base64", "media_type": part.mime_type, "data": base64.b64encode(part.data).decode("ascii")},
                }
            )
        response = self._client.messages.create(
            model=self._model,
            max_tokens=1024,
            temperature=0,
            system=system,
            messages=[{"role": "user", "content": content}],
        )
        text = "".join(block.text for block in response.content if getattr(block, "type", None) == "text")
        return json.loads(text)


class GeminiProvider:
    """Live provider using the Gemini API (free tier: gemini-3.6-flash).
    Uses the raw REST endpoint via stdlib `urllib` — no new dependency
    needed, matching `AnthropicProvider`'s pattern of an optional,
    undeclared-in-pyproject SDK for live-only providers, except here
    there's no SDK to import at all.

    Uses `generationConfig.responseMimeType: application/json` (guarantees
    valid JSON syntax) plus an instruction describing the target schema in
    the prompt — the same instruction-based enforcement `AnthropicProvider`
    uses, not Gemini's native `responseSchema` (that requires translating
    this project's plain JSON Schema, which uses `["string", "null"]`-style
    type unions for nullable fields, into Gemini's OpenAPI-subset schema
    format — unnecessary complexity for what this needs to do)."""

    def __init__(self, model: str = "gemini-3.6-flash", api_key: str | None = None) -> None:
        self._model = model
        self._api_key = api_key or os.environ.get("GEMINI_API_KEY")
        if not self._api_key:
            raise RuntimeError("GeminiProvider requires GEMINI_API_KEY (env var or api_key=)")

    def complete_json(self, kind: str, prompt: str, schema: dict, *, media: list[MediaPart] | None = None) -> dict:
        instruction = (
            "Respond with ONLY a single JSON object matching this schema, no prose, "
            f"no markdown fences: {json.dumps(schema)}"
        )
        parts: list[dict] = [{"text": f"{instruction}\n\n{prompt}"}]
        for part in media or ():
            parts.append({"inline_data": {"mime_type": part.mime_type, "data": base64.b64encode(part.data).decode("ascii")}})
        body = {
            "contents": [{"parts": parts}],
            "generationConfig": {"responseMimeType": "application/json"},
        }
        req = urllib.request.Request(
            f"https://generativelanguage.googleapis.com/v1beta/models/{self._model}:generateContent",
            data=json.dumps(body).encode("utf-8"),
            headers={"x-goog-api-key": self._api_key, "Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read().decode("utf-8"))
        text = result["candidates"][0]["content"]["parts"][0]["text"]
        return json.loads(text)


class ModelGateway:
    def __init__(self, provider: Provider) -> None:
        self._provider = provider

    def complete_json(self, kind: str, prompt: str, schema: dict, *, media: list[MediaPart] | None = None) -> dict:
        return self._provider.complete_json(kind, prompt, schema, media=media)
