#!/usr/bin/env python3
"""probe_scopes — 横向第三条 scopes/variables（世代票据消费侧首撞）验收（RED 起步，2026-10-20）。

需求文本=参考线形状（server.py:517-581 键映射+expensive 补齐 + session.py:461-561 引用账本）：
- 状态门：S1/S2 来 scopes/variables → 1003 拒（fallback）
- S3 两步走：stackTrace 取真帧 id（世代*1000+index）→ scopes(frameId)→
  {"scopes": [{"name", "variablesReference", "expensive": False}]}（expensive 父面补）
  → variables(variablesReference)→{"variables": [{name,value,type,variablesReference}]}（平铺）
- 负路 stale：frameId 世代≠当前 / variablesReference 未登记 → 2002 携 stale_reference
- 收场链照旧：disconnect→[resp, terminated]→exit0；孤儿不残留
守卫单集（scopes/variables 只收 stopped，用户钉照参考）；真 LLDB 后端（E4 停读面起）。
"""
import socket, subprocess, time, sys, os, shutil
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "client"))
import dap_client as dc

SRV_DIR = str(Path(__file__).resolve().parent.parent / "server")
PORT = 5689
KT = str(Path(__file__).resolve().parent.parent.parent / "third_party/klee/ktest/get_sign_test000003.ktest")
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

def is_event(m, name):
    return (m is not None and m.get("type") == "event" and m.get("event") == name)
#end def

def code_of(resp):
    b = resp.get("body") or {}
    return b.get("code"), b.get("detail")
#end def

def no_orphans():
    """收场断言：真 inferior 与 lldb-server 不得残留（close 路契约）。"""
    return all(not subprocess.run(["pgrep", "-x", n], capture_output=True,
                                  text=True).stdout.strip()
               for n in ("get_sign", "lldb-server"))
#end def


def scopes_chain():
    name = "scopes/variables 全链"
    p = start()
    try:
        s, buf = dc.connect("127.0.0.1", PORT)
        seq = __import__("itertools").count(1)

        dc.send(s, seq, "initialize", {"adapterId": "symrepl"})
        r, buf = dc.recv(s, buf)

        # 状态门 S1：未 launch 来 scopes → 1003 拒
        dc.send(s, seq, "scopes", {"frameId": 0})
        r, buf = recv_or_fail(s, buf, "S1 scopes 拒")
        ok = (r is not None and r.get("success") is False
              and code_of(r)[0] == 1003 and "S1" in (code_of(r)[1] or ""))
        check(name + ": S1 scopes 被状态门拒（1003）", ok)

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

        # 两步走前半：threads 取真 tid → stackTrace 取真帧 id（世代票据生产侧）
        dc.send(s, seq, "threads")
        tr, buf = recv_or_fail(s, buf, "threads 取真 id")
        tid = ((tr.get("body") or {}).get("threads") or [{}])[0].get("id") if tr is not None else None
        dc.send(s, seq, "stackTrace", {"threadId": tid})
        st, buf = recv_or_fail(s, buf, "stackTrace 取帧 id")
        fid = ((st.get("body") or {}).get("stackFrames") or [{}])[0].get("id") if st is not None else None
        check(name + ": stackTrace 取真帧 id（int，世代形状）", isinstance(fid, int))

        # scopes：帧 id → Locals 引用（expensive 父面补）
        dc.send(s, seq, "scopes", {"frameId": fid})
        sc, buf = recv_or_fail(s, buf, "scopes 响应")
        scopes = (sc.get("body") or {}).get("scopes") if sc is not None else None
        ok_sc = (sc is not None and sc.get("success") is True and isinstance(scopes, list)
                 and len(scopes) >= 1
                 and all(isinstance(x.get("name"), str)
                         and isinstance(x.get("variablesReference"), int)
                         and x.get("expensive") is False
                         for x in scopes))
        check(name + ": scopes 成臂（Locals 引用形状+expensive=False）", ok_sc)
        ref = scopes[0].get("variablesReference") if ok_sc else None

        # variables：引用 → 平铺变量列表（空列表合法；形状钉死内容不钉）
        dc.send(s, seq, "variables", {"variablesReference": ref})
        vr, buf = recv_or_fail(s, buf, "variables 响应")
        variables = (vr.get("body") or {}).get("variables") if vr is not None else None
        ok_v = (vr is not None and vr.get("success") is True and isinstance(variables, list)
                and all(isinstance(v.get("name"), str) and isinstance(v.get("value"), str)
                        and isinstance(v.get("type"), str)
                        and isinstance(v.get("variablesReference"), int)
                        for v in variables))
        check(name + ": variables 成臂（平铺形状，空列表合法）", ok_v)

        # 负路 stale：世代不符的 frameId / 未登记的引用 → 2002 携 stale_reference
        dc.send(s, seq, "scopes", {"frameId": 1000001})   # 世代 1000 ≠ 当前 0
        r, buf = recv_or_fail(s, buf, "stale frameId 负路")
        ok = (r is not None and r.get("success") is False
              and code_of(r)[0] == 2002 and "stale_reference" in repr(code_of(r)[1]))
        check(name + ": stale frameId 负路（2002+携 stale_reference）", ok)
        dc.send(s, seq, "variables", {"variablesReference": 999999})   # 未登记
        r, buf = recv_or_fail(s, buf, "stale ref 负路")
        ok = (r is not None and r.get("success") is False
              and code_of(r)[0] == 2002 and "stale_reference" in repr(code_of(r)[1]))
        check(name + ": 未登记引用负路（2002+携 stale_reference）", ok)

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
    scopes_chain()
    print("=" * 30)
    print("ALL PASS" if not fails else f"{len(fails)} FAIL: {fails}")
    sys.exit(1 if fails else 0)
#end if
