#!/usr/bin/env python3
"""probe_lldbdir — stdio/VS Code 接线第一刀：lldbDir 参数化验收（RED 起步，2026-10-23）。

需求文本=design-notes「stdio/VS Code 接线」契约（A 案：lldbDir 经 launch 请求参数携带，
argv --lldb-dir 降 optional 作 boot 缺省；decision 14 零码保持，E4 argv-required 字面修订）：
- S1 TCP boot 无 dir：进程存活（argparse optional 化）→ launch 携 lldbDir → 全链
  （值真流向 spawn 通道）→ threads → disconnect exit0
- S2 stdio boot 无 dir：进程存活 → initialize caps（VS Code spawn 形态前提）→ stdin EOF exit0
- S3 单会话四连发（launch 失败留 S1 可重递，转移表 R5→R6 家法）：
  无 lldbDir → 2002 携 no lldb_dir / lldbDir="" → 1001（形状坏核拦）/
  lldbDir=不存在 → 2002 携 unavailable / lldbDir=真 dir → 全链 exit0
boot 值兼容路（argv 给+launch 不给）不另设查——17 存量网全走 argv 路=回归证据。
RED 预测（契约诚实标注）：12/12 FAIL 全红——现状 --lldb-dir required，三场景 boot 即死
（argparse exit 2），连带全红（模块缺位形，非行为缺失形）。
"""
import itertools
import json
import os
import select
import shutil
import subprocess
import sys
import time
from pathlib import Path

import dap_client as dc

SRV_DIR = str(Path(__file__).resolve().parent.parent / "src" / "server")
PORT = 5699
KT = str(Path(__file__).resolve().parent.parent / "third_party/klee/ktest/get_sign_test000003.ktest")
TG = "/tmp/opencode/replay-build/get_sign"
LLDB_DIR = os.path.dirname(os.path.realpath(shutil.which("lldb")))   # 操作方侧解析一次
fails = []

