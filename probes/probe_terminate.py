#!/usr/bin/env python3
"""probe_terminate — terminate 广告钉兑现验收（RED 起步，2026-10-23）。

需求文本=design-notes「真客户端第三层钉」terminate 契约（用户裁：实现 terminate，
收场语义照 disconnect 两门同形；capability supportsTerminateRequest 已广告，
实现后广告转诚实）：
- T1 S0 断路器：initialize 前 terminate → resp success（无 body）+ terminated →
  纯转移 DEAD，会话毕 exit0
- T2 S3 带 inferior 在场：launch→入口停→terminate → resp success + terminated →
  inferior 被拆（无孤儿 get_sign/lldb-server）→ exit0
RED 预期（现状 terminate=unknown command → not implemented）：
T1/T2 的 resp success+terminated 查 FAIL；exit0 查 RED 期即绿（close→FIN 收场
已通，同 breakpoints P7/P8 模式）。
"""
import os, shutil, itertools, socket, subprocess, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import dap_client as dc

SRV_DIR = str(Path(__file__).resolve().parent.parent / "src" / "server")
PORT = 5701
KT = str(Path(__file__).resolve().parent.parent / "third_party/klee/ktest/get_sign_test000003.ktest")
TG = "/tmp/opencode/replay-build/get_sign"
LLDB_DIR = os.path.dirname(os.path.realpath(shutil.which("lldb")))
fails = []

def check(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        fails.append(name)
    #end if
#end def

def start():
    subprocess.run(["fuser", "-k", f"{PORT}/tcp"], capture_output=True)  # 自洁
    time.sleep(0.2)
    p = subprocess.Popen(["python3", "schedule.py", str(PORT), "--lldb-dir", LLDB_DIR],
                         cwd=SRV_DIR, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    for _ in range(50):
        r = subprocess.run(["ss", "-tln"], capture_output=True, text=True)
        if f":{PORT} " in r.stdout:
            return p
        if p.poll() is not None:
            raise RuntimeError("server died at boot:\n" + p.stdout.read())
        time.sleep(0.1)
    #end for
    raise RuntimeError("server never listened")
#end def

def recv_or_fail(s, buf, tag, window=15):
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

def no_orphans():
    return all(not subprocess.run(["pgrep", "-x", n], capture_output=True,
                                  text=True).stdout.strip()
               for n in ("get_sign", "lldb-server"))
#end def


def t1_s0_breaker():
    name = "T1 S0 terminate 断路器"
    p = start()
    try:
        s, buf = dc.connect("127.0.0.1", PORT)
        seq = itertools.count(1)
        # initialize 之前直接 terminate（无会话无肉身）
        dc.send(s, seq, "terminate")
        r, buf = recv_or_fail(s, buf, "terminate resp")
        e, buf = recv_or_fail(s, buf, "terminated 事件")
        check(name + ": terminate → resp success（无 body）",
              r is not None and r.get("command") == "terminate"
              and r.get("success") is True and r.get("body") in (None, {}))
        check(name + ": terminated 事件随（断路器与 disconnect 同形）", is_event(e, "terminated"))
        s.close()
        code = p.wait(timeout=10)
        check(name + ": 会话毕 exit=0", code == 0)
    finally:
        if p.poll() is None:
            p.terminate(); p.wait(timeout=5)
        #end if
    #end try
#end def


def t2_s3_teardown():
    name = "T2 S3 terminate 带 inferior 在场"
    p = start()
    try:
        s, buf = dc.connect("127.0.0.1", PORT)
        seq = itertools.count(1)
        dc.send(s, seq, "initialize", {"adapterId": "symrepl"})
        r, buf = dc.recv(s, buf)
        dc.send(s, seq, "launch", {"ktest": KT, "target": TG})
        e1, buf = recv_or_fail(s, buf, "process 事件")
        e2, buf = recv_or_fail(s, buf, "initialized 事件")
        pre = is_event(e1, "process") and is_event(e2, "initialized")
        check(name + ": 前置 launch 两事件就位", pre)
        if not pre:
            check(name + ": terminate → resp+terminated（前置死连带）", False)
            s.close()
            return
        #end if
        dc.send(s, seq, "configurationDone")
        got = []
        for _ in range(3):
            f, buf = recv_or_fail(s, buf, "放行帧")
            if f is not None:
                got.append(f)
            #end if
        #end for
        # 入口停在场 → terminate 应拆除 inferior
        dc.send(s, seq, "terminate")
        r, buf = recv_or_fail(s, buf, "terminate resp")
        e, buf = recv_or_fail(s, buf, "terminated 事件")
        check(name + ": terminate → resp success + terminated（拆除臂）",
              r is not None and r.get("success") is True and is_event(e, "terminated"))
        s.close()
        code = p.wait(timeout=10)
        check(name + ": 会话毕 exit=0", code == 0)
        check(name + ": inferior 被拆（无孤儿 get_sign/lldb-server）", no_orphans())
    finally:
        if p.poll() is None:
            p.terminate(); p.wait(timeout=5)
        #end if
    #end try
#end def


if __name__ == "__main__":
    t1_s0_breaker()
    t2_s3_teardown()
    print("=" * 30)
    print("ALL PASS" if not fails else f"{len(fails)} FAIL: {fails}")
    sys.exit(1 if fails else 0)
#end if
