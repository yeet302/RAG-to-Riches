"""Shared JSON transport for Azure Chat Completions and explicit Responses endpoints."""
from __future__ import annotations

import httpx

from settings import Settings


def completion_url(settings: Settings) -> str:
    endpoint = settings.llm_endpoint.rstrip("/")
    if endpoint.endswith(("/chat/completions", "/responses")):
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
    url = completion_url(settings)
    uses_responses = url.endswith("/responses")
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_content},
    ]
    if uses_responses:
        payload = {
            "model": settings.llm_model,
            "input": messages,
            "text": {"format": {"type": "json_object"}},
            "store": False,
        }
    else:
        payload = {
            "model": settings.llm_model,
            "messages": messages,
            "response_format": {"type": "json_object"},
        }
    async with httpx.AsyncClient(timeout=timeout_seconds, follow_redirects=False,
                                 trust_env=False, transport=transport) as client:
        response = await client.post(
            url, params=params, headers={"api-key": settings.llm_api_key}, json=payload,
        )
        response.raise_for_status()
        body = response.json()
        if uses_responses:
            content = responses_text(body)
        else:
            choice = body["choices"][0]
            message = choice["message"]
            if choice.get("finish_reason") != "stop" or message.get("refusal"):
                raise ValueError("Incomplete or refused model response")
            content = message["content"]
        if not isinstance(content, str) or not content.strip():
            raise ValueError("Missing model text")
        return content


def responses_text(body: dict) -> str:
    """Extract completed assistant text, ignoring reasoning and rejecting refusals."""
    if body.get("status") != "completed" or body.get("error"):
        raise ValueError("Incomplete or failed model response")
    chunks = []
    for item in body["output"]:
        if item.get("type") != "message":
            continue
        if item.get("role") != "assistant" or item.get("status") != "completed":
            raise ValueError("Incomplete model message")
        for part in item["content"]:
            if part.get("type") == "refusal":
                raise ValueError("Refused model response")
            if part.get("type") == "output_text":
                text = part["text"]
                if not isinstance(text, str):
                    raise ValueError("Invalid model text")
                chunks.append(text)
    return "".join(chunks)
