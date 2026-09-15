from __future__ import annotations

from pathlib import Path

from open_harness.schema.message import (
  AssistantMessage,
  Message,
  Part,
  TextPart,
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
