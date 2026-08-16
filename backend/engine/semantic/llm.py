"""
LLM abstraction.

Two hard rules, straight from the design doc:

  * The LLM never touches an Office file. It only ever returns JSON.
  * The engine must run *without* an LLM. NullLLM is the default, so the
    rule engine alone still produces a spec and a filled document.

Providers: anthropic | openai | gemini | ollama | null
Selected via OTE_LLM_PROVIDER, keyed via OTE_LLM_API_KEY (or the provider's
own env var). Every response is coerced to JSON; a malformed reply degrades
to "no opinion" rather than corrupting the pipeline.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

try:
    import httpx
except Exception:                                   # pragma: no cover
    httpx = None

_JSON_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)


def _coerce_json(text: str) -> Optional[Any]:
    if not text:
        return None
    m = _JSON_FENCE.search(text)
    raw = m.group(1) if m else text
    raw = raw.strip()
    start = min([i for i in (raw.find("{"), raw.find("[")) if i != -1] or [0])
    for end in (len(raw), raw.rfind("}") + 1, raw.rfind("]") + 1):
        chunk = raw[start:end].strip()
        if not chunk:
            continue
        try:
            return json.loads(chunk)
        except Exception:
            continue
    return None


@dataclass
class LLMResult:
    data: Any
    raw: str = ""
    ok: bool = True
    provider: str = "null"


class BaseLLM:
    name = "base"

    def available(self) -> bool:
        return False

    def complete_json(self, system: str, user: str, max_tokens: int = 2000) -> LLMResult:
        raise NotImplementedError

    # convenience
    def json_or(self, default: Any, system: str, user: str, max_tokens: int = 2000) -> Any:
        if not self.available():
            return default
        try:
            res = self.complete_json(system, user, max_tokens)
            return res.data if res.ok and res.data is not None else default
        except Exception:
            return default


class NullLLM(BaseLLM):
    """Rules-only mode. Everything still works, just with less nuance."""
    name = "null"

    def available(self) -> bool:
        return False

    def complete_json(self, system: str, user: str, max_tokens: int = 2000) -> LLMResult:
        return LLMResult(data=None, ok=False, provider="null")


class _HTTPLLM(BaseLLM):
    endpoint = ""
    default_model = ""

    def __init__(self, api_key: Optional[str] = None, model: Optional[str] = None,
                 timeout: float = 90.0, base_url: Optional[str] = None):
        self.api_key = api_key
        self.model = model or self.default_model
        self.timeout = timeout
        self.base_url = base_url

    def available(self) -> bool:
        return bool(httpx) and bool(self.api_key)

    def _post(self, url: str, headers: Dict[str, str], payload: dict) -> dict:
        with httpx.Client(timeout=self.timeout) as c:
            r = c.post(url, headers=headers, json=payload)
            r.raise_for_status()
            return r.json()


class AnthropicLLM(_HTTPLLM):
    name = "anthropic"
    default_model = "claude-sonnet-4-6"

    def complete_json(self, system: str, user: str, max_tokens: int = 2000) -> LLMResult:
        url = (self.base_url or "https://api.anthropic.com") + "/v1/messages"
        data = self._post(url, {
            "x-api-key": self.api_key or "",
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }, {
            "model": self.model,
            "max_tokens": max_tokens,
            "system": system + "\n\nRespond with JSON only. No prose, no code fences.",
            "messages": [{"role": "user", "content": user}],
        })
        text = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
        return LLMResult(_coerce_json(text), text, True, self.name)


class OpenAILLM(_HTTPLLM):
    name = "openai"
    default_model = "gpt-4o-mini"

    def complete_json(self, system: str, user: str, max_tokens: int = 2000) -> LLMResult:
        url = (self.base_url or "https://api.openai.com/v1") + "/chat/completions"
        data = self._post(url, {"Authorization": f"Bearer {self.api_key}"}, {
            "model": self.model,
            "max_tokens": max_tokens,
            "response_format": {"type": "json_object"},
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}],
        })
        text = data["choices"][0]["message"]["content"]
        return LLMResult(_coerce_json(text), text, True, self.name)


class GeminiLLM(_HTTPLLM):
    name = "gemini"
    default_model = "gemini-2.0-flash"

    def complete_json(self, system: str, user: str, max_tokens: int = 2000) -> LLMResult:
        base = self.base_url or "https://generativelanguage.googleapis.com/v1beta"
        url = f"{base}/models/{self.model}:generateContent?key={self.api_key}"
        data = self._post(url, {}, {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": {"maxOutputTokens": max_tokens,
                                 "responseMimeType": "application/json"},
        })
        text = "".join(p.get("text", "")
                       for p in data["candidates"][0]["content"]["parts"])
        return LLMResult(_coerce_json(text), text, True, self.name)


class OllamaLLM(_HTTPLLM):
    name = "ollama"
    default_model = "llama3.1"

    def available(self) -> bool:
        return bool(httpx)

    def complete_json(self, system: str, user: str, max_tokens: int = 2000) -> LLMResult:
        base = self.base_url or os.getenv("OLLAMA_HOST", "http://localhost:11434")
        data = self._post(base + "/api/chat", {}, {
            "model": self.model, "stream": False, "format": "json",
            "options": {"num_predict": max_tokens},
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}],
        })
        text = data.get("message", {}).get("content", "")
        return LLMResult(_coerce_json(text), text, True, self.name)


_PROVIDERS = {
    "anthropic": (AnthropicLLM, "ANTHROPIC_API_KEY"),
    "openai": (OpenAILLM, "OPENAI_API_KEY"),
    "gemini": (GeminiLLM, "GEMINI_API_KEY"),
    "ollama": (OllamaLLM, ""),
    "null": (NullLLM, ""),
}


def get_llm(provider: Optional[str] = None, model: Optional[str] = None,
            api_key: Optional[str] = None) -> BaseLLM:
    provider = (provider or os.getenv("OTE_LLM_PROVIDER", "null")).lower()
    cls, env = _PROVIDERS.get(provider, (NullLLM, ""))
    if cls is NullLLM:
        return NullLLM()
    key = api_key or os.getenv("OTE_LLM_API_KEY") or (os.getenv(env) if env else None)
    llm = cls(api_key=key, model=model or os.getenv("OTE_LLM_MODEL"))
    return llm if llm.available() else NullLLM()


# --------------------------------------------------------------------------
# Prompts. Kept here so the whole "what we ask the model" surface is auditable.
# --------------------------------------------------------------------------
ROLE_ARBITRATION_SYSTEM = """You classify fields in business document templates.
You are given a template field (its label, section, formatting) and a list of
candidate semantic roles produced by a rule engine. Choose the best role, or
return null if none fit.

