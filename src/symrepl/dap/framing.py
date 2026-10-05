"""Content-Length frame codec — the one framing module shared by the
DAP server and the internal parent↔child protocol (ADR-0003
decisions 2 and 4).

Wire format: a ``Content-Length: <n>`` header line (optionally other
headers), a blank line, then exactly *n* body bytes, where *n* counts
**encoded** UTF-8 bytes.  The identical format serves DAP over stdio
or a socket and the internal protocol over the service child's
stdin/stdout.

A malformed or oversized frame is always fatal for the session
(ADR-0003 decision 2: no resync), so every structural problem raises
:class:`FramingError`; callers kill the session when they see it.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable

__all__ = [
    "MAX_FRAME_BYTES",
    "MAX_HEADER_BYTES",
    "FrameReader",
    "FramingError",
    "encode_frame",
]

#: Hard cap on one frame's body, counted in encoded bytes (ADR-0003
#: decision 2).
MAX_FRAME_BYTES = 64 * 1024 * 1024

#: Hard cap on the header section, bounding a header that never
#: terminates.
MAX_HEADER_BYTES = 1024 * 1024

_HEADER_TERMINATOR = b"\r\n\r\n"

_DECIMAL = re.compile(r"^[0-9]+$")


class FramingError(Exception):
    """A malformed or oversized frame — always fatal for the session."""


def encode_frame(payload: dict) -> bytes:
    """Serialize ``payload`` as one wire frame.

    The declared length counts **encoded** UTF-8 bytes, so non-ASCII
    text is safe.  Raises :class:`FramingError` when the encoded body
    exceeds :data:`MAX_FRAME_BYTES`.
    """
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    if len(body) > MAX_FRAME_BYTES:
        raise FramingError(
            f"frame body is {len(body)} bytes, over the "
            f"{MAX_FRAME_BYTES}-byte cap"
        )
    return f"Content-Length: {len(body)}\r\n\r\n".encode("ascii") + body


class FrameReader:
    """Blocking frame reader over a byte stream.

    ``read_exact`` is any callable taking a byte count and returning
    up to that many bytes — ``BufferedReader.read``/``read1``, raw
    sockets, ``os.read`` all fit.  Reads are sized so that no request
    can stall behind bytes the stream has already delivered (see
    :meth:`read_frame`): this matters because a buffered ``read(n)``
    blocks until *all* *n* bytes arrive, and frame lengths are not
    known up front.
    """

    def __init__(self, read_exact: Callable[[int], bytes]) -> None:
        self._read_exact = read_exact
        self._buffer = b""

    def read_frame(self) -> dict:
        """Return the next frame's decoded JSON object.

        Raises ``EOFError`` on a clean end of stream (between frames)
        and :class:`FramingError` for anything malformed or oversized.

        The header is consumed one byte at a time — its length is
        unknown, and asking a buffered ``read`` for a large fixed
        chunk would block until that chunk fills even though a whole
        frame may already be buffered.  The body is requested by its
        exact declared remaining length, which is safe for both
        buffered and raw readers.
        """
        header_end = self._buffer.find(_HEADER_TERMINATOR)
        while header_end < 0:
            if len(self._buffer) > MAX_HEADER_BYTES:
                raise FramingError(
                    f"header section exceeds {MAX_HEADER_BYTES} bytes "
                    "without terminating"
                )
            if not self._pull(1):
                if self._buffer:
                    raise FramingError(
                        f"stream ended mid-header with {len(self._buffer)} "
                        "buffered byte(s)"
                    )
                raise EOFError("clean end of stream before a frame")
            header_end = self._buffer.find(_HEADER_TERMINATOR)

        length = self._parse_length(self._buffer[:header_end])
        body_start = header_end + len(_HEADER_TERMINATOR)

        while len(self._buffer) - body_start < length:
            remaining = length - (len(self._buffer) - body_start)
            if not self._pull(remaining):
                raise FramingError(
                    f"stream ended mid-frame with {len(self._buffer)} "
                    "buffered byte(s)"
                )

        body = self._buffer[body_start : body_start + length]
        self._buffer = self._buffer[body_start + length :]
        return self._decode_body(body)

    def _pull(self, want: int) -> bytes:
        """One read of up to ``want`` bytes, appended to the buffer.

        Returns the chunk; ``b""`` means end of stream."""
        chunk = self._read_exact(want)
        self._buffer += chunk
        return chunk

    @staticmethod
    def _parse_length(header_blob: bytes) -> int:
        """Extract the (single, valid, within-cap) Content-Length from
        the header section."""
        # latin-1 never fails and preserves bytes; any non-ASCII header
        # cannot be a valid Content-Length line anyway.
        length: int | None = None
        for line in header_blob.decode("latin-1").split("\r\n"):
            if ":" not in line:
                raise FramingError(f"malformed header line: {line!r}")
            name, _, value = line.partition(":")
            if name.strip().lower() != "content-length":
                continue
            if length is not None:
                raise FramingError("duplicate Content-Length header")
            value = value.strip()
            if not _DECIMAL.match(value):
                raise FramingError(f"invalid Content-Length: {value!r}")
            length = int(value)
        if length is None:
            raise FramingError("frame is missing Content-Length")
        if length > MAX_FRAME_BYTES:
            raise FramingError(
                f"declared frame body of {length} bytes, over the "
                f"{MAX_FRAME_BYTES}-byte cap"
            )
        return length

    @staticmethod
    def _decode_body(body: bytes) -> dict:
        try:
            text = body.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise FramingError(f"frame body is not valid UTF-8: {exc}") from exc
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise FramingError(f"frame body is not valid JSON: {exc}") from exc
        if not isinstance(payload, dict):
            raise FramingError(
                f"frame body must be a JSON object, got "
                f"{type(payload).__name__}"
            )
        return payload
