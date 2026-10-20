#!/usr/bin/env python3
"""session.py — 会话子进程（端口面）：wire 协议+命令派发+后端插拔。

进程即会话（ADR "one child per session"，进程生死=会话生死）。
线形状源=原 src/symrepl/dap/service.py:28-34（三消息）+:94（pong 形状）。
铁律：线形状不自造——探针钉死真形，桩对真分歧进不来。
调试器知识零入册：本模块只说 DebugBackend 原语语汇，具体引擎住
*_backend.py（插拔册+工厂，2026-10-20 用户裁：session 不变、后端可换）。
"""
import argparse
import contextlib
import json
import os
import queue
import sys
import threading
from typing import Protocol

from childproto import VERSION
from error import SessionError, SessionErrCode

BACKENDS = {"lldb": "lldb_backend"}   # 插拔册：调试器名→适配器模块；加后端=加模块+一行
_backend_name = "lldb"                # main() 按 --backend 改写
_lldb_dir = ""                        # 工具链目录（lldb+lldb-server 同册）；main() 按 --lldb-dir 改写
_backend = None                       # 进程级单实例（后端构造期自带守门）


class DebugBackend(Protocol):
    """后端孔位（结构性满足：适配器不 import 本类，零循环）。
    停读面原语；continue_/wait_state/exit_code 随 continue 砖入册。"""

    def launch(self, ktest: str) -> int:
        """起 inferior 至入口停，回 inferior pid。"""
        ...
    #end def

    def report_entry_stop(self) -> dict:
        """config_done：入口停上报（entry_stopped→stopped），回 stopped 事件 params。"""
        ...
    #end def

    def threads(self) -> list[dict]:
        """线程清单（单线程契约）。"""
        ...
    #end def

    def stack_trace(self, thread_id: int, levels: int) -> dict:
        """栈帧（世代票据 frame id）。"""
        ...
    #end def

    def scopes(self, frame_id: int) -> list[dict]:
        """帧作用域引用（发 variablesReference，账本归适配器私有）。"""
        ...
    #end def

    def variables(self, ref: int) -> list[dict]:
        """引用背后的变量（平铺无子）。"""
        ...
    #end def

    def close(self) -> None:
        """收场拆除（inferior 随后端语义处置）。"""
        ...
    #end def
#end class


def read_frame(read, buf: bytes):
    """阻塞读一帧（Content-Length 线格式，规则权威=本地 framing.py）。返回 (dict|None, buf)。

    read=单参阻塞读可调用（BufferedReader.read1——小帧不憋整块，真 service :101-108 同教训）。
    """
    while b"\r\n\r\n" not in buf:
        chunk = read(4096)
        if not chunk:
            return None, buf          # EOF：父死信号
        buf += chunk
    #end while
    head, _, buf = buf.partition(b"\r\n\r\n")
    length = int(dict(l.split(b":", 1) for l in head.split(b"\r\n")
                      if b":" in l)[b"Content-Length"])
    while len(buf) < length:
        chunk = read(length - len(buf))
        if not chunk:
            return None, buf
        buf += chunk
    #end while
    body, buf = buf[:length], buf[length:]
    return json.loads(body.decode("utf-8")), buf
#end def


def send(protocol_out, obj: dict) -> None:
    body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    protocol_out.write(b"Content-Length: %d\r\n\r\n" % len(body) + body)
    protocol_out.flush()
#end def


def _reply_err(protocol_out, msg: dict, code: str, message: str) -> None:
    """线错误应答：码族=字符串（桩码族 "bad_request"/"no_thread" 同形；
    SessionError 码进线时取 name.lower() 串）。"""
    send(protocol_out, {"type": "response", "id": msg.get("id"), "ok": False,
                        "error": {"code": code, "message": message}})
#end def


def _run(protocol_out, msg: dict, fn):
    """原语调用统一壳：SessionError→wire 错误，成→ok+result 单帧。"""
    try:
        result = fn()
    except SessionError as e:
        _reply_err(protocol_out, msg, e.code.name.lower(), e.message)
        return
    #end try
    send(protocol_out, {"type": "response", "id": msg.get("id"), "ok": True,
                        "result": result or {}})
#end def


def _ensure_backend(params: dict):
    """工厂+单实例：懒 import（ping/握手路零后端依赖）；lldb_dir/target=端口面注入。"""
    global _backend
    if _backend is None:
        import importlib
        mod = importlib.import_module(BACKENDS[_backend_name])
        _backend = mod.make_backend(_lldb_dir, params["target"])
    #end if
    return _backend
#end def


def _backend_ready():
    """取后端实例；launch 前来取=NOT_LAUNCHED（协议 S 门挡不到的防御）。"""
    if _backend is None:
        raise SessionError(SessionErrCode.NOT_LAUNCHED, "no session in this process")
    #end if
    return _backend
#end def


def handle(protocol_out, msg: dict) -> None:
    cmd = msg.get("cmd")
    if cmd == "ping":
        send(protocol_out, {"type": "response", "id": msg.get("id"), "ok": True,
                            "result": {"pong": True, "service_version": VERSION}})
    elif cmd == "launch":
        _launch(protocol_out, msg)
    elif cmd == "config_done":
        _config_done(protocol_out, msg)
    elif cmd == "threads":
        _run(protocol_out, msg, lambda: {"threads": _backend_ready().threads()})
    elif cmd == "stack_trace":
        _stack_trace(protocol_out, msg)
    elif cmd == "scopes":
        _scopes(protocol_out, msg)
    elif cmd == "variables":
        _variables(protocol_out, msg)
    elif cmd == "continue":
        _run(protocol_out, msg, lambda: _backend_ready().continue_())
    else:
        send(protocol_out, {"type": "response", "id": msg.get("id"), "ok": False,
                            "error": {"code": "unknown_command",
                                      "message": f"unknown command {cmd!r}"}})
    #end if
