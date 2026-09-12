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


class ToolStateCompleted(BaseModel):
  status: Literal["completed"] = "completed"
  input: dict[str, Any] = Field(default_factory=dict)
  output: str = ""
  title: str = ""
  metadata: dict[str, Any] = Field(default_factory=dict)
  time_start: float = 0.0
  time_end: float = 0.0


ToolState = Annotated[
  ToolStatePending | ToolStateCompleted,
  Field(discriminator="status"),
]


class ToolPart(BaseModel):
  type: Literal["tool"] = "tool"
  id: str = Field(default_factory=_part_id)
  call_id: str
  tool: str
  state: ToolState = Field(default_factory=ToolStatePending)