Return JSON: {"role": "<role or null>", "confidence": 0.0-1.0, "reason": "<short>"}
Never invent a role that is not in the candidate list unless "allow_new" is true,
in which case you may return a new snake_case role name."""

SOURCE_EXTRACTION_SYSTEM = """You extract structured facts from a source document
(meeting summary, minutes, transcript, report) into canonical JSON.

Absolute rules:
- Extract ONLY what the text supports. Never infer, never invent, never fill gaps.
- If a field is not stated, omit it entirely. Do not guess.
- For every extracted field, quote the exact sentence you took it from as evidence.
- Dates must be ISO 8601 (YYYY-MM-DD) only when the text makes the date unambiguous.

Return JSON of this shape:
{
  "fields": {
    "<role>": {"value": <string|number>, "confidence": 0.0-1.0, "evidence": "<verbatim quote>"}
  },
  "collections": {
    "<role>": {
      "items": [{"<item_field>": <value>, ...}],
      "confidence": 0.0-1.0,
      "evidence": "<verbatim quote>"
    }
  }
}"""

VALUE_SELECTION_SYSTEM = """You match extracted source facts to template fields.
Given a template field (semantic role, label, section, expected format) and the
available canonical source data, return the value that belongs in that field.

Return JSON: {"value": <value or null>, "confidence": 0.0-1.0, "evidence": "<verbatim quote or null>"}
Return null when the source does not support a value. A null answer is correct
and expected; a fabricated one is a failure."""
