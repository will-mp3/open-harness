from __future__ import annotations

import time
from pathlib import Path

from pydantic import BaseModel, Field

from open_harness.schema.message import AssistantMessage, Message, new_id


def find_project_root(cwd: Path) -> Path:
  """Find the nearest .git marker, falling back to the resolved cwd."""
  current = cwd.resolve()

  for candidate in (current, *current.parents):
    if (candidate / ".git").exists():
      return candidate

  return current


class Session(BaseModel):
  id: str = Field(default_factory=lambda: new_id("ses"))
  cwd: Path
  project_root: Path
  model: str
  time_created: float = Field(default_factory=time.time)
  messages: list[Message] = Field(default_factory=list)

  @classmethod
  def create(cls, *, cwd: Path, project_root: Path, model: str) -> Session:
    return cls(cwd=cwd, project_root=project_root, model=model)

  def append(self, message: Message) -> Message:
    self.messages.append(message)
    return message

  def last_assistant(self) -> Message | None:
    for message in reversed(self.messages):
      if isinstance(message.info, AssistantMessage):
        return message
    return None
