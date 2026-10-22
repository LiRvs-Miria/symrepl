#!/usr/bin/env python3
"""probe_trace — branchTrace 预扫描引导回放+报告（M2 收尾件）验收（RED 起步，2026-10-21）。

需求文本=design-notes branchTrace 契约（target=预扫描站点集仅作断点依据；
actual=断点命中据实记录 pc/function/mnemonic/调用栈/符号 value；value 级
一致性=派生注记层；跑末汇总 output 事件=路径线+节点详情；无命令面纯被动）：
- P1 launch 两事件+configDone 放行三帧（入口停，客户端零 setBreakpoints）
- P2 第一次 continue → 停站点（reason=breakpoint——自动插桩证据，客户端未设断）
- P3 第二次 continue → 停下一站点（reason=breakpoint）
- P4 第三次 continue → 收场段 [resp, output(汇总), exited, terminated] 宽容序，
  output 文本形状：含 0x 地址行/<get_sign>（命中帧，勘误：test3 路径不在 main 停）/
  节点行（≥2 行，实测收钉）
- P5 收场链照旧：disconnect → [resp, terminated] → exit0；孤儿不残留
RED 预期（契约+勘误随 breakpoints 砖先例）：自动插桩缺位=第一次 continue 直通
exit，exited/terminated 在 P2 段已被消费——P2/P3/P4a/P4b FAIL（P4a 红因事件
提前消费，非生命周期伤），P1/P5 绿。
"""
import socket, subprocess, time, sys, os, shutil, itertools
from pathlib import Path
import dap_client as dc

SRV_DIR = str(Path(__file__).resolve().parent.parent / "src" / "server")
PORT = 5695
KT = str(Path(__file__).resolve().parent.parent / "third_party/klee/ktest/get_sign_test000003.ktest")
TG = "/tmp/opencode/replay-build/get_sign"
LLDB_DIR = os.path.dirname(os.path.realpath(shutil.which("lldb")))
fails = []

