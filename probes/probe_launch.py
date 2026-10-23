#!/usr/bin/env python3
"""probe_launch — 砖2 launch 接线验收（RED 起步，2026-10-16）。

需求文本=design-notes「砖2 先验攻防定稿」：
- L1 好单全链：initialize→caps；launch(好参)→[Event process(带 pid), Event initialized]
  （顺序钉死，先验①）→configurationDone→{launch resp(body{}), configDone resp, Event stopped}
  （launch resp 先于 configDone resp；stopped 与双帧不钉相对序——reader/handle 双路真竞争，D3 吃 seq）
  →disconnect→[resp, terminated]→会话毕 exit0
- L2 废单不惊动：launch(坏参)→ErrReply（无事件、留 S1）→重递好单→**仅一对**事件→configDone 放行
桩 pid=自身真 pid（先验④代码先行裁），探针钉形状不钉值。
"""
import socket, subprocess, time, sys, os, shutil
from pathlib import Path
import dap_client as dc

SRV_DIR = str(Path(__file__).resolve().parent.parent / "src" / "server")
PORT = 5683
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
    """收一帧；超时=没等到（返回 (None, buf)）。窗口 15s：真 LLDB launch 1-3s。"""
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


def L1_happy_path():
    name = "L1 好单全链"
    p = start()
    try:
        s, buf = dc.connect("127.0.0.1", PORT)
        seq = __import__("itertools").count(1)

        dc.send(s, seq, "initialize", {"adapterId": "symrepl"})
        r, buf = dc.recv(s, buf)
        check(name + ": initialize 回 caps", r.get("success") is True
              and r.get("body", {}).get("supportsConfigurationDoneRequest") is True)

        dc.send(s, seq, "launch", {"ktest": KT, "target": TG})
        e1, buf = recv_or_fail(s, buf, "process 事件")
        ok1 = is_event(e1, "process") and isinstance(
            (e1.get("body") or {}).get("systemProcessId"), int)
        check(name + ": process 事件带 pid 形状", ok1)
        if ok1:
            e2, buf = recv_or_fail(s, buf, "initialized 事件")
            check(name + ": initialized 事件随后", is_event(e2, "initialized"))
        #end if

        dc.send(s, seq, "configurationDone")
        # 砖3：放行三帧收齐再验——launch resp 字节必先于 configDone resp（同路顺序写），
        # stopped 走 reader 路与双帧（handle 路）有真竞争，不钉相对序
        got = []
        for tag in ("放行帧1", "放行帧2", "放行帧3"):
            f, buf = recv_or_fail(s, buf, tag)
            if f is not None:
                got.append(f)
            #end if
        #end for
        launch_i = next((i for i, f in enumerate(got)
                         if f.get("type") == "response" and f.get("command") == "launch"), None)
        config_i = next((i for i, f in enumerate(got)
                         if f.get("type") == "response" and f.get("command") == "configurationDone"), None)
        stop_i = next((i for i, f in enumerate(got)
                       if f.get("type") == "event" and f.get("event") == "stopped"), None)
        ok_r1 = (launch_i is not None and got[launch_i].get("success") is True
                 and got[launch_i].get("body") == {})
        check(name + ": 放行含 launch resp(body={})", ok_r1)
        ok_r2 = (config_i is not None and got[config_i].get("success") is True)
        check(name + ": 放行含 configDone resp", ok_r2)
        check(name + ": 双帧定序 launch 先于 configDone",
              None not in (launch_i, config_i) and launch_i < config_i)
        sb = (got[stop_i].get("body") or {}) if stop_i is not None else {}
        check(name + ": stopped 事件（reason/threadId/allThreadsStopped 形状）",
              stop_i is not None and isinstance(sb.get("reason"), str)
              and isinstance(sb.get("threadId"), int)
              and isinstance(sb.get("allThreadsStopped"), bool))

        dc.send(s, seq, "disconnect")
        r3, buf = recv_or_fail(s, buf, "disconnect resp")
        ok_d = (r3 is not None and r3.get("command") == "disconnect"
                and r3.get("success") is True)
        if ok_d:
            e3, buf = recv_or_fail(s, buf, "terminated 事件")
            ok_d = is_event(e3, "terminated")
        #end if
        check(name + ": disconnect→[resp, terminated]", ok_d)

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


def L2_bad_order_no_startle():
    name = "L2 废单不惊动"
    p = start()
    try:
        s, buf = dc.connect("127.0.0.1", PORT)
        seq = __import__("itertools").count(1)

        dc.send(s, seq, "initialize", {"adapterId": "symrepl"})
        r, buf = dc.recv(s, buf)

        dc.send(s, seq, "launch", {"target": "t"})       # 缺 ktest：废单
        r, buf = dc.recv(s, buf)
        bad = (r.get("success") is False and r.get("command") == "launch"
               and isinstance((r.get("body") or {}).get("code"), int)
               and bool(r.get("message")))
        check(name + ": 废单回 ErrReply(粗类码+message)", bad)

        try:                                             # 废单后静默窗口：无事件
            s.settimeout(0.6)
            ghost, buf = dc.recv(s, buf)
            check(name + ": 废单不触发任何事件", False)
            return
        except socket.timeout:
            check(name + ": 废单不触发任何事件", True)
        #end try

        dc.send(s, seq, "launch", {"ktest": KT, "target": TG})   # 重递好单
        e1, buf = recv_or_fail(s, buf, "process 事件")
        if not is_event(e1, "process"):
            check(name + ": 重递好单→process 事件", False)
            return
        #end if
        e2, buf = recv_or_fail(s, buf, "initialized 事件")
        check(name + ": 重递好单→仅一对事件(process,initialized)",
              is_event(e2, "initialized"))

        dc.send(s, seq, "configurationDone")
        # 砖3：放行帧+stopped 共三帧竞争（同 L1），只验双帧都在且 launch 先
        got = []
        for tag in ("放行帧1", "放行帧2", "放行帧3"):
            f, buf = recv_or_fail(s, buf, tag)
            if f is not None:
                got.append(f)
            #end if
        #end for
        launch_i = next((i for i, f in enumerate(got)
                         if f.get("type") == "response" and f.get("command") == "launch"), None)
        config_i = next((i for i, f in enumerate(got)
                         if f.get("type") == "response" and f.get("command") == "configurationDone"), None)
        check(name + ": configDone 放行双帧照旧（launch 先）",
              None not in (launch_i, config_i) and launch_i < config_i)
        s.close()
    finally:
        if p.poll() is None:
            p.terminate(); p.wait(timeout=5)
        #end if
    #end try
#end def


if __name__ == "__main__":
    L1_happy_path()
    L2_bad_order_no_startle()
    print("=" * 30)
    print("ALL PASS" if not fails else f"{len(fails)} FAIL: {fails}")
    sys.exit(1 if fails else 0)
#end if
