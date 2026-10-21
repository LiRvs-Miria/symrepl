#!/usr/bin/env python3
"""probe_breakpoints — setBreakpoints（第二次停相产出者）验收（RED 起步，2026-10-20）。

需求文本=design-notes setBreakpoints 契约（参考线形状：server.py:583-618 参数提取/键映射
+ service.py:321-329 类型域 + session.py:492-530 全量替换断点表+resolved line）：
- P1 状态门：S1 来 setBreakpoints → 1003 拒
- P2 全链：S3 setBreakpoints [20,21] → 两行 verified True+line int
- P3 命中上报：continue → stopped(reason="breakpoint")+hitBreakpointIds 非空+threadId int
- P4 第二停相面：stackTrace 帧0 落 get_sign.c line 20（path 非空）→ scopes(新世代帧 id)
  → Locals 新世代引用 → variables 平铺（内容不钉）
- P5 stale 真实触发：scopes(frameId=0)（旧世代 0）→ 2002 携 stale_reference
- P6 替换语义行为钉：setBreakpoints [21] → continue → 停于 line 21
  （旧 bp 20 残留则先停 20=替换失败当场见）
- P7 生命周期闭环：末轮 continue → exited{exitCode int} 先 + terminated 随
- P8 收场链照旧：disconnect → [resp, terminated] → exit0；孤儿不残留
RED 预期（契约）：P1-P5 FAIL（命令未知/断点不生效 continue 直通 exit）、P6 错位 FAIL、
P7-P8 绿（生命周期事件已通）。
"""
import socket, subprocess, time, sys, os, shutil, itertools
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "client"))
import dap_client as dc

SRV_DIR = str(Path(__file__).resolve().parent.parent / "server")
PORT = 5693
KT = str(Path(__file__).resolve().parent.parent.parent / "third_party/klee/ktest/get_sign_test000003.ktest")
TG = "/tmp/opencode/replay-build/get_sign"
SRC = str(Path(__file__).resolve().parent.parent.parent / "tests/fixtures/src/get_sign.c")
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

def stop_body(burst_frames):
    stop = next((f for f in burst_frames if is_event(f, "stopped")), None)
    return stop, ((stop or {}).get("body") or {})
#end def

def no_orphans():
    """收场断言：真 inferior 与 lldb-server 不得残留（close 路契约）。"""
    return all(not subprocess.run(["pgrep", "-x", n], capture_output=True,
                                  text=True).stdout.strip()
               for n in ("get_sign", "lldb-server"))
#end def


