from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from open_harness.tools.base import ToolContext, ToolResult
from open_harness.tools.registry import ToolRegistry, normalize_schema


class _Params(BaseModel):
  path: str = Field(description="A path")
  limit: int | None = None


class _Tool:
  id = "sample"
  description = "A sample tool"
  params = _Params

  async def execute(self, args: _Params, ctx: ToolContext) -> ToolResult:
    return ToolResult(title="sample", output=args.path)


def test_optional_field_loses_its_null_branch() -> None:
  schema = normalize_schema(_Params.model_json_schema())

  limit: dict[str, Any] = schema["properties"]["limit"]

  assert "anyOf" not in limit
  assert limit["type"] == "integer"


def test_objects_forbid_additional_properties() -> None:
  schema = normalize_schema(_Params.model_json_schema())

  assert schema["additionalProperties"] is False


def test_titles_are_stripped() -> None:
  schema = normalize_schema(_Params.model_json_schema())

  assert "title" not in schema
  assert "title" not in schema["properties"]["path"]


def test_unbounded_integers_are_clamped() -> None:
  schema = normalize_schema(_Params.model_json_schema())

  limit: dict[str, Any] = schema["properties"]["limit"]

  assert limit["minimum"] == -(2**31)
  assert limit["maximum"] == 2**31 - 1


def test_registry_produces_sorted_definitions() -> None:
  registry = ToolRegistry()
  registry.register(_Tool())

  earlier_tool = _Tool()
  earlier_tool.id = "alpha"
  registry.register(earlier_tool)

  definitions = registry.definitions()

  assert [definition.name for definition in definitions] == ["alpha", "sample"]
  assert definitions[1].description == "A sample tool"
  assert definitions[1].parameters["type"] == "object"


def test_registry_get_returns_none_for_unknown_ids() -> None:
  assert ToolRegistry().get("nope") is None
