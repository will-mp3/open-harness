from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, cast

from open_harness.schema.events import (
  Finish,
  LLMEvent,
  ReasoningDelta,
  ReasoningEnd,
  ReasoningStart,
  StepFinish,
  StepStart,
  TextDelta,
  TextEnd,
  TextStart,
  ToolCall,
  ToolInputDelta,
  ToolInputEnd,
  ToolInputStart,
  Usage,
)


@dataclass
class _ToolAccumulator:
  call_id: str = ""
  name: str = ""
  arguments: str = ""
  announced: bool = False
  closed: bool = False


def _parse_arguments(raw: str) -> dict[str, Any]:
  text = raw.strip()
  if not text:
    return {}

  try:
    parsed: Any = json.loads(text)
  except json.JSONDecodeError:
    # Tool argument validation owns the recovery path for malformed input
    return {}

  if not isinstance(parsed, dict):
    return {}

  return cast(dict[str, Any], parsed)


def parse_usage(raw: dict[str, Any] | None) -> Usage:
  data = raw or {}
  prompt = int(data.get("prompt_tokens") or 0)
  completion = int(data.get("completion_tokens") or 0)
  prompt_details: dict[str, Any] = data.get("prompt_tokens_details") or {}
  completion_details: dict[str, Any] = data.get("completion_tokens_details") or {}
  cache_read = int(prompt_details.get("cached_tokens") or 0)
  cache_write = int(data.get("cache_creation_input_tokens") or 0)

  return Usage(
    input_tokens=prompt,
    output_tokens=completion,
    total_tokens=int(data.get("total_tokens") or (prompt + completion)),
    non_cached_input_tokens=max(prompt - cache_read - cache_write, 0),
    cache_read_input_tokens=cache_read,
    cache_write_input_tokens=cache_write,
    reasoning_tokens=int(completion_details.get("reasoning_tokens") or 0),
  )


class ChunkTranslator:
  """Keep provider stream bookkeeping behind the LLMEvent contract."""

  def __init__(self, step: int = 0) -> None:
    self._step = step
    self._started = False
    self._text_open = False
    self._reasoning_open = False
    self._tools: dict[int, _ToolAccumulator] = {}
    self._finish_reason: str | None = None
    self._terminal_emitted = False

  @property
  def _text_id(self) -> str:
    return f"text-{self._step}"

  @property
  def _reasoning_id(self) -> str:
    return f"reasoning-{self._step}"

  def translate(self, chunk: dict[str, Any]) -> list[LLMEvent]:
    if self._terminal_emitted:
      return []

    events: list[LLMEvent] = []
    if not self._started:
      self._started = True
      events.append(StepStart(step=self._step))

    choices: list[dict[str, Any]] = chunk.get("choices") or []
    if choices:
      choice: dict[str, Any] = choices[0] or {}
      delta: dict[str, Any] = choice.get("delta") or {}
      events.extend(self._reasoning_events(delta))
      events.extend(self._text_events(delta))
      events.extend(self._tool_events(delta))

      reason = choice.get("finish_reason")
      if reason:
        self._finish_reason = str(reason)
        events.extend(self._close_open_blocks())

    # Usage can arrive after finish_reason, so terminal events must wait
    if chunk.get("usage") is not None and self._finish_reason is not None:
      events.extend(self._terminal_events(parse_usage(chunk.get("usage"))))

    return events

  def finish(self) -> list[LLMEvent]:
    if not self._started or self._terminal_emitted:
      return []

    events = self._close_open_blocks()
    events.extend(self._terminal_events(Usage()))
    return events

  def _text_events(self, delta: dict[str, Any]) -> list[LLMEvent]:
    content = delta.get("content")
    if not isinstance(content, str) or not content:
      return []

    events: list[LLMEvent] = []
    if not self._text_open:
      self._text_open = True
      events.append(TextStart(id=self._text_id))

    events.append(TextDelta(id=self._text_id, text=content))
    return events

  def _reasoning_events(self, delta: dict[str, Any]) -> list[LLMEvent]:
    content = delta.get("reasoning") or delta.get("reasoning_content")
    if not isinstance(content, str) or not content:
      return []

    events: list[LLMEvent] = []
    if not self._reasoning_open:
      self._reasoning_open = True
      events.append(ReasoningStart(id=self._reasoning_id))

    events.append(ReasoningDelta(id=self._reasoning_id, text=content))
    return events

  def _tool_events(self, delta: dict[str, Any]) -> list[LLMEvent]:
    events: list[LLMEvent] = []
    fragments: list[dict[str, Any]] = delta.get("tool_calls") or []

    for fragment in fragments:
      index = int(fragment.get("index") or 0)
      acc = self._tools.setdefault(index, _ToolAccumulator())

      fragment_id = fragment.get("id")
      if isinstance(fragment_id, str) and fragment_id:
        acc.call_id = fragment_id

      function: dict[str, Any] = fragment.get("function") or {}
      name = function.get("name")
      if isinstance(name, str) and name:
        acc.name = name

      if acc.name and not acc.announced:
        events.extend(self._close_text())
        acc.announced = True
        if not acc.call_id:
          acc.call_id = f"call-{self._step}-{index}"
        events.append(ToolInputStart(id=acc.call_id, name=acc.name))

      arguments = function.get("arguments")
      if isinstance(arguments, str) and arguments:
        acc.arguments += arguments
        if acc.announced:
          events.append(ToolInputDelta(id=acc.call_id, text=arguments))

    return events

  def _close_text(self) -> list[LLMEvent]:
    if not self._text_open:
      return []

    self._text_open = False
    return [TextEnd(id=self._text_id)]

  def _close_open_blocks(self) -> list[LLMEvent]:
    events = self._close_text()
    if self._reasoning_open:
      self._reasoning_open = False
      events.append(ReasoningEnd(id=self._reasoning_id))

    for acc in self._tools.values():
      if not acc.announced or acc.closed:
        continue

      acc.closed = True
      events.append(ToolInputEnd(id=acc.call_id))
      events.append(
        ToolCall(
          id=acc.call_id,
          name=acc.name,
          input=_parse_arguments(acc.arguments),
        )
      )

    return events

  def _terminal_events(self, usage: Usage) -> list[LLMEvent]:
    if self._terminal_emitted:
      return []

    self._terminal_emitted = True
    reason = self._finish_reason or "stop"
    return [
      StepFinish(step=self._step, reason=reason, usage=usage),
      Finish(reason=reason, usage=usage),
    ]