def check(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        fails.append(name)
    #end if
#end def

def start_soft(port=PORT):
    """boot 不强求活（RED 点=argparse optional 化）：死=booted False，不炸网。"""
    subprocess.run(["fuser", "-k", f"{port}/tcp"], capture_output=True)  # 自洁
    time.sleep(0.2)
    p = subprocess.Popen(["python3", "schedule.py", str(port)], cwd=SRV_DIR,
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    for _ in range(50):                  # 只查监听不碰会话（单会话拓扑：试连=自杀）
        if p.poll() is not None:
            return p, False
        #end if
        r = subprocess.run(["ss", "-tln"], capture_output=True, text=True)
        if f":{port} " in r.stdout:
            return p, True
        #end if
        time.sleep(0.1)
    #end for
    return p, False
#end def

def is_event(m, name):
    return (m is not None and m.get("type") == "event" and m.get("event") == name)
#end def

def code_of(resp):
    b = resp.get("body") or {}
    return b.get("code"), b.get("detail")
#end def

def recv_or_fail(s, buf, tag, window=15):
    try:
        s.settimeout(window)
        return dc.recv(s, buf)
    except TimeoutError:
        print(f"  <- ({tag} 超时未达)")
        return None, buf
    #end try
#end def


def s1_tcp_launch_carry():
    name = "S1 TCP boot 无 dir"
    p, booted = start_soft()
    try:
        check(name + ": boot 无 --lldb-dir 存活（argparse optional 化）", booted)
        if not booted:
            check(name + ": launch 携 lldbDir 全链（boot 死 RED 连带）", False)
            check(name + ": configDone 放行+threads（boot 死 RED 连带）", False)
            check(name + ": disconnect→[resp, terminated]→exit0（boot 死 RED 连带）", False)
            return
        #end if
        s, buf = dc.connect("127.0.0.1", PORT)
        seq = itertools.count(1)
        dc.send(s, seq, "initialize", {"adapterId": "symrepl"})
        r, buf = dc.recv(s, buf)
        dc.send(s, seq, "launch", {"ktest": KT, "target": TG, "lldbDir": LLDB_DIR})
        e1, buf = recv_or_fail(s, buf, "process 事件")
        e2, buf = recv_or_fail(s, buf, "initialized 事件")
        check(name + ": launch 携 lldbDir → 两事件（值真流向 spawn 通道）",
              is_event(e1, "process") and is_event(e2, "initialized"))
        dc.send(s, seq, "configurationDone")
        got, buf = [], buf
        for _ in range(3):                # 双帧+stopped，宽容序
            f, buf = recv_or_fail(s, buf, "放行帧")
            if f is not None:
                got.append(f)
            #end if
        #end for
        dc.send(s, seq, "threads")
        tr, buf = recv_or_fail(s, buf, "threads")
        ok = (sum(1 for f in got if f.get("type") == "response") == 2
              and any(is_event(f, "stopped") for f in got)
              and tr is not None and tr.get("success") is True)
        check(name + ": configDone 放行三帧+threads 成", ok)
        dc.send(s, seq, "disconnect")
        r3, buf = recv_or_fail(s, buf, "disconnect resp")
        e3, buf = recv_or_fail(s, buf, "terminated")
        s.close()
        code = p.wait(timeout=10)
        check(name + ": disconnect→[resp, terminated]→exit0",
              r3 is not None and r3.get("success") is True
              and is_event(e3, "terminated") and code == 0)
    finally:
        if p.poll() is None:
            p.terminate(); p.wait(timeout=5)
        #end if
    #end try
#end def


def _send_msg(proc, seq, command, arguments=None):
    msg = {"seq": seq, "type": "request", "command": command,
           "arguments": arguments or {}}
    body = json.dumps(msg, ensure_ascii=False).encode("utf-8")
    proc.stdin.write(b"Content-Length: %d\r\n\r\n" % len(body) + body)
    proc.stdin.flush()
#end def

class _Buf:
    """子进程 stdout 的带超时帧读取（probe_stdio 同款）。"""
    def __init__(self, proc):
        self.proc = proc
        self.fd = proc.stdout.fileno()
        self.buf = b""
    #end def

    def _fill(self, timeout):
        r, _, _ = select.select([self.fd], [], [], timeout)
        if not r:
            raise TimeoutError("stdio recv timeout")
        #end if
        chunk = os.read(self.fd, 65536)
        if not chunk:
            raise EOFError("stdio EOF")
        #end if
        self.buf += chunk
    #end def

    def recv_msg(self, timeout=15):
        deadline = time.monotonic() + timeout
        while b"\r\n\r\n" not in self.buf:
            self._fill(deadline - time.monotonic())
        #end while
        head, _, rest = self.buf.partition(b"\r\n\r\n")
        length = int(dict(l.split(b":", 1) for l in head.split(b"\r\n")
                          if b":" in l)[b"Content-Length"])
        self.buf = rest
        while len(self.buf) < length:
            self._fill(deadline - time.monotonic())
        #end while
        body, self.buf = self.buf[:length], self.buf[length:]
        return json.loads(body.decode("utf-8"))
    #end def
#end class


def s2_stdio_boot():
    name = "S2 stdio boot 无 dir"
    # stderr 分道（probe_stdio 家法）：stdout 是协议通道，日志混入=帧解析炸
    p = subprocess.Popen([sys.executable, "schedule.py", "--stdio"], cwd=SRV_DIR,
                         stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE, text=False)
    try:
        time.sleep(0.5)
        booted = p.poll() is None
        check(name + ": boot 无 --lldb-dir 存活（argparse optional 化）", booted)
        if not booted:
            check(name + ": initialize caps（boot 死 RED 连带）", False)
            check(name + ": stdin EOF → exit0（boot 死 RED 连带）", False)
            return
        #end if
        r = _Buf(p)
        _send_msg(p, 1, "initialize", {"adapterId": "symrepl"})
        m = r.recv_msg()
        check(name + ": initialize caps（stdio 握手，VS Code spawn 形态前提）",
              m.get("type") == "response" and m.get("success") is True)
        p.stdin.close()
        code = p.wait(timeout=15)
        check(name + ": stdin EOF → exit0", code == 0)
    finally:
        if p.poll() is None:
            p.terminate(); p.wait(timeout=5)
        #end if
    #end try
#end def


def s3_four_launch_chain():
    name = "S3 单会话四连发"
    p, booted = start_soft()
    try:
        if not booted:
            for tag in ("无 lldbDir→2002", "lldbDir=''→1001", "lldbDir=不存在→2002",
                        "lldbDir=真 dir 全链"):
                check(name + f": {tag}（boot 死 RED 连带）", False)
            #end for
            return
        #end if
        s, buf = dc.connect("127.0.0.1", PORT)
        seq = itertools.count(1)
        dc.send(s, seq, "initialize", {"adapterId": "symrepl"})
        r, buf = dc.recv(s, buf)

        # ①launch 不带 lldbDir（boot 也没给）→ 壳拦 2002 携 no lldb_dir
        dc.send(s, seq, "launch", {"ktest": KT, "target": TG})
        r, buf = recv_or_fail(s, buf, "无 lldbDir")
        check(name + ": 无 lldbDir → 2002 携 no lldb_dir（壳拦，留 S1 可重递）",
              r is not None and r.get("success") is False and code_of(r)[0] == 2002
              and "no lldb_dir" in repr(code_of(r)[1]))

        # ②lldbDir=""（形状坏）→ 核 validate_launch 拦 1001
        dc.send(s, seq, "launch", {"ktest": KT, "target": TG, "lldbDir": ""})
        r, buf = recv_or_fail(s, buf, "lldbDir 空串")
        check(name + ": lldbDir='' → 1001（形状坏核拦）",
              r is not None and r.get("success") is False and code_of(r)[0] == 1001)

        # ③lldbDir=不存在路径（值真流向 session.py：lldb 加载死）→ SpawnFailed 路 2002
        dc.send(s, seq, "launch", {"ktest": KT, "target": TG,
                                   "lldbDir": "/nonexistent-lldb-dir"})
        r, buf = recv_or_fail(s, buf, "lldbDir 不存在")
        check(name + ": lldbDir=不存在 → 2002 携 unavailable（SpawnFailed 路）",
              r is not None and r.get("success") is False and code_of(r)[0] == 2002)

        # ④lldbDir=真 dir → 全链（正值证明参数达 session.py 并真实驱动 lldb）
        dc.send(s, seq, "launch", {"ktest": KT, "target": TG, "lldbDir": LLDB_DIR})
        e1, buf = recv_or_fail(s, buf, "process 事件")
        e2, buf = recv_or_fail(s, buf, "initialized 事件")
        check(name + ": lldbDir=真 dir → 两事件（可重递后成）",
              is_event(e1, "process") and is_event(e2, "initialized"))
        dc.send(s, seq, "configurationDone")
        got, buf = [], buf
        for _ in range(3):
            f, buf = recv_or_fail(s, buf, "放行帧")
            if f is not None:
                got.append(f)
            #end if
        #end for
        dc.send(s, seq, "threads")
        tr, buf = recv_or_fail(s, buf, "threads")
        dc.send(s, seq, "disconnect")
        r3, buf = recv_or_fail(s, buf, "disconnect resp")
        e3, buf = recv_or_fail(s, buf, "terminated")
        s.close()
        code = p.wait(timeout=10)
        check(name + ": configDone 放行+threads+disconnect exit0（值真流向收尾）",
              sum(1 for f in got if f.get("type") == "response") == 2
              and any(is_event(f, "stopped") for f in got)
              and tr is not None and tr.get("success") is True
              and r3 is not None and r3.get("success") is True
              and is_event(e3, "terminated") and code == 0)
    finally:
        if p.poll() is None:
            p.terminate(); p.wait(timeout=5)
        #end if
    #end try
#end def


if __name__ == "__main__":
    s1_tcp_launch_carry()
    s2_stdio_boot()
    s3_four_launch_chain()
    print("=" * 30)
    print("ALL PASS" if not fails else f"{len(fails)} FAIL: {fails}")
    sys.exit(1 if fails else 0)
#end if
