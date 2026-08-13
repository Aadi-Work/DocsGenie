from __future__ import annotations

import httpx

from app.llm.base import LLMProvider


class GeminiLLM(LLMProvider):
    """Google Gemini via Generative Language API."""

    def __init__(self, api_key: str, model: str = "gemini-2.0-flash"):
        if not api_key:
            raise ValueError("GEMINI_API_KEY is required when LLM_PROVIDER=gemini")
        self.api_key = api_key.strip().strip('"').strip("'")
        self.model = model
        self.base = "https://generativelanguage.googleapis.com/v1beta"

    async def complete(self, system: str, user: str, temperature: float = 0.2) -> str:
        url = f"{self.base}/models/{self.model}:generateContent"
        payload = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": {
                "temperature": temperature,
                "maxOutputTokens": 4096,
            },
        }
        async with httpx.AsyncClient(timeout=90) as client:
            res = await client.post(url, params={"key": self.api_key}, json=payload)
            if res.status_code >= 400:
                raise RuntimeError(f"Gemini API error {res.status_code}: {res.text}")
            data = res.json()
        candidates = data.get("candidates") or []
        if not candidates:
            return ""
        parts = ((candidates[0].get("content") or {}).get("parts")) or []
        return "".join(p.get("text", "") for p in parts)
