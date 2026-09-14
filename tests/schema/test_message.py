from __future__ import annotations

from open_harness.schema.events import Usage
from open_harness.schema.message import (
  AssistantMessage,
  Message,
  ReasoningPart,
  StepFinishPart,
  TextPart,
  ToolPart,
  ToolStateCompleted,
  ToolStateErrored,
  ToolStatePending,
  ToolStateRunning,
  UserMessage,
  new_id,
)


def test_ids_are_prefixed_and_sort_chronologically() -> None:
  first = new_id("msg")
  second = new_id("msg")

  assert first.startswith("msg_")
  assert second.startswith("msg_")
  assert first < second


def test_tool_state_defaults_to_pending() -> None:
  part = ToolPart(call_id="call_1", tool="read")

  assert isinstance(part.state, ToolStatePending)
  assert part.state.raw == ""


def test_tool_state_restores_completed_from_status() -> None:
  part = ToolPart.model_validate(
    {
      "call_id": "call_1",
      "tool": "read",
      "state": {
        "status": "completed",
        "input": {"file_path": "README.md"},
        "output": "Project documentation",
        "title": "Read README.md",
      },
    }
  )

  assert isinstance(part.state, ToolStateCompleted)
  assert part.state.input == {"file_path": "README.md"}
  assert part.state.output == "Project documentation"


def test_tool_state_restores_runing_from_status() -> None:
  part = ToolPart.model_validate(
    {
      "call_id": "call_1",
      "tool": "read",
      "state": {
        "status": "running",
        "input": {"file_path": "README.md"},
        "title": "Read README.md",
        "time_start": 10.0,
      },
    }
  )

  assert isinstance(part.state, ToolStateRunning)
  assert part.state.input == {"file_path": "README.md"}
  assert part.state.title == "Read README.md"
  assert part.state.time_start == 10.0


def test_tool_state_restores_error_from_status() -> None:
  part = ToolPart.model_validate(
    {
      "call_id": "call_1",
      "tool": "read",
      "state": {
        "status": "error",
        "input": {"file_path": "missing.md"},
        "error": "File not found: missing.md",
        "metadata": {"file_path": "missing.md"},
        "time_start": 10.0,
        "time_end": 11.0,
      },
    }
  )

  assert isinstance(part.state, ToolStateErrored)
  assert part.state.input == {"file_path": "missing.md"}
  assert part.state.error == "File not found: missing.md"
  assert part.state.metadata == {"file_path": "missing.md"}
  assert part.state.time_start == 10.0
  assert part.state.time_end == 11.0


def test_joined_text_skips_empty_and_non_text_parts() -> None:
  message = Message(
    info=AssistantMessage(parent_id="msg_x", time_created=0.0, model="m"),
    parts=[
      TextPart(text="one"),
      ReasoningPart(text="internal reasoning"),
      TextPart(text=""),
      ToolPart(call_id="call_1", tool="read"),
      TextPart(text="two"),
    ],
  )

  assert message.joined_text() == "one\ntwo"


def test_tool_parts_filters_by_type_and_preserves_order() -> None:
  message = Message(
    info=AssistantMessage(parent_id="msg_x", time_created=0.0, model="m"),
    parts=[
      TextPart(text="hello"),
      ToolPart(call_id="call_1", tool="read"),
      ReasoningPart(text="internal reasoning"),
      ToolPart(call_id="call_2", tool="ls"),
    ],
  )

  assert [part.call_id for part in message.tool_parts()] == ["call_1", "call_2"]


def test_user_message_round_trip_preserves_header_and_parts() -> None:
  original = Message(
    info=UserMessage(time_created=10.0, model="m"),
    parts=[TextPart(text="Explain this project")],
  )

  restored = Message.model_validate_json(original.model_dump_json())

  assert isinstance(restored.info, UserMessage)
  assert isinstance(restored.parts[0], TextPart)
  assert restored.info.id == original.info.id
  assert restored.parts[0].id == original.parts[0].id
  assert restored == original
  assert restored.joined_text() == "Explain this project"

def test_step_finish_round_trip_preserves_usage_and_cost() -> None:
  original = Message(
    info=AssistantMessage(parent_id="msg_x", time_created=0.0, model="m"),
    parts=[
      TextPart(text="Done"),
      StepFinishPart(
        reason="stop",
        usage=Usage(input_tokens=10, output_tokens=5, total_tokens=15),
        cost=0.01,
      ),
    ],
  )

  restored = Message.model_validate_json(original.model_dump_json())
  finish = restored.parts[1]

  assert isinstance(finish, StepFinishPart)
  assert finish.reason == "stop"
  assert finish.usage.input_tokens == 10
  assert finish.usage.output_tokens == 5
  assert finish.usage.total_tokens == 15
  assert finish.cost == 0.01
  assert restored == original
  assert restored.joined_text() == "Done"