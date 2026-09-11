from __future__ import annotations

import asyncio
import json
from collections.abc import Callable, Coroutine

import httpx
import pytest

from open_harness.llm.openrouter import OpenRouterClient
from open_harness.schema.events import Finish, ProviderError, TextDelta
from open_harness.schema.request import LLMRequest, ToolDefinition

SSE_BODY = (
  b'data: {"choices":[{"delta":{"content":"Hi"},"finish_reason":null}]}\n\n'
  b'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\n'
  b'data: {"choices":[],"usage":{"prompt_tokens":5,"completion_tokens":2}}\n\n'
  b"data: [DONE]\n\n"
)


def _client(
  handler: (
    Callable[[httpx.Request], httpx.Response]
    | Callable[[httpx.Request], Coroutine[None, None, httpx.Response]]
  ),
  *,
  header_timeout: float = 300.0,
  chunk_timeout: float = 300.0,
) -> OpenRouterClient:
  return OpenRouterClient(
    api_key="sk-test",
    referer="https://example.test",
    title="test",
    transport=httpx.MockTransport(handler),
    header_timeout=header_timeout,
    chunk_timeout=chunk_timeout,
  )


async def test_succesful_stream_preserves_text_and_final_usage() -> None:
  def handler(request: httpx.Request) -> httpx.Response:
    return httpx.Response(
      200,
      headers={"content-type": "text/event-stream"},
      content=SSE_BODY,
    )

  client = _client(handler)

  events = [event async for event in client.stream(LLMRequest(model="m"))]

  assert "".join(event.text for event in events if isinstance(event, TextDelta)) == "Hi"

  finishes = [event for event in events if isinstance(event, Finish)]
  assert len(finishes) == 1
  assert finishes[0].reason == "stop"
  assert finishes[0].usage.input_tokens == 5
  assert finishes[0].usage.output_tokens == 2
  assert events[-1] == finishes[0]


async def test_request_is_lowered_to_openrouter_format() -> None:
  captured: list[httpx.Request] = []

  def handler(request: httpx.Request) -> httpx.Response:
    captured.append(request)
    return httpx.Response(200, content=SSE_BODY)

  request = LLMRequest(
    model="test-model",
    system=["be helpful"],
    messages=[{"role": "user", "content": "hi"}],
    tools=[
      ToolDefinition(
        name="read",
        description="Read a file",
        parameters={"type": "object"},
      ),
    ],
    temperature=0.2,
    max_tokens=1000,
    reasoning_effort="high",
  )

  _ = [event async for event in _client(handler).stream(request)]

  assert len(captured) == 1
  sent = captured[0]
  assert sent.method == "POST"
  assert str(sent.url) == "https://openrouter.ai/api/v1/chat/completions"
  assert sent.headers["authorization"] == "Bearer sk-test"
  assert sent.headers["http-referer"] == "https://example.test"
  assert sent.headers["x-title"] == "test"
  assert sent.headers["content-type"] == "application/json"

  assert json.loads(sent.content) == {
    "model": "test-model",
    "messages": [
      {"role": "system", "content": "be helpful"},
      {"role": "user", "content": "hi"},
    ],
    "tools": [
      {
        "type": "function",
        "function": {
          "name": "read",
          "description": "Read a file",
          "parameters": {"type": "object"},
        },
      },
    ],
    "tool_choice": "auto",
    "temperature": 0.2,
    "max_tokens": 1000,
    "reasoning": {"effort": "high"},
    "stream": True,
    "usage": {"include": True},
  }


@pytest.mark.parametrize(
  ("status", "retryable"),
  [
    (400, False),
    (401, False),
    (429, True),
    (503, True),
  ],
)
async def test_http_failure_becomes_provider_error(
  status: int,
  retryable: bool,
) -> None:
  def handler(request: httpx.Request) -> httpx.Response:
    return httpx.Response(status, content=b"provider rejected request")

  events = [event async for event in _client(handler).stream(LLMRequest(model="m"))]

  assert len(events) == 1
  error = events[0]
  assert isinstance(error, ProviderError)
  assert error.status == status
  assert error.retryable is retryable
  assert "provider rejected request" in error.message


async def test_inline_error_terminates_the_stream() -> None:
  body = b'data: {"error":{"message":"upstream exploded","code":502}}\n\n' + SSE_BODY

  def handler(request: httpx.Request) -> httpx.Response:
    return httpx.Response(
      200,
      headers={"content-type": "text/event-stream"},
      content=body,
    )

  events = [event async for event in _client(handler).stream(LLMRequest(model="m"))]

  assert len(events) == 1
  error = events[0]
  assert isinstance(error, ProviderError)
  assert error.status == 502
  assert error.retryable is True
  assert "upstream exploded" in error.message


@pytest.mark.parametrize(
  "payload",
  [b"null", b"[]", b"42", b'"text"', b"true"],
)
async def test_non_object_payloads_are_skipped(payload: bytes) -> None:
  body = b"data: " + payload + b"\n\n" + SSE_BODY

  def handler(request: httpx.Request) -> httpx.Response:
    return httpx.Response(
      200,
      headers={"content-type": "text/event-stream"},
      content=body,
    )

  events = [event async for event in _client(handler).stream(LLMRequest(model="m"))]

  assert "".join(event.text for event in events if isinstance(event, TextDelta)) == "Hi"
  assert isinstance(events[-1], Finish)
  assert events[-1].reason == "stop"
  assert not any(isinstance(event, ProviderError) for event in events)


async def test_header_timeout_cancels_pending_request() -> None:
  never_respond = asyncio.Event()
  handler_cancelled = asyncio.Event()

  async def handler(request: httpx.Request) -> httpx.Response:
    try:
      await never_respond.wait()
    except asyncio.CancelledError:
      handler_cancelled.set()
      raise

    return httpx.Response(200, content=SSE_BODY)

  client = _client(handler, header_timeout=0.01)

  async with asyncio.timeout(1.0):
    events = [event async for event in client.stream(LLMRequest(model="m"))]

  assert len(events) == 1
  error = events[0]
  assert isinstance(error, ProviderError)
  assert error.status is None
  assert error.retryable is True
  assert "headers" in error.message.lower()
  assert handler_cancelled.is_set()
