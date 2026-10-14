"""framing — Content-Length 分帧编解码（结构件，无状态）。

帧格式：b"Content-Length: N\\r\\n\\r\\n" + N 字节 JSON body。
read_frame 返回 dict；对端体面关闭（FIN）返回 None；坏帧抛 FrameError。
半帧/坏 Content-Length 的混沌语义（chaos 任务 B）待阶段 3 对账，当前一律 FrameError。
"""
import asyncio
import json


class FrameError(Exception):
    pass
#end class


def encode_frame(msg: dict) -> bytes:
    body = json.dumps(msg, ensure_ascii=False).encode("utf-8")
    return b"Content-Length: %d\r\n\r\n" % len(body) + body
#end def


async def read_frame(reader: asyncio.StreamReader):
    try:
        head = await reader.readuntil(b"\r\n\r\n")
    except asyncio.IncompleteReadError as e:
        if not e.partial:
            return None                      # 对端体面走人（FIN）
        raise FrameError("client died mid-header")
    #end try
    try:
        headers = dict(l.split(b":", 1) for l in head.split(b"\r\n") if b":" in l)
        length = int(headers[b"Content-Length"])
        body = await reader.readexactly(length)
    except (KeyError, ValueError) as e:
        raise FrameError(f"bad header: {e!r}")
    except asyncio.IncompleteReadError:
        raise FrameError("client died mid-body")
    #end try
    try:
        return json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise FrameError(f"bad body: {e!r}")
    #end try
#end def
