from __future__ import annotations

from open_harness.llm.sse import SSEParser


def test_single_complete_frame() -> None:
  parser = SSEParser()
  assert list(parser.feed(b'data: {"a":1}\n\n')) == ['{"a":1}']


def test_frame_split_across_reads() -> None:
  parser = SSEParser()
  assert list(parser.feed(b'data: {"a"')) == []
  assert list(parser.feed(b":1}\n")) == ['{"a":1}']


def test_multiple_frames_in_one_read() -> None:
  parser = SSEParser()
  assert list(parser.feed(b"data: one\ndata: two\n")) == ["one", "two"]


def test_comments_and_blank_lines_are_ignored() -> None:
  parser = SSEParser()
  assert list(parser.feed(b": keep-alive\n\ndata: real\n")) == ["real"]


def test_crlf_line_endings_are_supported() -> None:
  parser = SSEParser()
  assert list(parser.feed(b"data: value\r\n")) == ["value"]


def test_done_sentinel_is_passed_through_unchanged() -> None:
  parser = SSEParser()
  assert list(parser.feed(b"data: [DONE]\n")) == ["[DONE]"]


def test_non_data_fields_are_ignored() -> None:
  parser = SSEParser()
  assert list(parser.feed(b"event: message\ndata: payload\n")) == ["payload"]


def test_utf8_character_split_across_reads() -> None:
  parser = SSEParser()
  assert list(parser.feed(b"data: caf\xc3")) == []
  assert list(parser.feed(b"\xa9\n")) == ["café"]
