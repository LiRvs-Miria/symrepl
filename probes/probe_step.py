#!/usr/bin/env python3
"""probe_step — step 族（next/stepIn/stepOut）验收（RED 起步，2026-10-23）。

需求文本=design-notes「step 族（2026-10-23 定案）」契约（用户两钉：同步阻塞
模型+三件起步；原 M2-d 兑现，2026-10-21 押后钉解除；真饵链=存痕复活）：
launch→setBreakpoints L20→configDone→continue→bp 停 L20→next→停 L21→
stepIn→get_sign 函数体→stepOut→回 main L21。
- T1 next → resp success 且无 body（DAP step 响应无 body）
- T2 next 后 stopped(reason="step") 事件（同步阻塞完成停相经事件泵）
- T3 帧0 落 get_sign.c line 21（source.path 非空）
- T4 stepIn → 帧0 name=get_sign 且 line 11（函数体首行）
- T5 stepOut → 帧0 name=main 且 line 21（返回点）
- T6 世代语义（resume 家法）：bp 停时旧 frameId，next 后 scopes(旧 id) →
  2002 携 stale_reference（世代推进=旧引用失效）
- T7 disconnect → [resp, terminated] 照旧
- T8 exit0 + 无孤儿 inferior/lldb-server
RED 预测：T1-T6 FAIL（next/stepIn/stepOut unknown command——命令缺位形；
T2 超时未达、T6 实际 success 非.stale）、T7-T8 绿（收场路独立）。
"""
import os, shutil, itertools, socket, subprocess, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import dap_client as dc

SRV_DIR = str(Path(__file__).resolve().parent.parent / "src" / "server")
PORT = 5703
KT = str(Path(__file__).resolve().parent.parent / "third_party/klee/ktest/get_sign_test000003.ktest")
TG = "/tmp/opencode/replay-build/get_sign"
SRC = str(Path(__file__).resolve().parent.parent / "tests/fixtures/src/get_sign.c")
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

def code_of(resp):
    b = resp.get("body") or {}
    return b.get("code"), b.get("detail")
#end def

def burst(s, buf, tags, window=5):
    frames = []
    for tag in tags:
        f, buf = recv_or_fail(s, buf, tag, window=window)
        if f is not None:
            frames.append(f)
        #end if
    #end for
    return frames, buf
#end def

def stop_body(frames):
    stop = next((f for f in frames if is_event(f, "stopped")), None)
    return stop, ((stop or {}).get("body") or {})
#end def

def frame0(s, buf, seq, tid):
    dc.send(s, seq, "stackTrace", {"threadId": tid, "startFrame": 0, "levels": 3})
    r, buf = recv_or_fail(s, buf, "stackTrace")
    f0 = (((r or {}).get("body") or {}).get("stackFrames") or [{}])[0]
    return f0, buf
#end def

def step_stop(s, buf, seq, command, tag):
    """步进一发：响应（success+无 body）→ 宽容收 stopped(reason=step)。
    返回 (resp_ok, stop_body, buf)。窗口 3s：步进毫秒级，RED 期超时快速失败。"""
    dc.send(s, seq, command)
    r, buf = recv_or_fail(s, buf, f"{command} resp", window=20)
    ok = (r is not None and r.get("command") == command
          and r.get("success") is True and r.get("body") in (None, {}))
    f, buf = recv_or_fail(s, buf, f"{command} stopped", window=3)
    sb = ((f or {}).get("body") or {})
    return ok, sb, buf
#end def

def no_orphans():
    return all(not subprocess.run(["pgrep", "-x", n], capture_output=True,
                                  text=True).stdout.strip()
               for n in ("get_sign", "lldb-server"))
#end def


