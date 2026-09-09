from __future__ import annotations

from open_harness.schema.events import LLMEventAdapter, ProviderError, TextDelta, ToolCall, Usage
from open_harness.schema.request import LLMRequest, ToolDefinition


def test_usage_breakdown_invariant_holds() -> None:
  usage = Usage(
    input_tokens=100,
    non_cached_input_tokens=60,
    cache_read_input_tokens=30,
    cache_write_input_tokens=10,
  )
  assert usage.breakdown_matches_input


def test_usage_breakdown_invariant_detects_mismatch() -> None:
  usage = Usage(input_tokens=100, non_cached_input_tokens=1)
  assert not usage.breakdown_matches_input


def test_union_discriminates_on_type_field() -> None:
  event = LLMEventAdapter.validate_python({"type": "text-delta", "id": "text-0", "text": "hello"})
  assert isinstance(event, TextDelta)
  assert event.text == "hello"


def test_tool_carries_parsed_input() -> None:
  event = LLMEventAdapter.validate_python(
    {"type": "tool-call", "id": "call_1", "name": "read", "input": {"file_path": "a.py"}}
  )
  assert isinstance(event, ToolCall)
  assert event.input == {"file_path": "a.py"}


def test_provider_error_round_trips() -> None:
  original = ProviderError(message="boom", status=500, retryable=True)
  restored = LLMEventAdapter.validate_python(original.model_dump())
  assert restored == original


def test_request_defaults() -> None:
  request = LLMRequest(model="anthropic/claude-sonnet-5")
  assert request.system == []
  assert request.messages == []
  assert request.tools == []
  assert request.tool_choice == "auto"


def test_tool_definition_holds_a_json_schema() -> None:
  definition = ToolDefinition(
    name="read",
    description="Read a file",
    parameters={"type": "object", "properties": {}, "additionalProperties": False},
  )
  assert definition.parameters["type"] == "object"


def test_tool_result_preserves_metadata() -> None:
  event = LLMEventAdapter.validate_python(
    {
      "type": "tool-result",
      "id": "call_1",
      "name": "read",
      "output": "contents",
      "metadata": {"truncated": True},
    }
  )

  assert event.model_dump()["metadata"] == {"truncated": True}
