"""childproto — 执行层协议的词汇表+编解码（边界层）。

父进程与 service 子进程之间内部协议（参考 service.py:28-34）的形状定义：
request/response/event 三种消息的 ADT 与（反）序列化。本模块只管形状，
不识命令语义——`cmd` 是过路字符串；命令语义的承诺住在管道两端
（父=壳的代理调用点，子=service._dispatch）。管道不是调用栈。
线格式复用 framing（同 Content-Length；读法同 asyncio StreamReader）。
"""
from typing import NamedTuple

#: 内部协议版本。版本校验行为未裁（挂钉，见 design-notes「砖1 契约」）。
VERSION = 1


class ChildRequest(NamedTuple):
    """父 → 子：一次内部请求。id=关联键（本模块不解释其语义）。"""
    id: int
    cmd: str
    params: dict
#end class


class ChildResponse(NamedTuple):
    """子 → 父：对某 id 请求的应答。ok=True 看 result，False 看 error。"""
    id: int | None
    ok: bool
    result: dict | None
    error: dict | None
#end class


class ChildEvent(NamedTuple):
    """子 → 父：无请求触发的通告（stopped/output/exited…，D3 事件源的内部形态）。"""
    event: str
    params: dict | None
#end class


class BadChildMsg(NamedTuple):
    """解析失败当值返回（错误前置，同 FrameBad 哲学）：不抛、不猜、不续。"""
    reason: str
    detail: str
#end class


def request_msg(req: ChildRequest) -> dict:
    """ADT → 线上 dict（字节化归 framing.encode_frame，此处只到 dict 层）。"""
    return {"type": "request", "id": req.id, "cmd": req.cmd, "params": req.params}
#end def


def parse_child_msg(msg: dict) -> "ChildResponse | ChildEvent | BadChildMsg":
    """线上 dict → ADT。type 分流：response/event；未知 → BadChildMsg。

    字段如实透传不假装（同 ParsedRequest 的敌意输入哲学：缺 id 就给 None）。
    """
    kind = msg.get("type") if isinstance(msg, dict) else None
    if kind == "response":
        return ChildResponse(id=msg.get("id"), ok=bool(msg.get("ok")),
                             result=msg.get("result"), error=msg.get("error"))
    #end if
    if kind == "event":
        ev = msg.get("event")
        return ChildEvent(event=ev if isinstance(ev, str) else repr(ev),
                          params=msg.get("params"))
    #end if
    return BadChildMsg(reason="bad_type", detail=f"unknown type {kind!r}")
#end def