def breakpoints_chain():
    name = "setBreakpoints 全链"
    p = start()
    try:
        s, buf = dc.connect("127.0.0.1", PORT)
        seq = itertools.count(1)

        dc.send(s, seq, "initialize", {"adapterId": "symrepl"})
        r, buf = dc.recv(s, buf)

        # P1 状态门：S1（initialize 已答未 launch）来 setBreakpoints → 1003 拒
        dc.send(s, seq, "setBreakpoints", {"source": {"path": SRC}, "breakpoints": []})
        r, buf = recv_or_fail(s, buf, "S1 setBreakpoints 拒")
        ok = (r is not None and r.get("success") is False
              and code_of(r)[0] == 1003 and "S1" in (code_of(r)[1] or ""))
        check(name + ": P1 S1 setBreakpoints 被状态门拒（1003）", ok)

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

        # P2 全链：S3 setBreakpoints [20,21] → 两行 verified True+line int
        # （resolved line 与请求行可能不同=地址回解析，只钉 verified+int 不钉相等）
        dc.send(s, seq, "setBreakpoints",
                {"source": {"path": SRC}, "breakpoints": [{"line": 20}, {"line": 21}]})
        r, buf = recv_or_fail(s, buf, "setBreakpoints [20,21] resp")
        bps = ((r or {}).get("body") or {}).get("breakpoints") or []
        ok = (r is not None and r.get("success") is True and len(bps) == 2
              and all(bp.get("verified") is True and isinstance(bp.get("line"), int)
                      for bp in bps))
        check(name + ": P2 两行 verified True+line int", ok)

        # P3 命中上报：continue → 停于断点（第二次停相产出；GREEN 段=[resp,stopped]，
        # RED 段=[resp,exited,terminated] 直通 exit——段定员 3 兼容两形）
        dc.send(s, seq, "continue")
        b1, buf = burst(s, buf, ("P3 突发1", "P3 突发2", "P3 突发3"))
        cont = next((f for f in b1 if f.get("type") == "response"
                     and f.get("command") == "continue"), None)
        stop, sb = stop_body(b1)
        check(name + ": P3 停于断点 reason=breakpoint+hitBreakpointIds 非空+threadId int",
              cont is not None and cont.get("success") is True
              and sb.get("reason") == "breakpoint"
              and isinstance(sb.get("hitBreakpointIds"), list) and sb["hitBreakpointIds"]
              and isinstance(sb.get("threadId"), int))

        # P4 第二停相面：stackTrace→帧0 落 line 20→scopes(新世代帧 id)→Locals→variables 平铺
        tid = sb.get("threadId") or 1
        dc.send(s, seq, "stackTrace", {"threadId": tid})
        r, buf = recv_or_fail(s, buf, "P4 stackTrace resp")
        frames = ((r or {}).get("body") or {}).get("stackFrames") or []
        f0 = frames[0] if frames else {}
        check(name + ": P4a 帧0 落 get_sign.c line 20（path 非空）",
              r is not None and r.get("success") is True
              and f0.get("line") == 20 and bool(f0.get("path")))
        fid = f0.get("id") or 0
        dc.send(s, seq, "scopes", {"frameId": fid})
        r, buf = recv_or_fail(s, buf, "P4 scopes resp")
        scopes = ((r or {}).get("body") or {}).get("scopes") or []
        ref = scopes[0].get("variablesReference") if scopes else 0
        check(name + ": P4b Locals 新世代引用",
              r is not None and r.get("success") is True and scopes
              and scopes[0].get("name") == "Locals"
              and isinstance(ref, int) and ref > 0)
        dc.send(s, seq, "variables", {"variablesReference": ref or 0})
        r, buf = recv_or_fail(s, buf, "P4 variables resp")
        check(name + ": P4c variables 平铺（形状钉，内容不钉）",
              r is not None and r.get("success") is True
              and isinstance((r.get("body") or {}).get("variables"), list))

        # P5 stale 真实触发：旧世代帧 id（gen0）——此刻相位 stopped（命中停相），
        # 守卫序=相位先于世代解码 → 世代 0≠当前 → 2002 携 stale_reference
        dc.send(s, seq, "scopes", {"frameId": 0})
        r, buf = recv_or_fail(s, buf, "P5 stale 负路")
        ok = (r is not None and r.get("success") is False
              and code_of(r)[0] == 2002 and "stale_reference" in repr(code_of(r)[1]))
        check(name + ": P5 旧世代帧 id → 2002+stale_reference（负路真实触发）", ok)

        # P6 替换语义行为钉：setBreakpoints [21]（全量删旧）→ continue → 停于 line 21
        dc.send(s, seq, "setBreakpoints",
                {"source": {"path": SRC}, "breakpoints": [{"line": 21}]})
        r, buf = recv_or_fail(s, buf, "P6 setBreakpoints [21] resp")
        bps = ((r or {}).get("body") or {}).get("breakpoints") or []
        check(name + ": P6a 替换为 [21] 一行 verified True",
              r is not None and r.get("success") is True and len(bps) == 1
              and bps[0].get("verified") is True and isinstance(bps[0].get("line"), int))
        dc.send(s, seq, "continue")
        b2, buf = burst(s, buf, ("P6 突发1", "P6 突发2", "P6 突发3"))
        stop2, sb2 = stop_body(b2)
        tid2 = sb2.get("threadId") or tid
        dc.send(s, seq, "stackTrace", {"threadId": tid2})
        r, buf = recv_or_fail(s, buf, "P6 stackTrace resp")
        f0b = (((r or {}).get("body") or {}).get("stackFrames") or [{}])[0]
        check(name + ": P6b 停于 line 21（旧 bp20 残留=先停 20，替换失败当场见）",
              stop2 is not None and f0b.get("line") == 21)

        # P7 生命周期闭环：末轮 continue → exited{exitCode int} 先 + terminated 随
        dc.send(s, seq, "continue")
        b3, buf = burst(s, buf, ("P7 突发1", "P7 突发2", "P7 突发3"))
        ei = next((i for i, f in enumerate(b3) if is_event(f, "exited")), None)
        ti = next((i for i, f in enumerate(b3) if is_event(f, "terminated")), None)
        check(name + ": P7 exited{exitCode int} 先于 terminated",
              ei is not None and ti is not None and ei < ti
              and isinstance((b3[ei].get("body") or {}).get("exitCode"), int))

        # P8 收场链照旧（disconnect 对 dead inferior 拆除）
        dc.send(s, seq, "disconnect")
        r3, buf = recv_or_fail(s, buf, "disconnect resp")
        ok_d = (r3 is not None and r3.get("command") == "disconnect" and r3.get("success") is True)
        if ok_d:
            e3, buf = recv_or_fail(s, buf, "terminated 事件")
            ok_d = is_event(e3, "terminated")
        #end if
        check(name + ": P8 disconnect→[resp, terminated] 照旧", ok_d)

        s.close()
        code = p.wait(timeout=10)
        check(name + ": 会话毕 exit=0", code == 0)
        check(name + ": 无孤儿 inferior/lldb-server", no_orphans())
        if fails:                                # 失败取证：server 侧尾巴倾倒（flaky 排障）
            try:
                print("---- server tail (forensics) ----")
                print(p.stdout.read()[-2000:] if p.stdout else "(no pipe)")
            except Exception as e:
                print(f"(forensics unavailable: {e!r})")
            #end try
        #end if
    finally:
        if p.poll() is None:
            p.terminate(); p.wait(timeout=5)
        #end if
    #end try
#end def


if __name__ == "__main__":
    breakpoints_chain()
    print("=" * 30)
    print("ALL PASS" if not fails else f"{len(fails)} FAIL: {fails}")
    sys.exit(1 if fails else 0)
#end if
