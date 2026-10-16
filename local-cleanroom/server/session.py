#!/usr/bin/env python3
"""session.py — 桩：交互式回放会话的子进程肉身（进程即会话，真逻辑后续填，本文件原地演化）。

形状源=原 src/symrepl/dap/service.py:28-34（三消息）+:94（pong 形状）。
砖1 只需三件事：ping 探活（回显 id）/ stdin-EOF exit0（父死信号，decision 8
对偶）/ 坏帧 exit3（no-resync 对齐 :40-43）。
铁律：线形状不自造——探针钉死真形，桩对真分歧进不来。
"""
import argparse
import json
import os
import sys

from childproto import VERSION


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


def handle(protocol_out, msg: dict) -> None:
    if msg.get("cmd") == "ping":
        send(protocol_out, {"type": "response", "id": msg.get("id"), "ok": True,
                            "result": {"pong": True, "service_version": VERSION}})
    else:
        send(protocol_out, {"type": "response", "id": msg.get("id"), "ok": False,
                            "error": {"code": "unknown_command",
                                      "message": f"stub: {msg.get('cmd')!r}"}})
    #end if
#end def


def main() -> int:
    parser = argparse.ArgumentParser(prog="stub-session")
    parser.add_argument("--log", default=os.devnull, metavar="PATH")
    args = parser.parse_args()

    protocol_out = sys.stdout.buffer          # hygiene 同真 service（:401-406）：先抓 fd1 再改道
    stdin_read = sys.stdin.buffer.read1       # type: ignore[attr-defined]  # 运行时是 BufferedReader（真 service :109 同款）
    with open(args.log, "a", buffering=1, errors="replace") as logf:
        sys.stdout, sys.stderr = logf, logf   # 杂音归日志，协议通道独占 fd1
        buf = b""
        while True:
            try:
                msg, buf = read_frame(stdin_read, buf)
            except Exception:
                return 3                      # 坏帧 no-resync（对齐真 service :40-43）
            #end try
            if msg is None:
                return 0                      # stdin-EOF：父死信号 → 干净自杀（:38-39）
            #end if
            handle(protocol_out, msg)
        #end while
    #end with
#end def


if __name__ == "__main__":
    raise SystemExit(main())
#end if
