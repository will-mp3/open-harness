from __future__ import annotations

from typing import Any

import pytest

from open_harness.llm.translate import ChunkTranslator
from open_harness.schema.events import (
  Finish,
  ReasoningDelta,
  ReasoningEnd,
  ReasoningStart,
  StepFinish,
  StepStart,
  TextDelta,
  TextEnd,
  TextStart,
  ToolCall,
  ToolInputDelta,
  ToolInputEnd,
  ToolInputStart,
  Usage,
)


def test_text_stream_defers_terminal_events_until_usage_arrives() -> None:
  translator = ChunkTranslator(step=0)

  assert translator.translate(
    {"choices": [{"delta": {"content": "Hel"}, "finish_reason": None}]}
  ) == [
    StepStart(step=0),
    TextStart(id="text-0"),
    TextDelta(id="text-0", text="Hel"),
  ]

  assert translator.translate(
    {"choices": [{"delta": {"content": "lo"}, "finish_reason": None}]}
  ) == [TextDelta(id="text-0", text="lo")]

  assert translator.translate({"choices": [{"delta": {}, "finish_reason": "stop"}]}) == [
    TextEnd(id="text-0")
  ]

  usage = Usage(
    input_tokens=100,
    output_tokens=20,
    total_tokens=120,
    non_cached_input_tokens=70,
    cache_read_input_tokens=30,
  )

  assert translator.translate(
    {
      "choices": [],
      "usage": {
        "prompt_tokens": 100,
        "completion_tokens": 20,
        "total_tokens": 120,
        "prompt_tokens_details": {"cached_tokens": 30},
      },
    }
  ) == [StepFinish(step=0, reason="stop", usage=usage), Finish(reason="stop", usage=usage)]

  assert translator.finish() == []


def test_reasoning_fields_share_one_event_lifecycle() -> None:
  translator = ChunkTranslator(step=1)

  assert translator.translate(
    {
      "choices": [
        {
          "delta": {"reasoning": "Let me "},
          "finish_reason": None,
        }
      ]
    }
  ) == [
    StepStart(step=1),
    ReasoningStart(id="reasoning-1"),
    ReasoningDelta(id="reasoning-1", text="Let me "),
  ]

  assert translator.translate(
    {
      "choices": [
        {
          "delta": {"reasoning_content": "check."},
          "finish_reason": None,
        }
      ]
    }
  ) == [ReasoningDelta(id="reasoning-1", text="check.")]

  assert translator.translate({"choices": [{"delta": {}, "finish_reason": "stop"}]}) == [
    ReasoningEnd(id="reasoning-1")
  ]

  assert translator.finish() == [
    StepFinish(step=1, reason="stop", usage=Usage()),
    Finish(reason="stop", usage=Usage()),
  ]

  assert translator.finish() == []


def _tool_chunk(fragment: dict[str, Any]) -> dict[str, Any]:
  return {
    "choices": [
      {
        "delta": {"tool_calls": [fragment]},
        "finish_reason": None,
      }
    ]
  }


def test_tool_call_is_assembled_from_argument_fragments() -> None:
  translator = ChunkTranslator()

  assert translator.translate(
    _tool_chunk(
      {
        "index": 0,
        "id": "call-1",
        "function": {"name": "read", "arguments": ""},
      }
    )
  ) == [
    StepStart(step=0),
    ToolInputStart(id="call-1", name="read"),
  ]

  for fragment in ('{"file_', 'path":"a.py"}'):
    assert translator.translate(
      _tool_chunk(
        {
          "index": 0,
          "function": {"arguments": fragment},
        }
      )
    ) == [ToolInputDelta(id="call-1", text=fragment)]

  assert translator.translate({"choices": [{"delta": {}, "finish_reason": "tool_calls"}]}) == [
    ToolInputEnd(id="call-1"),
    ToolCall(
      id="call-1",
      name="read",
      input={"file_path": "a.py"},
    ),
  ]

  assert translator.finish() == [
    StepFinish(step=0, reason="tool_calls", usage=Usage()),
    Finish(reason="tool_calls", usage=Usage()),
  ]

  assert translator.finish() == []


