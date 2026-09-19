from __future__ import annotations

from typing import Any

from open_harness.schema.request import ToolDefinition
from open_harness.tools.base import Tool

type JsonValue = str | int | float | bool | None | list[JsonValue] | dict[str, JsonValue]

_INT32_MIN = -(2**31)
_INT32_MAX = 2**31 - 1
_SCHEMA_MAP_KEYS = {"properties", "patternProperties", "$defs", "definitions"}


def _normalize(node: JsonValue, *, schema_map: bool = False) -> JsonValue:
  if isinstance(node, list):
    return [_normalize(item) for item in node]
  if not isinstance(node, dict):
    return node

  if schema_map:
    return {name: _normalize(schema) for name, schema in node.items()}

  stripped: dict[str, JsonValue] = {key: value for key, value in node.items() if key != "title"}

  all_of = stripped.get("allOf")
  if isinstance(all_of, list) and len(all_of) == 1 and isinstance(all_of[0], dict):
    merged = {**all_of[0], **{key: value for key, value in stripped.items() if key != "allOf"}}
    return _normalize(merged)

  any_of = stripped.get("anyOf")
  if isinstance(any_of, list):
    branches = [
      branch for branch in any_of if not (isinstance(branch, dict) and branch.get("type") == "null")
    ]
    if len(branches) == 1 and isinstance(branches[0], dict):
      merged = {
        **branches[0],
        **{key: value for key, value in stripped.items() if key != "anyOf"},
      }
      return _normalize(merged)
    if branches:
      stripped["anyOf"] = branches

  result: dict[str, JsonValue] = {
    key: _normalize(value, schema_map=key in _SCHEMA_MAP_KEYS) for key, value in stripped.items()
  }

  if result.get("type") == "object":
    result["additionalProperties"] = False

  if result.get("type") == "integer" and "minimum" not in result and "maximum" not in result:
    result["minimum"] = _INT32_MIN
    result["maximum"] = _INT32_MAX

  return result


def normalize_schema(schema: dict[str, Any]) -> dict[str, Any]:
  normalized = _normalize(schema)
  return normalized if isinstance(normalized, dict) else {}


class ToolRegistry:
  def __init__(self) -> None:
    self._tools: dict[str, Tool] = {}
    self._schemas: dict[str, dict[str, Any]] = {}

  def register(self, tool: Tool) -> None:
    self._tools[tool.id] = tool
    self._schemas.pop(tool.id, None)

  def get(self, tool_id: str) -> Tool | None:
    return self._tools.get(tool_id)

  def ids(self) -> list[str]:
    return sorted(self._tools)

  def definitions(self) -> list[ToolDefinition]:
    definitions: list[ToolDefinition] = []

    for tool_id in self.ids():
      tool = self._tools[tool_id]

      if tool_id not in self._schemas:
        self._schemas[tool_id] = normalize_schema(tool.params.model_json_schema())

      definitions.append(
        ToolDefinition(
          name=tool_id,
          description=tool.description,
          parameters=self._schemas[tool_id],
        )
      )

    return definitions
