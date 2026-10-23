#!/usr/bin/env python3
"""probe_stacktrace — 横向第二条 stackTrace（守卫入门）验收（RED 起步，2026-10-20）。

需求文本=参考线形状（server.py:491 键映射 + service.py:370 子面守卫 + session.py:419 相位守卫）：
- 状态门：S1/S2 来 stackTrace → 1003 拒（fallback，detail 带 phase 名）
- S3 stackTrace → body={"stackFrames": [{id,name,line,column,path}, ...],
  "totalFrames": int}（键映射：子面 stack_frames/total_frames）
- 负路：threadId 不存在 → 2002（子面 no_thread 经 Rejected 败臂，detail 携 no_thread）
- 收场链照旧：disconnect→[resp, terminated]→exit0
E4 真化（2026-10-20）：真饵 KT/TG+真线程 id 两步走（threads→stackTrace）+孤儿断言；
not_stopped 负路 S3 内不可观测，押后随 continue 砖。
"""
import socket, subprocess, time, sys, os, shutil
from pathlib import Path
import dap_client as dc

SRV_DIR = str(Path(__file__).resolve().parent.parent / "src" / "server")
PORT = 5687
KT = str(Path(__file__).resolve().parent.parent / "third_party/klee/ktest/get_sign_test000003.ktest")
TG = "/tmp/opencode/replay-build/get_sign"
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

def recv_or_fail(s, buf, tag):
    """收一帧；超时=没等到。窗口 15s：真 LLDB launch 1-3s。"""
    try:
        s.settimeout(15)
        return dc.recv(s, buf)
    except socket.timeout:
        print(f"  <- ({tag} 超时未达)")
        return None, buf
    #end try
#end def

def no_orphans():
    """收场断言：真 inferior 与 lldb-server 不得残留（close 路契约）。"""
    return all(not subprocess.run(["pgrep", "-x", n], capture_output=True,
                                  text=True).stdout.strip()
               for n in ("get_sign", "lldb-server"))
#end def

def is_event(m, name):
    return (m is not None and m.get("type") == "event" and m.get("event") == name)
#end def

def code_of(resp):
    b = resp.get("body") or {}
    return b.get("code"), b.get("detail")
#end def


def stacktrace_chain():
    name = "stackTrace 全链"
    p = start()
    try:
        s, buf = dc.connect("127.0.0.1", PORT)
        seq = __import__("itertools").count(1)

        dc.send(s, seq, "initialize", {"adapterId": "symrepl"})
        r, buf = dc.recv(s, buf)

        # 状态门 S1：未 launch 来 stackTrace → 1003 拒
        dc.send(s, seq, "stackTrace", {"threadId": 1})
        r, buf = recv_or_fail(s, buf, "S1 stackTrace 拒")
        ok = (r is not None and r.get("success") is False
              and code_of(r)[0] == 1003 and "S1" in (code_of(r)[1] or ""))
        check(name + ": S1 stackTrace 被状态门拒（1003）", ok)

        # 到 S3：launch → 两事件；configDone → 三帧收齐（双帧+stopped，宽容序）
        dc.send(s, seq, "launch", {"ktest": KT, "target": TG})
        e1, buf = recv_or_fail(s, buf, "process 事件")
        e2, buf = recv_or_fail(s, buf, "initialized 事件")
        check(name + ": launch 两事件就位", is_event(e1, "process") and is_event(e2, "initialized"))
        dc.send(s, seq, "configurationDone")
        got = []
        for tag in ("放行帧1", "放行帧2", "放行帧3"):
            f, buf = recv_or_fail(s, buf, tag)
            if f is not None:
                got.append(f)
            #end if
        #end for
        check(name + ": 放行三帧收齐（双帧+stopped）",
              sum(1 for f in got if f.get("type") == "response") == 2
              and any(is_event(f, "stopped") for f in got))

        # S3 两步走：threads 取真 id（真线程 id=OS tid，桩时代假 1 已退役）→ stackTrace
        dc.send(s, seq, "threads")
        tr, buf = recv_or_fail(s, buf, "threads 取真 id")
        tid = ((tr.get("body") or {}).get("threads") or [{}])[0].get("id") if tr is not None else None
        check(name + ": threads 取真 id（int）", isinstance(tid, int))

        dc.send(s, seq, "stackTrace", {"threadId": tid})
        r, buf = recv_or_fail(s, buf, "S3 stackTrace 响应")
        body = (r.get("body") or {}) if r is not None else {}
        frames = body.get("stackFrames")
        shape_ok = (isinstance(frames, list) and len(frames) >= 1
                    and all(isinstance(f.get("id"), int) and isinstance(f.get("name"), str)
                            and isinstance(f.get("line"), int) and isinstance(f.get("column"), int)
                            and (f.get("source") is None
                                 or (isinstance(f["source"], dict)
                                     and isinstance(f["source"].get("name"), str)
                                     and isinstance(f["source"].get("path"), str)))
                            for f in frames)
                    and isinstance(body.get("totalFrames"), int))
        check(name + ": S3 stackTrace 成臂（DAP StackFrame：source{name,path} 包裹+totalFrames）",
              r is not None and r.get("success") is True and shape_ok)

        # 负路：线程不存在 → 2002（子面 no_thread 经 Rejected 败臂）
        dc.send(s, seq, "stackTrace", {"threadId": 99})
        r, buf = recv_or_fail(s, buf, "no_thread 负路")
        ok = (r is not None and r.get("success") is False
              and code_of(r)[0] == 2002 and "no_thread" in repr(code_of(r)[1]))
        check(name + ": no_thread 负路（2002+detail 携 no_thread）", ok)

        # 收场链照旧
        dc.send(s, seq, "disconnect")
        r3, buf = recv_or_fail(s, buf, "disconnect resp")
        ok_d = (r3 is not None and r3.get("command") == "disconnect" and r3.get("success") is True)
        if ok_d:
            e3, buf = recv_or_fail(s, buf, "terminated 事件")
            ok_d = is_event(e3, "terminated")
        #end if
        check(name + ": disconnect→[resp, terminated] 照旧", ok_d)

        s.close()
        code = p.wait(timeout=10)
        check(name + ": 会话毕 exit=0", code == 0)
        check(name + ": 无孤儿 inferior/lldb-server", no_orphans())
    finally:
        if p.poll() is None:
            p.terminate(); p.wait(timeout=5)
        #end if
    #end try
#end def


if __name__ == "__main__":
    stacktrace_chain()
    print("=" * 30)
    print("ALL PASS" if not fails else f"{len(fails)} FAIL: {fails}")
    sys.exit(1 if fails else 0)
#end if
