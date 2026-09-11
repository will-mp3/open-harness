from __future__ import annotations

import pytest
from open_harness.llm.fake import FakeClient

from open_harness.schema.events import Finish, LLMEvent, TextDelta
from open_harness.schema.request import LLMRequest


async def test_replays_scripted_turns_in_order_and_records_requests() -> None:
  turn_one: list[LLMEvent] = [
    TextDelta(id="text-0", text="first"),
    Finish(reason="stop"),
  ]
  turn_two: list[LLMEvent] = [
    TextDelta(id="text-0", text="second"),
    Finish(reason="stop"),
  ]
  client = FakeClient([turn_one, turn_two])
  request_one = LLMRequest(model="a")
  request_two = LLMRequest(model="b")

  got_one = [event async for event in client.stream(request_one)]
  got_two = [event async for event in client.stream(request_two)]

  assert got_one == turn_one
  assert got_two == turn_two
  assert client.requests == [request_one, request_two]


async def test_exhaustion_fails_loudly() -> None:
  client = FakeClient([])

  with pytest.raises(AssertionError, match="exhausted"):
    _ = [event async for event in client.stream(LLMRequest(model="a"))]
