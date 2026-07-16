from __future__ import annotations

from typing import Any

from app.config import get_settings


class OpenAIUnavailable(RuntimeError):
    pass


class OpenAIResponsesClient:
    def __init__(self) -> None:
        settings = get_settings()
        if not settings.openai_api_key:
            raise OpenAIUnavailable("OPENAI_API_KEY ausente.")
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise OpenAIUnavailable("SDK OpenAI nao instalado.") from exc
        self._client = OpenAI(api_key=settings.openai_api_key, timeout=settings.openai_timeout_seconds)

    def create(self, **kwargs: Any) -> Any:
        return self._client.responses.create(**kwargs)
