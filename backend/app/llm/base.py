from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class LLMProvider(ABC):
    @abstractmethod
    async def complete(self, system: str, user: str, temperature: float = 0.2) -> str:
        raise NotImplementedError

    async def json_complete(self, system: str, user: str) -> dict[str, Any]:
        import json
        import re

        raw = await self.complete(system, user)
        match = re.search(r"\{[\s\S]*\}", raw)
        if not match:
            return {}
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            return {}
