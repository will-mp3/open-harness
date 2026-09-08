from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

ModelMessage = dict[str, Any]


class ToolDefinition(BaseModel):
    """A tool as the model sees it; parameters is a normalized JSON Schema."""

    name: str
    description: str
    parameters: dict[str, Any]


class LLMRequest(BaseModel):
    """Provider-neutral request; the provider client handles wire formatting."""

    model: str
    system: list[str] = Field(default_factory=list)
    messages: list[ModelMessage] = Field(default_factory=list[ModelMessage])
    tools: list[ToolDefinition] = Field(default_factory=list[ToolDefinition])
    tool_choice: str = "auto"
    temperature: float | None = None
    max_tokens: int | None = None
    reasoning_effort: str | None = None
