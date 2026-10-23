#!/usr/bin/env python3
"""probe_chaos — 砖5 child 死路混沌探针（真饵，2026-10-23）。

需求文本=design-notes「砖4+砖5 收场契约」混沌三路（C1/C2/C3 为收编钉，砖4 步1 已落核级）：
- C1 死亡在途：全链→SIGKILL child→stackTrace→2002 携 refused（快败不烧 timeout）
  →threads→2002（父活陪到底）→disconnect→resp+terminated+FIN rc=0（对尸 child 拆除幂等）
- C2 挂起：全链→SIGSTOP child→threads→2001 携 timeout（烧 timeout=10s，网预算注明）
  →SIGKILL 收场→disconnect 链同 C1
- C3 拆除判别（砖4）：全链+setBreakpoints→continue→bp 停（inferior 活着）→disconnect→
  resp+terminated→孤儿断言持有（get_sign/lldb-server 零残留=显式拆除确定性兑现）+FIN rc=0
孤儿纪律（契约攻击#3）：SIGKILL 会话员则 inferior/lldb-server 必成孤（backend.close 永不
运行）——C1/C2 探针自清孤儿、不持孤儿断言；孤儿断言只在 C3（显式拆除章）持有。
RED 预期（诚实标注）：收编钉——步1 核级已落，三路预期首跑即绿；本网钉真线端到端行为
（自毙≡SIGKILL 一章两死法，注入器按契约用 SIGKILL）。
码勘误（照 breakpoints 家法 AI 自收）：契约散文 C2"2002 携 upstream_timeout"笔误——
UPSTREAM_TIMEOUT=2001（error.py），网按枚举真值钉 2001。
"""
import itertools
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

import dap_client as dc

SRV_DIR = str(Path(__file__).resolve().parent.parent / "src" / "server")
PORT = 5697
KT = str(Path(__file__).resolve().parent.parent / "third_party/klee/ktest/get_sign_test000003.ktest")
TG = "/tmp/opencode/replay-build/get_sign"
SRC = str(Path(__file__).resolve().parent.parent / "tests/fixtures/src/get_sign.c")
LLDB_DIR = os.path.dirname(os.path.realpath(shutil.which("lldb")))   # 操作方侧解析一次，服务零猜测
fails = []

