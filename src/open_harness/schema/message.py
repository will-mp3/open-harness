from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field
from ulid import ULID


def new_id(prefix: str) -> str:
  return f"{prefix}_{ULID()}"


def _part_id() -> str:
  return new_id("prt")


class ToolStatePending(BaseModel):
  status: Literal["pending"] = "pending"
  raw: str = ""


class ToolStateRunning(BaseModel):
  status: Literal["running"] = "running"
  input: dict[str, Any] = Field(default_factory=dict)
  title: str = ""
  time_start: float = 0.0


class ToolStateCompleted(BaseModel):
  status: Literal["completed"] = "completed"
  input: dict[str, Any] = Field(default_factory=dict)
  output: str = ""
  title: str = ""
  metadata: dict[str, Any] = Field(default_factory=dict)
  time_start: float = 0.0
  time_end: float = 0.0


class ToolStateErrored(BaseModel):
  status: Literal["error"] = "error"
  input: dict[str, Any] = Field(default_factory=dict)
  error: str = ""
  metadata: dict[str, Any] = Field(default_factory=dict)
  time_start: float = 0.0
  time_end: float = 0.0


ToolState = Annotated[
  ToolStatePending | ToolStateRunning | ToolStateCompleted | ToolStateErrored,
  Field(discriminator="status"),
]


class TextPart(BaseModel):
  type: Literal["text"] = "text"
  id: str = Field(default_factory=_part_id)
  text: str = ""
  synthetic: bool = False


class ReasoningPart(BaseModel):
  type: Literal["reasoning"] = "reasoning"
  id: str = Field(default_factory=_part_id)
  text: str = ""


class ToolPart(BaseModel):
  type: Literal["tool"] = "tool"
  id: str = Field(default_factory=_part_id)
  call_id: str
  tool: str
  state: ToolState = Field(default_factory=ToolStatePending)


Part = Annotated[
  TextPart | ReasoningPart | ToolPart,
  Field(discriminator="type"),
]


class UserMessage(BaseModel):
  id: str = Field(default_factory=lambda: new_id("msg"))
  role: Literal["user"] = "user"
  time_created: float
  model: str


class AssistantMessage(BaseModel):
  id: str = Field(default_factory=lambda: new_id("msg"))
  role: Literal["assistant"] = "assistant"
  parent_id: str
  time_created: float
  model: str


MessageInfo = Annotated[
  UserMessage | AssistantMessage,
  Field(discriminator="role")
]


class Message(BaseModel):
  info: MessageInfo
  parts: list[Part] = Field(default_factory=list)

  def joined_text(self) -> str:
    return "\n".join(part.text for part in self.parts if isinstance(part, TextPart) and part.text)

  def tool_parts(self) -> list[ToolPart]:
    return [part for part in self.parts if isinstance(part, ToolPart)]
