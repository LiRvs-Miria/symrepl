"""Unit tests for the Content-Length frame codec.

Pure I/O over ``BytesIO`` — no processes, no sockets.  The framing
layer must be trustworthy on its own because ADR-0003 decision 2 makes
malformed frames fatal: splitting, coalescing, encoded-byte lengths,
and every failure mode are pinned here.
"""

from __future__ import annotations

import io
import json

import pytest

from symrepl.dap.framing import (
    FrameReader,
    FramingError,
    encode_frame,
)


def _reader(data: bytes, max_read: int | None = None) -> FrameReader:
    bio = io.BytesIO(data)
    if max_read is None:
        return FrameReader(bio.read)
    return FrameReader(lambda n: bio.read(min(n, max_read)))


def test_round_trip_counts_encoded_bytes():
    payload = {"type": "event", "params": {"text": "分支追踪→停止"}}
    frame = encode_frame(payload)
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    header, _, rest = frame.partition(b"\r\n\r\n")
    assert rest == body
    declared = int(header.removeprefix(b"Content-Length: "))
    assert declared == len(body)  # encoded bytes, not character count
    assert _reader(frame).read_frame() == payload


def test_two_frames_in_one_read():
    first = encode_frame({"seq": 1})
    second = encode_frame({"seq": 2, "text": "ноль"})
    reader = _reader(first + second)
    assert reader.read_frame() == {"seq": 1}
    assert reader.read_frame() == {"seq": 2, "text": "ноль"}


def test_byte_by_byte_feed():
    frame = encode_frame({"type": "hello", "version": 1})
    assert _reader(frame, max_read=1).read_frame() == {
        "type": "hello",
        "version": 1,
    }


def test_extra_headers_tolerated_and_name_case_insensitive():
    body = b'{"a": 1}'
    raw = (
        b"Content-Type: application/vscode-jsonrpc; charset=utf-8\r\n"
        b"content-length: 8\r\n"
        b"\r\n" + body
    )
    assert _reader(raw).read_frame() == {"a": 1}


def test_header_value_whitespace_tolerated():
    assert _reader(b"Content-Length:   2  \r\n\r\n{}").read_frame() == {}


def test_missing_content_length():
    with pytest.raises(FramingError):
        _reader(b"Content-Type: text\r\n\r\n{}").read_frame()


def test_non_numeric_and_negative_lengths():
    with pytest.raises(FramingError):
        _reader(b"Content-Length: 8x\r\n\r\n{}").read_frame()
    with pytest.raises(FramingError):
        _reader(b"Content-Length: -5\r\n\r\n{}").read_frame()
    with pytest.raises(FramingError):
        _reader(b"Content-Length: \r\n\r\n{}").read_frame()


def test_duplicate_content_length():
    raw = b"Content-Length: 2\r\nContent-Length: 2\r\n\r\n{}"
    with pytest.raises(FramingError):
        _reader(raw).read_frame()


def test_declared_length_over_cap(monkeypatch):
    monkeypatch.setattr("symrepl.dap.framing.MAX_FRAME_BYTES", 8)
    with pytest.raises(FramingError):
        _reader(b"Content-Length: 9\r\n\r\n123456789").read_frame()


def test_encode_over_cap(monkeypatch):
    monkeypatch.setattr("symrepl.dap.framing.MAX_FRAME_BYTES", 4)
    with pytest.raises(FramingError):
        encode_frame({"x": "yyyyy"})


def test_truncated_body_then_eof():
    frame = encode_frame({"k": "v"})
    with pytest.raises(FramingError):
        _reader(frame[:-2]).read_frame()


def test_clean_eof_between_frames():
    with pytest.raises(EOFError):
        _reader(b"").read_frame()
    reader = _reader(encode_frame({"a": 1}))
    reader.read_frame()
    with pytest.raises(EOFError):
        reader.read_frame()


def test_body_not_utf8():
    with pytest.raises(FramingError):
        _reader(b"Content-Length: 2\r\n\r\n\xff\xfe").read_frame()


def test_body_not_json():
    with pytest.raises(FramingError):
        _reader(b"Content-Length: 4\r\n\r\nnope").read_frame()


def test_body_not_an_object():
    with pytest.raises(FramingError):
        _reader(b"Content-Length: 6\r\n\r\n[1, 2]").read_frame()


def test_header_line_without_colon():
    with pytest.raises(FramingError):
        _reader(b"Content-Length 4\r\n\r\n{}").read_frame()


def test_runaway_header_section(monkeypatch):
    monkeypatch.setattr("symrepl.dap.framing.MAX_HEADER_BYTES", 16)
    with pytest.raises(FramingError):
        _reader(b"X: " + b"y" * 64).read_frame()