def test_interleaved_tool_calls_keep_their_arguments_separate() -> None:
  translator = ChunkTranslator()

  assert translator.translate(
    _tool_chunk(
      {
        "index": 0,
        "id": "call-read",
        "function": {"name": "read", "arguments": '{"file_path":'},
      }
    )
  ) == [
    StepStart(step=0),
    ToolInputStart(id="call-read", name="read"),
    ToolInputDelta(id="call-read", text='{"file_path":'),
  ]

  assert translator.translate(
    _tool_chunk(
      {
        "index": 1,
        "id": "call-ls",
        "function": {"name": "ls", "arguments": '{"path":'},
      }
    )
  ) == [
    ToolInputStart(id="call-ls", name="ls"),
    ToolInputDelta(id="call-ls", text='{"path":'),
  ]

  for index, call_id, fragment in [
    (1, "call-ls", '"."}'),
    (0, "call-read", '"a.py"}'),
  ]:
    assert translator.translate(
      _tool_chunk(
        {
          "index": index,
          "function": {"arguments": fragment},
        }
      )
    ) == [ToolInputDelta(id=call_id, text=fragment)]

  assert translator.translate({"choices": [{"delta": {}, "finish_reason": "tool_calls"}]}) == [
    ToolInputEnd(id="call-read"),
    ToolCall(
      id="call-read",
      name="read",
      input={"file_path": "a.py"},
    ),
    ToolInputEnd(id="call-ls"),
    ToolCall(
      id="call-ls",
      name="ls",
      input={"path": "."},
    ),
  ]

  assert translator.finish() == [
    StepFinish(step=0, reason="tool_calls", usage=Usage()),
    Finish(reason="tool_calls", usage=Usage()),
  ]

  assert translator.finish() == []


def test_tool_call_closes_open_text_before_tool_input_starts() -> None:
  translator = ChunkTranslator()

  assert translator.translate(
    {
      "choices": [
        {
          "delta": {"content": "I'll read the file."},
          "finish_reason": None,
        }
      ]
    }
  ) == [
    StepStart(step=0),
    TextStart(id="text-0"),
    TextDelta(id="text-0", text="I'll read the file."),
  ]

  assert translator.translate(
    _tool_chunk(
      {
        "index": 0,
        "id": "call-read",
        "function": {
          "name": "read",
          "arguments": '{"file_path":"a.py"}',
        },
      }
    )
  ) == [
    TextEnd(id="text-0"),
    ToolInputStart(id="call-read", name="read"),
    ToolInputDelta(id="call-read", text='{"file_path":"a.py"}'),
  ]

  assert translator.translate({"choices": [{"delta": {}, "finish_reason": "tool_calls"}]}) == [
    ToolInputEnd(id="call-read"),
    ToolCall(
      id="call-read",
      name="read",
      input={"file_path": "a.py"},
    ),
  ]

  assert translator.finish() == [
    StepFinish(step=0, reason="tool_calls", usage=Usage()),
    Finish(reason="tool_calls", usage=Usage()),
  ]


@pytest.mark.parametrize(
  "raw_arguments",
  [
    pytest.param("", id="empty"),
    pytest.param("   ", id="whitespace"),
    pytest.param("{not json", id="malformed-json"),
    pytest.param("[]", id="array"),
    pytest.param("null", id="null"),
    pytest.param("42", id="number"),
  ],
)
def test_invalid_tool_arguments_produce_empty_input(raw_arguments: str) -> None:
  translator = ChunkTranslator()

  translator.translate(
    _tool_chunk(
      {
        "index": 0,
        "id": "call-read",
        "function": {
          "name": "read",
          "arguments": raw_arguments,
        },
      }
    )
  )

  assert translator.translate({"choices": [{"delta": {}, "finish_reason": "tool_calls"}]}) == [
    ToolInputEnd(id="call-read"),
    ToolCall(id="call-read", name="read", input={}),
  ]


def test_finish_without_any_chunks_emits_nothing() -> None:
  translator = ChunkTranslator()

  assert translator.finish() == []


@pytest.mark.parametrize(
  ("field", "end_event"),
  [
    pytest.param("content", TextEnd(id="text-0"), id="text"),
    pytest.param("reasoning", ReasoningEnd(id="reasoning-0"), id="reasoning"),
  ],
)
def test_finish_closes_open_content(
  field: str,
  end_event: TextEnd | ReasoningEnd,
) -> None:
  translator = ChunkTranslator()

  translator.translate(
    {
      "choices": [
        {
          "delta": {field: "Partial response"},
          "finish_reason": None,
        }
      ]
    }
  )

  assert translator.finish() == [
    end_event,
    StepFinish(step=0, reason="stop", usage=Usage()),
    Finish(reason="stop", usage=Usage()),
  ]

  assert translator.finish() == []


def test_finish_finalizes_an_open_tool_call() -> None:
  translator = ChunkTranslator()

  translator.translate(
    _tool_chunk(
      {
        "index": 0,
        "id": "call-read",
        "function": {
          "name": "read",
          "arguments": '{"file_path":"a.py"}',
        },
      }
    )
  )

  assert translator.finish() == [
    ToolInputEnd(id="call-read"),
    ToolCall(
      id="call-read",
      name="read",
      input={"file_path": "a.py"},
    ),
    StepFinish(step=0, reason="stop", usage=Usage()),
    Finish(reason="stop", usage=Usage()),
  ]

  assert translator.finish() == []