def step_chain():
    name = "step 族全链"
    p = start()
    try:
        s, buf = dc.connect("127.0.0.1", PORT)
        seq = itertools.count(1)

        # 前置链（存量功能）：launch→配置期下断点 L20→放行→continue→bp 停
        dc.send(s, seq, "initialize", {"adapterId": "symrepl"})
        r, buf = dc.recv(s, buf)
        dc.send(s, seq, "launch", {"ktest": KT, "target": TG})
        e1, buf = recv_or_fail(s, buf, "process 事件")
        e2, buf = recv_or_fail(s, buf, "initialized 事件")
        pre = is_event(e1, "process") and is_event(e2, "initialized")
        check(name + ": 前置 launch 两事件就位", pre)
        if not pre:
            for t in ("T1", "T2", "T3", "T4", "T5", "T6", "T7", "T8"):
                check(f"{name}: {t}（前置死连带）", False)
            #end for
            s.close()
            return
        #end if
        dc.send(s, seq, "setBreakpoints",
                {"source": {"path": SRC}, "breakpoints": [{"line": 20}]})
        r, buf = recv_or_fail(s, buf, "setBreakpoints")
        dc.send(s, seq, "configurationDone")
        got, buf = burst(s, buf, ("放行1", "放行2", "放行3"))
        check(name + ": 放行三帧收齐（双帧+stopped）",
              sum(1 for f in got if f.get("type") == "response") == 2
              and any(is_event(f, "stopped") for f in got))
        dc.send(s, seq, "threads")
        tr, buf = recv_or_fail(s, buf, "threads 取 tid")
        tid = ((tr or {}).get("body") or {}).get("threads", [{}])[0].get("id")
        dc.send(s, seq, "continue")
        b1, buf = burst(s, buf, ("bp 突发1", "bp 突发2", "bp 突发3"))
        _, sb = stop_body(b1)
        check(name + ": 前置 bp 停 L20（reason=breakpoint）",
              sb.get("reason") == "breakpoint")
        f0, buf = frame0(s, buf, seq, tid)
        old_fid = f0.get("id")   # bp 停相世的 frameId（T6 世代钉用）

        # T1/T2/T3 next：停 L21
        ok1, sb1, buf = step_stop(s, buf, seq, "next", "next")
        check(name + ": T1 next resp success 无 body", ok1)
        check(name + ": T2 next 后 stopped reason=step",
              sb1.get("reason") == "step" and isinstance(sb1.get("threadId"), int))
        f0, buf = frame0(s, buf, seq, tid)
        check(name + ": T3 帧0 落 get_sign.c line 21（source.path 非空）",
              f0.get("line") == 21
              and bool((f0.get("source") or {}).get("path")))

        # T6 世代钉：bp 停相世的 frameId 在 next 后失效
        dc.send(s, seq, "scopes", {"frameId": old_fid})
        r, buf = recv_or_fail(s, buf, "T6 stale scopes")
        ok6 = (r is not None and r.get("success") is False
               and code_of(r)[0] == 2002
               and "stale_reference" in repr(code_of(r)[1]))
        check(name + ": T6 旧世代 frameId → 2002 stale_reference", ok6)

        # T4 stepIn：入 get_sign 函数体首行
        ok4, sb4, buf = step_stop(s, buf, seq, "stepIn", "stepIn")
        check(name + ": T4a stepIn resp success 无 body", ok4)
        f0, buf = frame0(s, buf, seq, tid)
        check(name + ": T4b 帧0 name=get_sign 且 line 11（函数体首行）",
              f0.get("name") == "get_sign" and f0.get("line") == 11)

        # T5 stepOut：回 main 返回点
        ok5, sb5, buf = step_stop(s, buf, seq, "stepOut", "stepOut")
        check(name + ": T5a stepOut resp success 无 body", ok5)
        f0, buf = frame0(s, buf, seq, tid)
        check(name + ": T5b 帧0 name=main 且 line 21（返回点）",
              f0.get("name") == "main" and f0.get("line") == 21)

        # T7/T8 收场照旧（inferior 停在场，显式拆除臂）
        dc.send(s, seq, "disconnect")
        r3, buf = recv_or_fail(s, buf, "disconnect resp")
        ok_d = (r3 is not None and r3.get("success") is True)
        if ok_d:
            e3, buf = recv_or_fail(s, buf, "terminated")
            ok_d = is_event(e3, "terminated")
        #end if
        check(name + ": T7 disconnect→[resp, terminated] 照旧", ok_d)
        s.close()
        code = p.wait(timeout=10)
        check(name + ": T8 会话毕 exit=0 + 无孤儿",
              code == 0 and no_orphans())
    finally:
        if p.poll() is None:
            p.terminate(); p.wait(timeout=5)
        #end if
    #end try
#end def


if __name__ == "__main__":
    step_chain()
    print("=" * 30)
    print("ALL PASS" if not fails else f"{len(fails)} FAIL: {fails}")
    sys.exit(1 if fails else 0)
#end if
