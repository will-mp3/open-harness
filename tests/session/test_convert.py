from __future__ import annotations

import json
from pathlib import Path

import pytest

from open_harness.schema.message import (
  AssistantMessage,
  Message,
  Part,
  ReasoningPart,
  StepFinishPart,
  TextPart,
  ToolPart,
  ToolState,
  ToolStateCompleted,
  ToolStateErrored,
  ToolStatePending,
  ToolStateRunning,
  UserMessage,
)
from open_harness.schema.session import Session
from open_harness.session.convert import to_model_messages


def _session(tmp_path: Path) -> Session:
  return Session.create(cwd=tmp_path, project_root=tmp_path, model="m")


def _user(text: str) -> Message:
  return Message(
    info=UserMessage(time_created=0.0, model="m"),
    parts=[TextPart(text=text)],
  )


def _assistant(parts: list[Part]) -> Message:
  return Message(
    info=AssistantMessage(parent_id="msg_x", time_created=0.0, model="m"),
    parts=parts,
  )


def test_text_only_turn(tmp_path: Path) -> None:
  session = _session(tmp_path)
  session.append(_user("hi"))
  session.append(_assistant([TextPart(text="hello")]))

  assert to_model_messages(session) == [
    {"role": "user", "content": "hi"},
    {"role": "assistant", "content": "hello"},
  ]


def test_one_tool_call_emits_assistant_then_tool_message(tmp_path: Path) -> None:
  session = _session(tmp_path)
  session.append(_user("read it"))
  session.append(
    _assistant(
      [
        ToolPart(
          call_id="call_1",
          tool="read",
          state=ToolStateCompleted(
            input={"file_path": "a.py"},
            output="contents",
          ),
        ),
      ]
    )
  )

  messages = to_model_messages(session)

  assert len(messages) == 3
  assert messages[0] == {"role": "user", "content": "read it"}

  assistant = messages[1]
  assert assistant["role"] == "assistant"
  assert assistant["content"] is None
  assert len(assistant["tool_calls"]) == 1

  call = assistant["tool_calls"][0]
  assert call["id"] == "call_1"
  assert call["type"] == "function"
  assert call["function"]["name"] == "read"
  assert json.loads(call["function"]["arguments"]) == {"file_path": "a.py"}

  assert messages[2] == {
    "role": "tool",
    "tool_call_id": "call_1",
    "content": "contents",
  }


@pytest.mark.parametrize(
  ("state", "expected_input", "expected_content"),
  [
    pytest.param(
      ToolStatePending(raw='{"file_path":'),
      {},
      "Tool call was interrupted and did not complete.",
      id="pending",
    ),
    pytest.param(
      ToolStateRunning(input={"file_path": "a.py"}),
      {"file_path": "a.py"},
      "Tool call was interrupted and did not complete.",
      id="running",
    ),
    pytest.param(
      ToolStateErrored(
        input={"file_path": "a.py"},
        error="File not found: a.py",
      ),
      {"file_path": "a.py"},
      "File not found: a.py",
      id="errored",
    ),
  ],
)
def test_noncompleted_tool_call_keeps_matching_result(
  tmp_path: Path,
  state: ToolState,
  expected_input: dict[str, str],
  expected_content: str,
) -> None:
  session = _session(tmp_path)
  session.append(_user("read it"))
  session.append(_assistant([ToolPart(call_id="call_1", tool="read", state=state)]))
  before = session.model_dump()

  messages = to_model_messages(session)

  assert len(messages) == 3
  assistant = messages[1]
  assert assistant["role"] == "assistant"
  assert assistant["content"] is None
  assert len(assistant["tool_calls"]) == 1

  call = assistant["tool_calls"][0]
  assert call["id"] == "call_1"
  assert call["type"] == "function"
  assert call["function"]["name"] == "read"
  assert json.loads(call["function"]["arguments"]) == expected_input

  assert messages[2] == {
    "role": "tool",
    "tool_call_id": "call_1",
    "content": expected_content,
  }
  assert session.model_dump() == before


@pytest.mark.parametrize(
  "parts",
  [
    pytest.param([], id="no-parts"),
    pytest.param([TextPart(text="")], id="empty-text"),
    pytest.param([ReasoningPart(text="Internal reasoning")], id="reasoning-only"),
    pytest.param([StepFinishPart()], id="step-finish-only"),
  ],
)
def test_assistant_without_replayable_content_is_omitted(
  tmp_path: Path,
  parts: list[Part],
) -> None:
  session = _session(tmp_path)
  session.append(_user("hi"))
  session.append(_assistant(parts))

  assert to_model_messages(session) == [
    {"role": "user", "content": "hi"},
  ]
