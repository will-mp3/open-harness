from __future__ import annotations

from open_harness.schema.message import (
  ToolPart,
  ToolStateCompleted,
  ToolStateErrored,
  ToolStatePending,
  ToolStateRunning,
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