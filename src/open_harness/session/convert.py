from __future__ import annotations

import json
from typing import Any

from open_harness.schema.message import ToolStateCompleted, UserMessage
from open_harness.schema.request import ModelMessage
from open_harness.schema.session import Session


def to_model_messages(session: Session) -> list[ModelMessage]:
  messages: list[ModelMessage] = []

  for message in session.messages:
    text = message.joined_text()

    if isinstance(message.info, UserMessage):
      messages.append({"role": "user", "content": text})
      continue

    tool_calls: list[dict[str, Any]] = []
    tool_results: list[ModelMessage] = []

    for part in message.tool_parts():
      state = part.state
      if isinstance(state, ToolStateCompleted):
        tool_calls.append(
          {
            "id": part.call_id,
            "type": "function",
            "function": {"name": part.tool, "arguments": json.dumps(state.input)},
          }
        )
        tool_results.append(
          {
            "role": "tool",
            "tool_call_id": part.call_id,
            "content": state.output,
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
