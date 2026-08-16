from __future__ import annotations

import json
import logging
import re
from functools import lru_cache
from typing import Any

from app.config import get_settings
from app.utils.aws import build_client, is_ssl_error, set_boto_ssl_verify
from app.utils.file_utils import AppError

log = logging.getLogger(__name__)


class BedrockService:
    def __init__(self) -> None:
        import boto3

        settings = get_settings()
        self.model_id = (settings.bedrock_model_id or "").strip()
        if not self.model_id:
            raise AppError(500, "BEDROCK_MODEL_ID is not configured")
        self.max_tokens = int(settings.bedrock_max_tokens or 4096)
        self.client, self._verify, self._client_kwargs = build_client(
            "bedrock-runtime", region=settings.aws_region
        )
        self._boto3 = boto3

    def _relax_ssl(self, exc: BaseException) -> bool:
        if self._verify is False or not is_ssl_error(exc):
            return False
        set_boto_ssl_verify(False)
        self._verify = False
        self.client = self._boto3.client("bedrock-runtime", verify=False, **self._client_kwargs)
        log.warning("Bedrock TLS verification failed; retrying without certificate verification")
        return True

    def _call(self, body: dict[str, Any]) -> str:
        try:
            response = self.client.invoke_model(
                modelId=self.model_id,
                body=json.dumps(body),
                contentType="application/json",
                accept="application/json",
            )
        except Exception as exc:
            if self._relax_ssl(exc):
                response = self.client.invoke_model(
                    modelId=self.model_id,
                    body=json.dumps(body),
                    contentType="application/json",
                    accept="application/json",
                )
            else:
                log.exception("Bedrock invoke failed")
                raise AppError(502, "Language model request failed") from exc
        payload = json.loads(response["body"].read())
        parts = payload.get("content") or []
        return "".join(p.get("text", "") for p in parts if p.get("type") == "text").strip()

    def invoke(self, prompt: str, *, system: str = "", temperature: float = 0.2) -> str:
        body: dict[str, Any] = {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": self.max_tokens,
            "temperature": temperature,
            "messages": [{"role": "user", "content": prompt}],
        }
        if system:
            body["system"] = system
        return self._call(body)

    def json_invoke(self, prompt: str, *, system: str = "", temperature: float = 0.1) -> dict[str, Any]:
        raw = self.invoke(prompt, system=system + " Return JSON only.", temperature=temperature)
        return parse_json_object(raw)


def parse_json_object(raw: str) -> dict[str, Any]:
    text = (raw or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?", "", text).strip()
        text = re.sub(r"```$", "", text).strip()
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.S)
        if not match:
            return {}
        try:
            data = json.loads(match.group(0))
            return data if isinstance(data, dict) else {}
        except json.JSONDecodeError:
            return {}


@lru_cache
def get_bedrock() -> BedrockService:
    return BedrockService()