#end def


def _launch(protocol_out, msg: dict) -> None:
    params = msg.get("params") or {}
    for key in ("ktest", "target"):
        v = params.get(key)
        if not isinstance(v, str) or not v:
            _reply_err(protocol_out, msg, "bad_request",
                       f"launch requires a '{key}' path string")
            return
        #end if
    #end for
    _run(protocol_out, msg,
         lambda: {"pid": _ensure_backend(params).launch(params["ktest"])})
#end def


def _config_done(protocol_out, msg: dict) -> None:
    try:
        stop = _backend_ready().report_entry_stop()
    except SessionError as e:
        _reply_err(protocol_out, msg, e.code.name.lower(), e.message)
        return
    #end try
    send(protocol_out, {"type": "response", "id": msg.get("id"), "ok": True, "result": {}})
    send(protocol_out, {"type": "event", "event": "stopped", "params": stop})
#end def


def _stack_trace(protocol_out, msg: dict) -> None:
    params = msg.get("params") or {}
    thread_id = params.get("thread_id", 0)
    if not isinstance(thread_id, int) or isinstance(thread_id, bool):
        # bool 是 int 子类（isinstance(True, int)=True），必须显式排除
        _reply_err(protocol_out, msg, "bad_request", "thread_id must be an integer")
        return
    #end if
    levels = params.get("levels", 20)
    if not isinstance(levels, int) or isinstance(levels, bool) or levels < 1:
        levels = 20                            # 坏值静默缺省（参考同款宽容）
    #end if
    _run(protocol_out, msg, lambda: _backend_ready().stack_trace(thread_id, levels))
#end def


def _scopes(protocol_out, msg: dict) -> None:
    params = msg.get("params") or {}
    frame_id = params.get("frame_id", 0)
    if not isinstance(frame_id, int) or isinstance(frame_id, bool) or frame_id < 0:
        # 负值归类型域（参考 decode 同款 bad_request）；世代校验归适配器
        _reply_err(protocol_out, msg, "bad_request",
                   "frameId must be a non-negative integer")
        return
    #end if
    _run(protocol_out, msg,
         lambda: {"scopes": _backend_ready().scopes(frame_id)})   # 命令名键重包（父面按键取）
#end def


def _variables(protocol_out, msg: dict) -> None:
    params = msg.get("params") or {}
    ref = params.get("variables_reference", 0)
    if not isinstance(ref, int) or isinstance(ref, bool):
        _reply_err(protocol_out, msg, "bad_request",
                   "variablesReference must be an integer")
        return
    #end if
    _run(protocol_out, msg,
         lambda: {"variables": _backend_ready().variables(ref)})  # 命令名键重包（父面按键取）
#end def


def _shutdown_backend() -> None:
    """收场尽力而为：close 崩不遮退出码（inferior 处置语义在后端 close 内）。"""
    global _backend
    if _backend is not None:
        with contextlib.suppress(Exception):
            _backend.close()
        #end with
        _backend = None
    #end if
#end def


def _reader(stdin_read, q: queue.Queue) -> None:
    """reader 线程（参考同构）：只读 stdin 入队，永不碰 SB/协议写路。
    半帧 buf 活在本线程内，队列只传完整 msg；坏帧=fatal、EOF=eof。"""
    buf = b""
    while True:
        try:
            msg, buf = read_frame(stdin_read, buf)
        except Exception:
            q.put(("fatal", None))            # 坏帧 no-resync
            return
        #end try
        if msg is None:
            q.put(("eof", None))              # 父死信号
            return
        #end if
        q.put(("message", msg))
    #end while
#end def


def main() -> int:
    global _backend_name, _lldb_dir
    parser = argparse.ArgumentParser(prog="session")
    parser.add_argument("--log", default=os.devnull, metavar="PATH")
    parser.add_argument("--backend", choices=sorted(BACKENDS), default="lldb")
    parser.add_argument("--lldb-dir", required=True, metavar="DIR",
                        help="toolchain dir containing lldb and lldb-server")
    args = parser.parse_args()
    _backend_name = args.backend
    _lldb_dir = args.lldb_dir

    protocol_out = sys.stdout.buffer          # hygiene 同真 service（:401-406）：先抓 fd1 再改道
    stdin_read = sys.stdin.buffer.read1       # type: ignore[attr-defined]  # 运行时是 BufferedReader（真 service :109 同款）
    with open(args.log, "a", buffering=1, errors="replace") as logf:
        sys.stdout, sys.stderr = logf, logf   # 杂音归日志（hygiene 先于 reader 启动，攻击点①）
        q = queue.Queue()
        threading.Thread(target=_reader, args=(stdin_read, q),
                         name="session-reader", daemon=True).start()
        while True:
            if _backend is not None:
                for ev in _backend.poll_events():   # 事件先发：响应不越其引发的事件
                    send(protocol_out, {"type": "event", **ev})
                #end for
            #end if
            try:
                kind, msg = q.get(timeout=0.05)
            except queue.Empty:
                continue                      # 无命令：回泵事件
            #end try
            if kind == "eof":
                _shutdown_backend()
                return 0                      # stdin-EOF：父死信号 → 干净自杀（:38-39）
            #end if
            if kind == "fatal":
                _shutdown_backend()
                return 3                      # 坏帧 no-resync（对齐真 service :40-43）
            #end if
            handle(protocol_out, msg)
        #end while
    #end with
#end def


if __name__ == "__main__":
    raise SystemExit(main())
#end if
