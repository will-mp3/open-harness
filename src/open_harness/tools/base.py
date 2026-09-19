from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel

from open_harness.schema.message import Message


class PermissionDenied(Exception):
  """The permission gate rejected this call."""


class ToolFailure(Exception):
  """An expected tool failure that can be shown to the model."""


@dataclass
class ToolResult:
  title: str
  output: str
  metadata: dict[str, Any] = field(default_factory=dict[str, Any])


class MetadataFn(Protocol):
  async def __call__(
    self,
    *,
    title: str | None = None,
    metadata: dict[str, Any] | None = None,
  ) -> None: ...


class AskFn(Protocol):
  async def __call__(
    self,
    *,
    permission: str,
    patterns: list[str],
    metadata: dict[str, Any],
    always: list[str] | None = None,
  ) -> None: ...


@dataclass
class ToolContext:
  session_id: str
  message_id: str
  call_id: str
  cwd: Path
  project_root: Path
  messages: list[Message]
  metadata: MetadataFn
  ask: AskFn


class Tool(Protocol):
  id: str
  description: str

  @property
  def params(self) -> type[BaseModel]: ...

  async def execute(self, args: Any, ctx: ToolContext) -> ToolResult: ...
