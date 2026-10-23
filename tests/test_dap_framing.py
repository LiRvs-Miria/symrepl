"""Unit tests for the Content-Length frame codec (cleanroom framing).

The framing module lives in the cleanroom tree (``local-cleanroom/server``,
the promoted DAP face); these tests import it directly off that directory.
Value-ADT API: ``read_frame`` returns ``FrameOk``/``FrameEof``/``FrameBad``
instead of raising; malformed frames are fatal at the session layer
(no-resync), so every failure mode is pinned here as a value.  Pure I/O
over in-memory streams — no processes, no sockets, no LLDB.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src" / "server"))

from framing import (  # noqa: E402
    MAX_FRAME_BYTES,
    FrameBad,
    FrameBadReason,
    FrameEof,
    FrameOk,
    encode_frame,
    read_frame,
)


def _reader(
    data: bytes, *, chunk: int | None = None, limit: int = 2**16
) -> asyncio.StreamReader:
    reader = asyncio.StreamReader(limit=limit)
    if chunk is None:
        reader.feed_data(data)
    else:
        for i in range(0, len(data), chunk):
            reader.feed_data(data[i:i + chunk])
    reader.feed_eof()
    return reader


def _read(data: bytes, **kw):
    async def _one():
        # Reader must be constructed and consumed inside the running loop
        # (asyncio.run tears the loop down afterwards).
        return await read_frame(_reader(data, **kw))
    return asyncio.run(_one())


def _assert_bad(out, reason: FrameBadReason) -> None:
    assert isinstance(out, FrameBad) and out.reason is reason


def test_round_trip_counts_encoded_bytes():
    payload = {"type": "event", "params": {"text": "分支追踪→停止"}}
    frame = encode_frame(payload)
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    header, _, rest = frame.partition(b"\r\n\r\n")
    assert rest == body
    declared = int(header.removeprefix(b"Content-Length: "))
    assert declared == len(body)  # encoded bytes, not character count
    assert _read(frame) == FrameOk(payload=payload)


def test_two_frames_in_one_read():
    first = encode_frame({"seq": 1})
    second = encode_frame({"seq": 2, "text": "ноль"})

    async def _two():
        reader = _reader(first + second)
        one = await read_frame(reader)
        two = await read_frame(reader)
        return one, two

    a, b = asyncio.run(_two())
    assert a == FrameOk(payload={"seq": 1})
    assert b == FrameOk(payload={"seq": 2, "text": "ноль"})


def test_byte_by_byte_feed():
    frame = encode_frame({"type": "hello", "version": 1})
    assert _read(frame, chunk=1) == FrameOk(payload={"type": "hello", "version": 1})


def test_extra_headers_tolerated():
    body = b'{"a": 1}'
    raw = (
        b"Content-Type: application/vscode-jsonrpc; charset=utf-8\r\n"
        b"Content-Length: 8\r\n"
        b"\r\n" + body
    )
    assert _read(raw) == FrameOk(payload={"a": 1})


def test_header_value_whitespace_tolerated():
    assert _read(b"Content-Length:   2  \r\n\r\n{}") == FrameOk(payload={})


def test_header_line_without_colon_yields_missing_length():
    # Lines without a colon are skipped by the header parser; the
    # missing Content-Length is then caught by the format gate.
    _assert_bad(_read(b"Content-Length 4\r\n\r\n{}"), FrameBadReason.LENGTH_FORMAT)


def test_header_name_is_case_sensitive():
    # As implemented: the parser looks up b"Content-Length" exactly.
    _assert_bad(_read(b"content-length: 8\r\n\r\n{}"), FrameBadReason.LENGTH_FORMAT)


def test_missing_content_length():
    _assert_bad(_read(b"Content-Type: text\r\n\r\n{}"), FrameBadReason.LENGTH_FORMAT)


def test_non_numeric_and_negative_lengths():
    for raw in (
        b"Content-Length: 8x\r\n\r\n{}",
        b"Content-Length: -5\r\n\r\n{}",
        b"Content-Length: \r\n\r\n{}",
    ):
        _assert_bad(_read(raw), FrameBadReason.LENGTH_FORMAT)


def test_duplicate_content_length_last_wins():
    # As implemented: dict(...) keeps the last occurrence, no rejection.
    out = _read(b"Content-Length: 2\r\nContent-Length: 2\r\n\r\n{}")
    assert out == FrameOk(payload={})


def test_declared_length_over_cap(monkeypatch):
    monkeypatch.setattr("framing.MAX_FRAME_BYTES", 8)
    _assert_bad(_read(b"Content-Length: 9\r\n\r\n123456789"),
                FrameBadReason.LENGTH_OVERSIZE)


def test_max_frame_bytes_constant_is_64mib():
    assert MAX_FRAME_BYTES == 64 * 1024 * 1024


def test_truncated_body():
    frame = encode_frame({"k": "v"})
    _assert_bad(_read(frame[:-2]), FrameBadReason.STREAM_TRUNCATED)


def test_peer_died_mid_header():
    _assert_bad(_read(b"Content-Length: 4\r\nCont"), FrameBadReason.STREAM_TRUNCATED)


def test_clean_eof_between_frames():
    async def _run():
        reader = _reader(encode_frame({"a": 1}))
        one = await read_frame(reader)
        two = await read_frame(reader)
        return one, two

    a, b = asyncio.run(_run())
    assert a == FrameOk(payload={"a": 1})
    assert b == FrameEof()


def test_empty_stream_is_eof():
    assert _read(b"") == FrameEof()


def test_body_not_utf8():
    _assert_bad(_read(b"Content-Length: 2\r\n\r\n\xff\xfe"), FrameBadReason.BAD_BODY)


def test_body_not_json():
    _assert_bad(_read(b"Content-Length: 4\r\n\r\nnope"), FrameBadReason.BAD_BODY)


def test_body_not_an_object():
    _assert_bad(_read(b"Content-Length: 6\r\n\r\n[1, 2]"), FrameBadReason.BAD_BODY)


def test_runaway_header_section():
    _assert_bad(_read(b"X: " + b"y" * 64, limit=8), FrameBadReason.HEADER_LIMIT)
