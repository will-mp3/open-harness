from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, TypeAdapter


class Usage(BaseModel):
  """Inclusive totals retain cache and reasoning tokens to avoid double-counting."""

  input_tokens: int = 0
  output_tokens: int = 0
  total_tokens: int = 0
  non_cached_input_tokens: int = 0
  cache_read_input_tokens: int = 0
  cache_write_input_tokens: int = 0
  reasoning_tokens: int = 0

  @property
  def breakdown_matches_input(self) -> bool:
    parts = (
      self.non_cached_input_tokens + self.cache_read_input_tokens + self.cache_write_input_tokens
    )
    return parts == self.input_tokens


class StepStart(BaseModel):
  type: Literal["step-start"] = "step-start"
  step: int


class TextStart(BaseModel):
  type: Literal["text-start"] = "text-start"
  id: str


class TextDelta(BaseModel):
  type: Literal["text-delta"] = "text-delta"
  id: str
  text: str


class TextEnd(BaseModel):
  type: Literal["text-end"] = "text-end"
  id: str


class ReasoningStart(BaseModel):
  type: Literal["reasoning-start"] = "reasoning-start"
  id: str


class ReasoningDelta(BaseModel):
  type: Literal["reasoning-delta"] = "reasoning-delta"
  id: str
  text: str


class ReasoningEnd(BaseModel):
  type: Literal["reasoning-end"] = "reasoning-end"
  id: str


class ToolInputStart(BaseModel):
  type: Literal["tool-input-start"] = "tool-input-start"
  id: str
  name: str


class ToolInputDelta(BaseModel):
  type: Literal["tool-input-delta"] = "tool-input-delta"
  id: str
  text: str


class ToolInputEnd(BaseModel):
  type: Literal["tool-input-end"] = "tool-input-end"
  id: str


class ToolCall(BaseModel):
  type: Literal["tool-call"] = "tool-call"
  id: str
  name: str
  input: dict[str, Any] = Field(default_factory=dict)


class ToolResultEvent(BaseModel):
  type: Literal["tool-result"] = "tool-result"
  id: str
  name: str
  output: str
  metadata: dict[str, Any] = Field(default_factory=dict)


class ToolErrorEvent(BaseModel):
  type: Literal["tool-error"] = "tool-error"
  id: str
  name: str
  message: str


class StepFinish(BaseModel):
  type: Literal["step-finish"] = "step-finish"
  step: int
  reason: str
  usage: Usage = Field(default_factory=Usage)


class Finish(BaseModel):
  type: Literal["finish"] = "finish"
  reason: str
  usage: Usage = Field(default_factory=Usage)


class ProviderError(BaseModel):
  type: Literal["provider-error"] = "provider-error"
  message: str
  status: int | None = None
  retryable: bool = False


LLMEvent = Annotated[
  StepStart
  | TextStart
  | TextDelta
  | TextEnd
  | ReasoningStart
  | ReasoningDelta
  | ReasoningEnd
  | ToolInputStart
  | ToolInputDelta
  | ToolInputEnd
  | ToolCall
  | ToolResultEvent
  | ToolErrorEvent
  | StepFinish
  | Finish
  | ProviderError,
  Field(discriminator="type"),
]

LLMEventAdapter: TypeAdapter[LLMEvent] = TypeAdapter(LLMEvent)
