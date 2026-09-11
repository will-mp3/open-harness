from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Protocol

from open_harness.schema.events import LLMEvent
from open_harness.schema.request import LLMRequest


class LLMClient(Protocol):
  """A client that streams provider-neutral events for a request"""

  def stream(self, request: LLMRequest) -> AsyncIterator[LLMEvent]: ...
