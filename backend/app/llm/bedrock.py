from __future__ import annotations

import json
from typing import Any

from app.llm.base import LLMProvider


class BedrockLLM(LLMProvider):
    def __init__(self, region: str, model_id: str):
        import boto3

        self.model_id = model_id
        self.client = boto3.client("bedrock-runtime", region_name=region)

    async def complete(self, system: str, user: str, temperature: float = 0.2) -> str:
        body: dict[str, Any] = {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 1200,
            "temperature": temperature,
            "system": system,
            "messages": [{"role": "user", "content": user}],
        }
        response = self.client.invoke_model(
            modelId=self.model_id,
            body=json.dumps(body),
            contentType="application/json",
            accept="application/json",
        )
        payload = json.loads(response["body"].read())
        parts = payload.get("content", [])
        return "".join(p.get("text", "") for p in parts if p.get("type") == "text")


class OpenAILLM(LLMProvider):
    def __init__(self, api_key: str, model: str):
        self.api_key = api_key
        self.model = model

    async def complete(self, system: str, user: str, temperature: float = 0.2) -> str:
        import httpx

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        body = {
            "model": self.model,
            "temperature": temperature,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        async with httpx.AsyncClient(timeout=60) as client:
            res = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers=headers,
                json=body,
            )
            res.raise_for_status()
            data = res.json()
            return data["choices"][0]["message"]["content"]
