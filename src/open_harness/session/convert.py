from __future__ import annotations

import json
from typing import Any

from open_harness.schema.message import (
  ToolPart,
  ToolStateCompleted,
  ToolStateErrored,
  ToolStateRunning,
  UserMessage,
)
from open_harness.schema.request import ModelMessage
from open_harness.schema.session import Session

INTERRUPTED_RESULT = "Tool call was interrupted and did not complete."


def _tool_input(part: ToolPart) -> dict[str, Any]:
  state = part.state
  if isinstance(state, ToolStateRunning | ToolStateCompleted | ToolStateErrored):
    return state.input
  return {}


def _tool_content(part: ToolPart) -> str:
  state = part.state
  if isinstance(state, ToolStateCompleted):
    return state.output
  if isinstance(state, ToolStateErrored):
    return state.error

  return INTERRUPTED_RESULT


def to_model_messages(session: Session) -> list[ModelMessage]:
  messages: list[ModelMessage] = []

  for message in session.messages:
    text = message.joined_text()

    if isinstance(message.info, UserMessage):
      if text:
        messages.append({"role": "user", "content": text})
      continue

    tool_parts = message.tool_parts()
    if not text and not tool_parts:
      continue

    tool_calls: list[dict[str, Any]] = []
    tool_results: list[ModelMessage] = []

    for part in tool_parts:
      tool_calls.append(
        {
          "id": part.call_id,
          "type": "function",
          "function": {
            "name": part.tool,
            "arguments": json.dumps(_tool_input(part)),
          },
        }
      )
      tool_results.append(
        {
          "role": "tool",
          "tool_call_id": part.call_id,
          "content": _tool_content(part),
        }
      )

    assistant: ModelMessage = {
      "role": "assistant",
      "content": text or None,
    }
    if tool_calls:
      assistant["tool_calls"] = tool_calls

    messages.append(assistant)
    messages.extend(tool_results)

  return messages
