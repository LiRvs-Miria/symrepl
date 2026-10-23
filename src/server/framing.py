"""framing — Content-Length 分帧编解码（结构件，无状态）。

帧格式：b"Content-Length: N\\r\\n\\r\\n" + N 字节 JSON body。
read_frame 返回**值 ADT**（契约见 design-notes「framing 重构设计档案」）：
FrameOk(payload) / FrameEof（对端体面告别 FIN）/ FrameBad(reason, detail)。
错误前置三闸：声明长度 64MiB 上限（声明即拒，洞1）、Content-Length 纯数字
闸（洞2）、头长限制收编 LimitOverrunError（洞3）。
坏帧=会话终局（no-resync，对齐参考 decision 2）：壳见 FrameBad 即 ABRUPT，
无恢复路径。
"""
import asyncio
import json
from enum import Enum, auto
from typing import NamedTuple

MAX_FRAME_BYTES = 64 * 1024 * 1024   # 声明长度上限（对齐参考 decision 2 的闸）
_HEADER_TERMINATOR = b"\r\n\r\n"

class FrameBadReason(Enum):
    LENGTH_OVERSIZE     = auto()    # 洞1：声明 > 64MiB，声明即拒
    LENGTH_FORMAT       = auto()    # 洞2：CL 缺失/非纯数字（isdigit 闸）
    HEADER_LIMIT        = auto()    # 洞3：readuntil 撞 LimitOverrunError，收编
    STREAM_TRUNCATED    = auto()    # 半帧：头中/体中断流
    BAD_BODY            = auto()    # body 非 UTF-8/JSON/object
#end class

class FrameOk(NamedTuple):
    payload: dict
#end class

class FrameEof(NamedTuple):
    pass
#end class

class FrameBad(NamedTuple):
    reason: FrameBadReason
    detail: str
#end class

FrameOut = FrameOk | FrameEof | FrameBad

async def read_frame(reader: asyncio.StreamReader) -> FrameOut:
    # 头：头长未知，读到分隔符为止；撞 reader 流限制=坏头超限（洞3 收编）
    try:
        head = await reader.readuntil(_HEADER_TERMINATOR)
    except asyncio.IncompleteReadError as e:
        if not e.partial:
            return FrameEof()                       # 对端体面走人（FIN）
        return FrameBad(FrameBadReason.STREAM_TRUNCATED, "peer died mid-header")
    #end try
    except asyncio.LimitOverrunError:
        return FrameBad(FrameBadReason.HEADER_LIMIT, "header over reader limit without terminator")
    #end try

    # 长度：纯数字闸（int() 会吞 +12/1_2——洞2；strip 先剥冒号后的空格，否则正常帧全被拒）
    headers = dict(l.split(b":", 1) for l in head.split(b"\r\n") if b":" in l)
    raw = headers.get(b"Content-Length")
    if raw is None or not raw.strip().isdigit():
        return FrameBad(FrameBadReason.LENGTH_FORMAT, f"bad Content-Length: {raw!r}")
    #end if
    length = int(raw)
    if length > MAX_FRAME_BYTES:
        return FrameBad(FrameBadReason.LENGTH_OVERSIZE,
                        f"declared {length} bytes, over the {MAX_FRAME_BYTES}-byte cap")
    #end if

    # 体：长度已过闸，readexactly 安全
    try:
        body = await reader.readexactly(length)
    except asyncio.IncompleteReadError:
        return FrameBad(FrameBadReason.STREAM_TRUNCATED, "peer died mid-body")
    #end try

    # 解码：UTF-8 → JSON → 必须是 object
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        return FrameBad(FrameBadReason.BAD_BODY, f"not valid UTF-8/JSON: {e!r}")
    #end try
    if not isinstance(payload, dict):
        return FrameBad(FrameBadReason.BAD_BODY,
                        f"body must be a JSON object, got {type(payload).__name__}")
    #end if
    return FrameOk(payload=payload)
#end def


def encode_frame(msg: dict) -> bytes:
    body = json.dumps(msg, ensure_ascii=False).encode("utf-8")
    return b"Content-Length: %d\r\n\r\n" % len(body) + body
#end def
