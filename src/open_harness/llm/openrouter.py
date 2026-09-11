from __future__ import annotations

from typing import Any

import httpx

from open_harness.schema.request import LLMRequest

DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"


class OpenRouterClient:
  """Translate requests and transport streamed responses from OpenRouter."""

  def __init__(
    self,
    *,
    api_key: str,
    referer: str,
    title: str,
    header_timeout: float = 300.0,
    chunk_timeout: float = 300.0,
    base_url: str = DEFAULT_BASE_URL,
    transport: httpx.AsyncBaseTransport | None = None,
  ) -> None:
    self._api_key = api_key
    self._referer = referer
    self._title = title
    self._header_timeout = header_timeout
    self._chunk_timeout = chunk_timeout
    self._base_url = base_url
    self._transport = transport

  def _headers(self) -> dict[str, str]:
    return {
      "Authorization": f"Bearer {self._api_key}",
      "HTTP-Referer": self._referer,
      "X-Title": self._title,
      "Content-Type": "application/json",
    }

  def _body(self, request: LLMRequest) -> dict[str, Any]:
    messages: list[dict[str, Any]] = [
      {"role": "system", "content": block} for block in request.system if block.strip()
    ]
    messages.extend(request.messages)

    body: dict[str, Any] = {
      "model": request.model,
      "messages": messages,
      "stream": True,
      "usage": {"include": True},
    }

    if request.tools:
      body["tools"] = [
        {
          "type": "function",
          "function": {
            "name": tool.name,
            "description": tool.description,
            "parameters": tool.parameters,
          },
        }
        for tool in request.tools
      ]
      body["tool_choice"] = request.tool_choice

    if request.temperature is not None:
      body["temperature"] = request.temperature

    if request.max_tokens is not None:
      body["max_tokens"] = request.max_tokens

    if request.reasoning_effort is not None:
      body["reasoning"] = {"effort": request.reasoning_effort}

    return body
