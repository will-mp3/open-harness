from __future__ import annotations

from collections.abc import Iterator


class SSEParser:
    """Buffer bytes until a complete line preserves split UTF-8 characters."""

    def __init__(self) -> None:
        self._buffer = b""

    def feed(self, chunk: bytes) -> Iterator[str]:
        self._buffer += chunk

        while b"\n" in self._buffer:
            line, _, self._buffer = self._buffer.partition(b"\n")
            line = line.removesuffix(b"\r")

            if line.startswith(b"data:"):
                payload = line[len(b"data:") :].removeprefix(b" ")
                yield payload.decode("utf-8", errors="replace")