def start(port=PORT):
    subprocess.run(["fuser", "-k", f"{port}/tcp"], capture_output=True)  # 自洁
    time.sleep(0.2)
    p = subprocess.Popen(["python3", "schedule.py", str(port), "--lldb-dir", LLDB_DIR], cwd=SRV_DIR,
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    for _ in range(50):
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

def stop_body(burst_frames):
    stop = next((f for f in burst_frames if is_event(f, "stopped")), None)
    return stop, ((stop or {}).get("body") or {})
#end def

def output_of(burst_frames):
    o = next((f for f in burst_frames if is_event(f, "output")), None)
    return o, ((o or {}).get("body") or {}).get("output")
#end def

def no_orphans():
    return all(not subprocess.run(["pgrep", "-x", n], capture_output=True,
                                  text=True).stdout.strip()
               for n in ("get_sign", "lldb-server"))
#end def


def trace_chain():
    name = "branchTrace 全链"
    p = start()
    try:
        s, buf = dc.connect("127.0.0.1", PORT)
        seq = itertools.count(1)

        dc.send(s, seq, "initialize", {"adapterId": "symrepl"})
        r, buf = dc.recv(s, buf)

        # P1 launch→configDone（客户端零 setBreakpoints——预扫描插桩是引擎的事；
        # scope=插桩范围，逗号分隔函数名）
        dc.send(s, seq, "launch", {"ktest": KT, "target": TG,
                                   "scope": "main,get_sign"})
        e1, buf = recv_or_fail(s, buf, "process 事件")
        e2, buf = recv_or_fail(s, buf, "initialized 事件")
        check(name + ": P1a launch 两事件就位", is_event(e1, "process") and is_event(e2, "initialized"))
        dc.send(s, seq, "configurationDone")
        got = []
        for tag in ("放行帧1", "放行帧2", "放行帧3"):
            f, buf = recv_or_fail(s, buf, tag)
            if f is not None:
                got.append(f)
            #end if
        #end for
        check(name + ": P1b 放行三帧收齐（双帧+stopped）",
              sum(1 for f in got if f.get("type") == "response") == 2
              and any(is_event(f, "stopped") for f in got))

        # P2 第一次 continue → 停站点（自动插桩证据：reason=breakpoint）
        dc.send(s, seq, "continue")
        b1, buf = burst(s, buf, ("P2 突发1", "P2 突发2", "P2 突发3"))
        stop1, sb1 = stop_body(b1)
        check(name + ": P2 停站点1 reason=breakpoint（自动插桩）",
              sb1.get("reason") == "breakpoint")

        # P3 第二次 continue → 停下一站点
        dc.send(s, seq, "continue")
        b2, buf = burst(s, buf, ("P3 突发1", "P3 突发2", "P3 突发3"))
        stop2, sb2 = stop_body(b2)
        check(name + ": P3 停站点2 reason=breakpoint",
              sb2.get("reason") == "breakpoint")

        # P4 第三次 continue → 收场段含汇总 output（宽容序；文本形状）
        dc.send(s, seq, "continue")
        b3, buf = burst(s, buf, ("P4 突发1", "P4 突发2", "P4 突发3", "P4 突发4"))
        print("P4 frames:", [(f.get("type"), f.get("event") or f.get("command"))
                             for f in b3])   # 取证：flaky 排障（收场路竞态嫌疑）
        out_ev, out_text = output_of(b3)
        ei = next((i for i, f in enumerate(b3) if is_event(f, "exited")), None)
        ti = next((i for i, f in enumerate(b3) if is_event(f, "terminated")), None)
        exited_ok = (ei is not None and ti is not None and ei < ti
                     and isinstance((b3[ei].get("body") or {}).get("exitCode"), int))
        check(name + ": P4a exited{exitCode int} 先于 terminated", exited_ok)
        text_ok = (out_ev is not None and isinstance(out_text, str)
                   and "0x" in out_text and "<get_sign>" in out_text
                   and len(out_text.strip().splitlines()) >= 2)
        check(name + ": P4b 汇总 output 事件（路径线+节点行，含 0x 地址/<get_sign>）",
              text_ok)

        # P5 收场链照旧
        dc.send(s, seq, "disconnect")
        r2, buf = recv_or_fail(s, buf, "disconnect resp")
        ok_d = (r2 is not None and r2.get("command") == "disconnect"
                and r2.get("success") is True)
        if ok_d:
            e3, buf = recv_or_fail(s, buf, "terminated 事件")
            ok_d = is_event(e3, "terminated")
        #end if
        check(name + ": P5 disconnect→[resp, terminated] 照旧", ok_d)

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


def bad_ktest_chain():
    """P0 坏 ktest 门禁：父面 E1 解析器校验（原 M2-b1 承诺），坏件 launch 即拒。"""
    name = "坏 ktest launch 门禁"
    bad = Path("/tmp/opencode/bad_probe.ktest")
    bad.write_bytes(b"KTEXX" + b"\x00" * 8)          # magic 不中 → BAD_MAGIC
    p = start()
    try:
        s, buf = dc.connect("127.0.0.1", PORT)
        seq = itertools.count(1)
        dc.send(s, seq, "initialize", {"adapterId": "symrepl"})
        r, buf = dc.recv(s, buf)
        dc.send(s, seq, "launch", {"ktest": str(bad), "target": TG})
        r, buf = recv_or_fail(s, buf, "坏 ktest launch resp")
        ok = (r is not None and r.get("success") is False
              and "ktest refused" in str((r.get("body") or {}).get("detail")))
        check(name + ": 坏件 launch 被父面门禁拒（success False + ktest refused）", ok)
        s.close()
        code = p.wait(timeout=10)
        check(name + ": 会话毕 exit=0", code == 0)
    finally:
        if p.poll() is None:
            p.terminate(); p.wait(timeout=5)
        #end if
    #end try
#end def


if __name__ == "__main__":
    bad_ktest_chain()
    trace_chain()
    print("=" * 30)
    print("ALL PASS" if not fails else f"{len(fails)} FAIL: {fails}")
    sys.exit(1 if fails else 0)
#end if
