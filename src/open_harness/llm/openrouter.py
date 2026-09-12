from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import suppress
from typing import Any, cast

import httpx

from open_harness.llm.sse import SSEParser
from open_harness.llm.translate import ChunkTranslator
from open_harness.schema.events import LLMEvent, ProviderError
from open_harness.schema.request import LLMRequest

DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"
_CONNECT_TIMEOUT = 30.0


def _is_retryable_status(status: int) -> bool:
  return status in (408, 429) or 500 <= status < 600


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

  async def _read_chunks(self, response: httpx.Response) -> AsyncIterator[bytes]:
    chunks = response.aiter_bytes()

    while True:
      try:
        async with asyncio.timeout(self._chunk_timeout):
          chunk = await anext(chunks)
      except StopAsyncIteration:
        return

      yield chunk

  async def stream(self, request: LLMRequest) -> AsyncIterator[LLMEvent]:
    parser = SSEParser()
    translator = ChunkTranslator()

    # Separate deadlines below cover headers and body reads independently
    timeout = httpx.Timeout(_CONNECT_TIMEOUT, read=None)

    async with httpx.AsyncClient(
      timeout=timeout,
      base_url=self._base_url,
      transport=self._transport,
    ) as http:
      outgoing = http.build_request(
        "POST",
        "chat/completions",
        json=self._body(request),
        headers=self._headers(),
      )

      try:
        async with asyncio.timeout(self._header_timeout):
          response = await http.send(outgoing, stream=True)
      except TimeoutError:
        yield ProviderError(
          message=f"No response headers with {self._header_timeout}s",
          retryable=True,
        )
        return
      except httpx.HTTPError as exc:
        yield ProviderError(
          message=str(exc) or "Unable to open the provider stream",
          retryable=True,
        )
        return

      try:
        if response.status_code >= 400:
          detail = bytearray()

          # Missing diagnostic text must not change the known HTTP classification
          with suppress(TimeoutError, httpx.HTTPError):
            async for raw in self._read_chunks(response):
              detail.extend(raw[: 500 - len(detail)])
              if len(detail) >= 500:
                break

          message = (
            detail.decode("utf-8", errors="replace")
            or "Provider returned no readable error details"
          )

          yield ProviderError(
            message=f"HTTP {response.status_code}: {message}",
            status=response.status_code,
            retryable=_is_retryable_status(response.status_code),
          )
          return

        async for raw in self._read_chunks(response):
          for payload in parser.feed(raw):
            if payload == "[DONE]":
              for event in translator.finish():
                yield event
              return

            try:
              value: Any = json.loads(payload)
            except json.JSONDecodeError:
              continue

            if not isinstance(value, dict):
              continue

            chunk = cast(dict[str, Any], value)

            if "error" in chunk:
              error = chunk["error"]

              if isinstance(error, dict):
                details = cast(dict[str, Any], error)
                code = details.get("code")
                message = str(details.get("message") or "Provider reported an error")
              else:
                code = None
                message = str(error or "Provider reported an error")

              status = code if isinstance(code, int) and not isinstance(code, bool) else None

              yield ProviderError(
                message=message,
                status=status,
                retryable=True if status is None else _is_retryable_status(status),
              )
              return

            for event in translator.translate(chunk):
              yield event

        for event in translator.finish():
          yield event

      except TimeoutError:
        yield ProviderError(
          message=f"Provider stream stalled for more than {self._chunk_timeout}s", retryable=True
        )
      except httpx.HTTPError as exc:
        yield ProviderError(
          message=str(exc) or "Provider stream failed",
          retryable=True,
        )
      finally:
        await response.aclose()
