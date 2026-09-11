from __future__ import annotations

from collections.abc import AsyncIterator, Sequence

from open_harness.schema.events import LLMEvent
from open_harness.schema.request import LLMRequest


class FakeClient:
  """Replay scripted turns and record requests for session assertions"""

  def __init__(self, turns: Sequence[Sequence[LLMEvent]]) -> None:
    self._turns: list[list[LLMEvent]] = [list(turn) for turn in turns]
    self.requests: list[LLMRequest] = []

  async def stream(self, request: LLMRequest) -> AsyncIterator[LLMEvent]:
    self.requests.append(request)

    if not self._turns:
      raise AssertionError(
        f"FakeClient exhausted: no scripted turn for request {len(self.requests)}"
      )

    for event in self._turns.pop(0):
      yield event