def start(port=PORT):
    subprocess.run(["fuser", "-k", f"{port}/tcp"], capture_output=True)  # 自洁
    time.sleep(0.2)
    p = subprocess.Popen(["python3", "schedule.py", str(port), "--lldb-dir", LLDB_DIR], cwd=SRV_DIR,
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    for _ in range(50):              # 只查监听不碰会话（单会话拓扑：试连=自杀）
        r = subprocess.run(["ss", "-tln"], capture_output=True, text=True)
        if f":{port} " in r.stdout:
            return p
        if p.poll() is not None:
            raise RuntimeError("server died at boot:\n" + p.stdout.read())
        time.sleep(0.1)
    #end for
    raise RuntimeError("server never listened")
#end def

def check(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        fails.append(name)
    #end if
#end def

def recv_or_fail(s, buf, tag, window=15):
    """收一帧；超时=没等到。默认窗口 15s（真 LLDB launch 1-3s）。"""
    try:
        s.settimeout(window)
        return dc.recv(s, buf)
    except socket.timeout:
        print(f"  <- ({tag} 超时未达)")
        return None, buf
    #end try
#end def

def is_event(m, name):
    return (m is not None and m.get("type") == "event" and m.get("event") == name)
#end def

def code_of(resp):
    b = resp.get("body") or {}
    return b.get("code"), b.get("detail")
#end def

def burst(s, buf, tags, window=5):
    """宽容收帧段（continue 砖同款：响应与事件竞争，逐帧收到段定员或超时）。"""
    frames = []
    for tag in tags:
        f, buf = recv_or_fail(s, buf, tag, window=window)
        if f is not None:
            frames.append(f)
        #end if
    #end for
    return frames, buf
#end def

def no_orphans():
    """孤儿断言（只在 C3 持有）：真 inferior 与 lldb-server 不得残留。"""
    return all(not subprocess.run(["pgrep", "-x", n], capture_output=True,
                                  text=True).stdout.strip()
               for n in ("get_sign", "lldb-server"))
#end def

def self_clean_orphans():
    """混沌定义（契约攻击#3）：杀会话员则 inferior/lldb-server 必成孤——探针自清。
    尽力而为：pkill 无匹配 rc=1 属常态。"""
    for n in ("get_sign", "lldb-server"):
        subprocess.run(["pkill", "-x", n], capture_output=True)
    #end for
    time.sleep(0.2)
#end def

def child_pid_of(server_pid):
    """会话肉身 pid：server 唯一直接子进程（session.py），按 cmdline 复核防误杀。"""
    r = subprocess.run(["pgrep", "-P", str(server_pid)], capture_output=True, text=True)
    for line in r.stdout.split():
        cmd = subprocess.run(["ps", "-p", line, "-o", "args="],
                             capture_output=True, text=True).stdout
        if "session.py" in cmd:
            return int(line)
        #end if
    #end for
    return None
#end def

def wait_dead(pid, timeout=3.0):
    """轮询 pid 死透（asyncio watcher 收割后 os.kill(pid,0) 报 ProcessLookupError）。"""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        #end try
        time.sleep(0.05)
    #end while
    return False
#end def


def full_chain(name, s, buf, seq):
    """三路共用的全链：initialize→launch 两事件→configDone 三帧收齐（宽容序）。"""
    dc.send(s, seq, "initialize", {"adapterId": "symrepl"})
    r, buf = dc.recv(s, buf)
    dc.send(s, seq, "launch", {"ktest": KT, "target": TG})
    e1, buf = recv_or_fail(s, buf, "process 事件")
    e2, buf = recv_or_fail(s, buf, "initialized 事件")
    check(name + ": launch 两事件就位", is_event(e1, "process") and is_event(e2, "initialized"))
    dc.send(s, seq, "configurationDone")
    got, buf = burst(s, buf, ("放行帧1", "放行帧2", "放行帧3"), window=15)
    check(name + ": 放行三帧收齐（双帧+stopped）",
          sum(1 for f in got if f.get("type") == "response") == 2
          and any(is_event(f, "stopped") for f in got))
    return buf
#end def

def farewell(name, s, buf, seq, p, *, log_teardown=False):
    """收场链（三路同形）：disconnect→[resp, terminated]→exit0；log_teardown=读 server
    日志断言显式拆除臂实跑（进程已死读管道安全）。"""
    dc.send(s, seq, "disconnect")
    r3, buf = recv_or_fail(s, buf, "disconnect resp")
    ok_d = (r3 is not None and r3.get("command") == "disconnect" and r3.get("success") is True)
    if ok_d:
        e3, buf = recv_or_fail(s, buf, "terminated 事件")
        ok_d = is_event(e3, "terminated")
    #end if
    check(name + ": disconnect→[resp, terminated]", ok_d)
    s.close()
    code = p.wait(timeout=10)
    check(name + ": 会话毕 exit=0", code == 0)
    if log_teardown:
        tail = p.stdout.read() if p.stdout else ""
        check(name + ": server 日志 child_teardown 在案（显式拆除臂实跑）",
              "child_teardown how=" in tail)
    #end if
#end def


def c1_death_in_transit():
    name = "C1 死亡在途"
    p = start()
    try:
        s, buf = dc.connect("127.0.0.1", PORT)
        seq = itertools.count(1)
        buf = full_chain(name, s, buf, seq)

        cp = child_pid_of(p.pid)
        check(name + ": 会话肉身在场（session.py 唯一直接子）", cp is not None)
        if cp is None:
            return
        #end if
        os.kill(cp, signal.SIGKILL)
        check(name + ": SIGKILL 后肉身死透", wait_dead(cp))
        time.sleep(0.5)              # 等 server 侧 reader 处理 stdout EOF（ch.dead 置位）

        # stackTrace 快败：Gone→2002，不烧 timeout
        t0 = time.monotonic()
        dc.send(s, seq, "stackTrace", {"threadId": 1})
        r, buf = recv_or_fail(s, buf, "stackTrace 拒", window=15)
        elapsed = time.monotonic() - t0
        check(name + ": stackTrace→2002 携 refused",
              r is not None and r.get("success") is False and code_of(r)[0] == 2002)
        check(name + f": 快败不烧 timeout（实测 {elapsed:.1f}s < 9s）", elapsed < 9.0)

        # 父活陪到底：再来一发照拒，server 进程仍活着
        dc.send(s, seq, "threads")
        r2, buf = recv_or_fail(s, buf, "threads 拒")
        check(name + ": threads→2002 且父活陪到底",
              r2 is not None and code_of(r2)[0] == 2002 and p.poll() is None)

        farewell(name, s, buf, seq, p)          # 对尸 child 拆除幂等 → FIN rc=0
    finally:
        self_clean_orphans()                    # chaos 定义：孤儿自清，不持断言
        if p.poll() is None:
            p.terminate(); p.wait(timeout=5)
        #end if
    #end try
#end def


def c2_hang_burn_timeout():
    name = "C2 挂起烧 timeout"
    p = start()
    try:
        s, buf = dc.connect("127.0.0.1", PORT)
        seq = itertools.count(1)
        buf = full_chain(name, s, buf, seq)

        cp = child_pid_of(p.pid)
        check(name + ": 会话肉身在场（session.py 唯一直接子）", cp is not None)
        if cp is None:
            return
        #end if
        os.kill(cp, signal.SIGSTOP)             # 挂起：进程活着，永远不应答

        # threads 烧满 timeout=10s → 2001（网预算：本查固定耗 ~10s）
        t0 = time.monotonic()
        dc.send(s, seq, "threads")
        r, buf = recv_or_fail(s, buf, "threads 烧窗", window=20)
        elapsed = time.monotonic() - t0
        check(name + ": threads→2001 携 timeout",
              r is not None and r.get("success") is False and code_of(r)[0] == 2001)
        check(name + f": 烧窗实测 {elapsed:.1f}s ≥ 9.5s（真烧 timeout 非 fast-fail）",
              elapsed >= 9.5)

        os.kill(cp, signal.SIGKILL)             # SIGKILL 收场（对挂起进程有效）
        check(name + ": SIGKILL 后肉身死透", wait_dead(cp))
        time.sleep(0.5)
        farewell(name, s, buf, seq, p)          # disconnect 链同 C1：对尸拆除幂等
    finally:
        self_clean_orphans()
        if p.poll() is None:
            p.terminate(); p.wait(timeout=5)
        #end if
    #end try
#end def


def c3_teardown_orphan():
    name = "C3 拆除判别（砖4）"
    p = start()
    try:
        s, buf = dc.connect("127.0.0.1", PORT)
        seq = itertools.count(1)
        buf = full_chain(name, s, buf, seq)

        # bp 停：inferior 活着（拆除对象在场，孤儿断言才有意义）
        dc.send(s, seq, "setBreakpoints",
                {"source": {"path": SRC}, "breakpoints": [{"line": 20}]})
        r, buf = recv_or_fail(s, buf, "setBreakpoints resp")
        bps = ((r or {}).get("body") or {}).get("breakpoints") or []
        check(name + ": setBreakpoints [20] verified",
              r is not None and r.get("success") is True
              and len(bps) == 1 and bps[0].get("verified") is True)
        dc.send(s, seq, "continue")
        b1, buf = burst(s, buf, ("P 突发1", "P 突发2"))   # bp 停段=[resp, stopped]
        stop = next((f for f in b1 if is_event(f, "stopped")), None)
        sb = (stop or {}).get("body") or {}
        check(name + ": continue 停于断点 reason=breakpoint", sb.get("reason") == "breakpoint")
        alive = bool(subprocess.run(["pgrep", "-x", "get_sign"], capture_output=True,
                                    text=True).stdout.strip())
        check(name + ": bp 停 inferior 在场（拆除对象活着）", alive)

        # disconnect=显式拆除：肉身在册（inferior 活着）走 TearDownChild 臂
        farewell(name, s, buf, seq, p, log_teardown=True)
        check(name + ": 孤儿断言持有（get_sign/lldb-server 零残留=显式拆除确定性）",
              no_orphans())
    finally:
        self_clean_orphans()
        if p.poll() is None:
            p.terminate(); p.wait(timeout=5)
        #end if
    #end try
#end def


if __name__ == "__main__":
    c1_death_in_transit()
    c2_hang_burn_timeout()
    c3_teardown_orphan()
    print("=" * 30)
    print("ALL PASS" if not fails else f"{len(fails)} FAIL: {fails}")
    sys.exit(1 if fails else 0)
#end if
