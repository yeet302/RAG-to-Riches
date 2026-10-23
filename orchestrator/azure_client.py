"""Shared bounded JSON Chat Completions transport for the configured Azure deployment."""
from __future__ import annotations

import httpx

from settings import Settings


def completion_url(settings: Settings) -> str:
    endpoint = settings.llm_endpoint.rstrip("/")
    if endpoint.endswith("/chat/completions"):
        return endpoint
    if endpoint.endswith(("/openai/v1", "/models")):
        return endpoint + "/chat/completions"
    suffix = "/openai/v1/chat/completions" if settings.llm_api_style == "openai_v1" else "/models/chat/completions"
    return endpoint + suffix


async def complete_json(settings: Settings, system_prompt: str, user_content: str, *,
                        timeout_seconds: float,
                        transport: httpx.AsyncBaseTransport | None = None) -> str:
    params = {}
    if settings.llm_api_version:
        params["api-version"] = settings.llm_api_version
    elif settings.llm_api_style == "inference":
        params["api-version"] = "2024-05-01-preview"
    async with httpx.AsyncClient(timeout=timeout_seconds, follow_redirects=False,
                                 trust_env=False, transport=transport) as client:
        response = await client.post(
            completion_url(settings), params=params,
            headers={"api-key": settings.llm_api_key},
            json={
                "model": settings.llm_model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_content},
                ],
                "response_format": {"type": "json_object"},
            },
        )
        response.raise_for_status()
        choice = response.json()["choices"][0]
        message = choice["message"]
        if choice.get("finish_reason") != "stop" or message.get("refusal"):
            raise ValueError("Incomplete or refused model response")
        content = message["content"]
        if not isinstance(content, str) or not content.strip():
            raise ValueError("Missing model text")
        return content
