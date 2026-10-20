#!/usr/bin/env python3
"""probe_continue — continue（横向与 E4 续刀合流点）验收（RED 起步，2026-10-20）。

需求文本=参考线形状（server.py:621-630 常量体/单源映射 + session.py:330-399 _resume 世代推进
+ :619-629 exited 双事件 + 父面 :769-793 一变二翻译）：
- 状态门：S1 来 continue → 1003 拒（fallback）
- S3 continue → resp success+body.allThreadsContinued True（单源映射）
- 守卫负路：continue 后 stackTrace/再 continue → 2002 携 not_stopped（running/dead 相位皆可）
- 世代失效：continue 后旧帧 id scopes → 2002 携 stale_reference（决策 12 消费侧首次真实触发）
- 生命周期事件：continue→inferior 秒退→exited{exitCode:int} 先+terminated{} 随（父面一变二翻译）
- 收场链照旧：disconnect 对 dead inferior 拆除→[resp, terminated]→exit0；孤儿不残留
泵架构=参考同构 reader 线程+owner 轮询（用户钉）；_MAX_RESUMES 不随迁（有意偏离带因）。
"""
import socket, subprocess, time, sys, os, shutil
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "client"))
import dap_client as dc

SRV_DIR = str(Path(__file__).resolve().parent.parent / "server")
PORT = 5691
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

def no_orphans():
    """收场断言：真 inferior 与 lldb-server 不得残留（close 路契约）。"""
    return all(not subprocess.run(["pgrep", "-x", n], capture_output=True,
                                  text=True).stdout.strip()
               for n in ("get_sign", "lldb-server"))
#end def


def continue_chain():
    name = "continue 全链"
    p = start()
    try:
        s, buf = dc.connect("127.0.0.1", PORT)
        seq = __import__("itertools").count(1)

        dc.send(s, seq, "initialize", {"adapterId": "symrepl"})
        r, buf = dc.recv(s, buf)

        # 状态门 S1：未 launch 来 continue → 1003 拒
        dc.send(s, seq, "continue", {})
        r, buf = recv_or_fail(s, buf, "S1 continue 拒")
        ok = (r is not None and r.get("success") is False
              and code_of(r)[0] == 1003 and "S1" in (code_of(r)[1] or ""))
        check(name + ": S1 continue 被状态门拒（1003）", ok)

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

        # continue：响应与 exited/terminated 事件可能竞争（inferior 秒退），宽容收帧
        dc.send(s, seq, "continue", {})
        burst = []
        for i in range(3):
            f, buf = recv_or_fail(s, buf, f"continue 突发{i+1}", window=5)
            if f is not None:
                burst.append(f)
            #end if
        #end for
        cont = next((f for f in burst if f.get("type") == "response"
                     and f.get("command") == "continue"), None)
        check(name + ": continue 响应（success+allThreadsContinued True）",
              cont is not None and cont.get("success") is True
              and (cont.get("body") or {}).get("allThreadsContinued") is True)
        ei = next((i for i, f in enumerate(burst) if is_event(f, "exited")), None)
        ti = next((i for i, f in enumerate(burst) if is_event(f, "terminated")), None)
        check(name + ": exited{exitCode int} 先于 terminated{}（父面一变二）",
              ei is not None and ti is not None and ei < ti
              and isinstance((burst[ei].get("body") or {}).get("exitCode"), int))

        # 守卫负路：continue 后 stackTrace → not_stopped（running/dead 相位皆可，不钉相位名）
        dc.send(s, seq, "stackTrace", {"threadId": 1})
        r, buf = recv_or_fail(s, buf, "continue 后 stackTrace 负路")
        ok = (r is not None and r.get("success") is False
              and code_of(r)[0] == 2002 and "not_stopped" in repr(code_of(r)[1]))
        check(name + ": continue 后 stackTrace 负路（2002+not_stopped）", ok)

        # continue 自身守卫：再 continue → not_stopped
        dc.send(s, seq, "continue", {})
        r, buf = recv_or_fail(s, buf, "再 continue 负路")
        ok = (r is not None and r.get("success") is False
              and code_of(r)[0] == 2002 and "not_stopped" in repr(code_of(r)[1]))
        check(name + ": 再 continue 负路（2002+not_stopped）", ok)

        # 世代失效负路：continue 前的帧 id（gen0）——参考守卫序=相位先于世代解码，
        # continue 后（inferior 秒退）phase dead → not_stopped 先答；
        # stale_reference 待 setBreakpoints 砖产出第二次停相后真实触发
        dc.send(s, seq, "scopes", {"frameId": 0})
        r, buf = recv_or_fail(s, buf, "世代失效负路")
        ok = (r is not None and r.get("success") is False
              and code_of(r)[0] == 2002 and "not_stopped" in repr(code_of(r)[1]))
        check(name + ": 旧帧 id 负路（2002+not_stopped，相位先于世代解码）", ok)

        # 收场链照旧（disconnect 对 dead inferior 拆除）
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
    continue_chain()
    print("=" * 30)
    print("ALL PASS" if not fails else f"{len(fails)} FAIL: {fails}")
    sys.exit(1 if fails else 0)
#end if
