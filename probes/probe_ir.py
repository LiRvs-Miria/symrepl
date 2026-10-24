#!/usr/bin/env python3
"""probe_ir — M3 launch 参数 ir+trace 注记层+源码归因验收（RED 2026-10-24）。

需求文本=ADR-0004 裁决 3+design-notes「M3 路径缩小」节+issues #15/#18：
- launch 可选参数 ir（.ll 路径）：缺席=现状零增帧（M1+M2 层）；坏 .ll=launch
  期 Rejected（ktest refused 家法 fail-fast，spawn 前）
- 在场=trace 报告注记层：节点携 kind: "fork"|"runtime"（二值）+ detail 携判据
  symbolic-branch / function-not-in-module / concrete-condition（A/B 细分住
  detail）；注记=派生层，记录节点保持据实
- 行级 join 键=(函数名, 行)：节点补 line（LineEntry 据实捕获）
- 源码归因（#15）：节点携 file/column（LineEntry 据实），报告文本行渲染
  `@ <file>:<line>`（空路径省略）
- 噪声A 活证：scope=module 时 kleeRuntest 运行时函数命中 → function-not-in-module

网链：
- bad_ir_chain：垃圾 .ll / 不存在路径 → launch 拒（ir refused），会话干净收场
- ir_trace_chain：ir=get_sign.ll + scope=main,get_sign → 收场报告节点全
  fork/symbolic-branch，line 集 ⊇ {11,13}，file/column 归因齐，文本携 `@ …:11`
- no_ir_chain：同链无 ir → 报告节点在（M2 形状）但零 kind 字段（零增帧）
- noise_a_chain：ir + scope=module → 至少一节点 function-not-in-module，
  get_sign 节点照旧 fork
- two_objs_chain（#15 验收）：两 fixtures 源码对齐——main 节点 file=two_objs.c、
  line 集={11}（源码 if 行）

RED 预期（ir/归因全链缺位）：bad_ir 2 FAIL + ir_trace 5 FAIL + noise_a 3 FAIL +
two_objs 4 FAIL；no_ir 全 PASS（现状即契约）。
"""
import socket, subprocess, time, sys, os, shutil, itertools, tempfile
from pathlib import Path
import dap_client as dc

SRV_DIR = str(Path(__file__).resolve().parent.parent / "src" / "server")
ROOT = Path(__file__).resolve().parent.parent
PORT = 5705
KT = str(ROOT / "third_party/klee/ktest/get_sign_test000003.ktest")
KT2 = str(ROOT / "third_party/klee/ktest/two_objs_test000001.ktest")
LLDB_DIR = os.path.dirname(os.path.realpath(shutil.which("lldb")))
WORK = Path(tempfile.mkdtemp(prefix="probe-ir-"))
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

def report_of(burst_frames):
    """收场 output 事件 body（output 文本 + report 节点表）。"""
    for f in burst_frames:
        if is_event(f, "output"):
            return f.get("body") or {}
        #end if
    #end for
    return None
#end def

def no_orphans():
    return all(not subprocess.run(["pgrep", "-x", n], capture_output=True,
                                  text=True).stdout.strip()
               for n in ("get_sign", "two_objs", "lldb-server"))
#end def

def make_fixtures():
    """clang 同配方现场做：两靶二进制+两 .ll（conftest 家法；易失自建）。"""
    clang = shutil.which("clang")
    if clang is None:
        print("SKIP all (clang not available)")
        print("=" * 30)
        print("ALL PASS" if not fails else f"{len(fails)} FAIL: {fails}")
        sys.exit(0)
    #end if
    inc = ROOT / "third_party" / "klee" / "runtime" / "include"
    work_bin = WORK / "bin"
    work_bin.mkdir(exist_ok=True)
    objects = []
    for src, cc, extra in ((ROOT / "third_party/klee/runtime/intrinsics.c", clang, []),
                           (ROOT / "third_party/klee/runtime/KTest.cpp", "clang++", ["-std=c++17"])):
        obj = work_bin / (src.stem + ".o")
        proc = subprocess.run([cc, "-O0", "-g", *extra, "-c", str(src), f"-I{inc}", "-o", str(obj)],
                              capture_output=True, text=True)
        if proc.returncode != 0:
            raise RuntimeError(f"{cc} failed: " + proc.stderr[:200])
        #end if
        objects.append(obj)
    #end for
    for name, src in (("get_sign", ROOT / "tests/fixtures/src/get_sign.c"),
                      ("two_objs", ROOT / "tests/fixtures/src/two_objs.c")):
        proc = subprocess.run(
            [clang, "-O0", "-g", "-S", "-emit-llvm", f"-I{inc}", str(src),
             "-o", str(WORK / f"{name}.ll")], capture_output=True, text=True)
        if proc.returncode != 0:
            raise RuntimeError("clang -emit-llvm failed: " + proc.stderr[:200])
        #end if
        proc = subprocess.run(
            [clang, "-O0", "-g", str(src), f"-I{inc}", *(str(o) for o in objects),
             "-o", str(work_bin / name)], capture_output=True, text=True)
        if proc.returncode != 0:
            raise RuntimeError("clang link failed: " + proc.stderr[:200])
        #end if
    #end for
    garbage = WORK / "garbage.ll"
    garbage.write_text("not llvm at all\njust text\n")
    return WORK / "bin" / "get_sign", WORK / "bin" / "two_objs", garbage
#end def

def drain_until_stop(s, buf, window=3, idle_rounds=2, max_frames=64):
    """连 recv 直到 stopped/exited 事件或连空两轮；回 (兴趣帧|None, 收到的帧, buf)。
    定长 burst 在 scope=module 长链上会白等烧窗（RED 实证 240s 挂）——兴趣驱动。"""
    got, miss = [], 0
    while miss < idle_rounds and len(got) < max_frames:
        f, buf = recv_or_fail(s, buf, "drain", window=window)
        if f is None:
            miss += 1
            continue
        #end if
        miss = 0
        got.append(f)
        if is_event(f, "stopped") or is_event(f, "exited"):
            return f, got, buf
        #end if
    #end while
    return None, got, buf
#end def

def run_to_report(s, buf, seq, launches, max_cont=500):
    """launch→configDone→连 continue 到收场，回 (收场 output body|None, buf)。"""
    dc.send(s, seq, "launch", launches)
    recv_or_fail(s, buf, "process")
    recv_or_fail(s, buf, "initialized")
    dc.send(s, seq, "configurationDone")
    f, b, buf = drain_until_stop(s, buf, window=15)   # 入口停（含 launch resp 双帧）
    for i in range(max_cont):
        dc.send(s, seq, "continue")
        f, b, buf = drain_until_stop(s, buf)
        exited = next((x for x in b if is_event(x, "exited")), None)
        if exited is not None:
            return report_of(b), buf
        #end if
        if (f is None or not is_event(f, "stopped")
                or (f.get("body") or {}).get("reason") != "breakpoint"):
            return report_of(b), buf                  # 非停相推进=异常路，如实回
        #end if
    #end for
    return None, buf
#end def

def finish(s, buf, p, name, seq):
    """收场链照旧：disconnect→[resp, terminated]→exit0→无孤儿。"""
    dc.send(s, seq, "disconnect")
    r, buf = recv_or_fail(s, buf, "disconnect resp")
    recv_or_fail(s, buf, "terminated 事件")           # 收完告别帧再断（probe_trace P5 家法）
    s.close()
    code = p.wait(timeout=10)
    check(name + ": 会话毕 exit=0", code == 0)
#end def

def bad_ir_chain(garbage):
    """坏 .ll 门禁：launch 期 Rejected（ktest refused 家法；spawn 前 fail-fast）。"""
    name = "坏 ir launch 门禁"
    p = start()
    try:
        s, buf = dc.connect("127.0.0.1", PORT)
        seq = itertools.count(1)
        dc.send(s, seq, "initialize", {"adapterId": "symrepl"})
        r, buf = dc.recv(s, buf)
        dc.send(s, seq, "launch", {"ktest": KT,
                                   "target": str(WORK / "bin" / "get_sign"),
                                   "ir": str(garbage)})
        r, buf = recv_or_fail(s, buf, "垃圾 ir resp")
        ok = (r is not None and r.get("success") is False
              and "ir refused" in str((r.get("body") or {}).get("detail")
                                       or (r.get("error") or {}).get("message")))
        check(name + ": 垃圾 .ll launch 被拒（success False + ir refused）", ok)
        dc.send(s, seq, "launch", {"ktest": KT, "target": str(WORK / "bin" / "get_sign"),
                                   "ir": str(WORK / "no-such.ll")})
        r, buf = recv_or_fail(s, buf, "不存在 ir resp")
        ok = (r is not None and r.get("success") is False
              and "ir refused" in str((r.get("body") or {}).get("detail")
                                       or (r.get("error") or {}).get("message")))
        check(name + ": 不存在路径 launch 被拒（ir refused）", ok)
        s.close()
        code = p.wait(timeout=10)
        check(name + ": 会话毕 exit=0", code == 0)
    finally:
        if p.poll() is None:
            p.terminate(); p.wait(timeout=5)
        #end if
    #end try
#end def


def ir_trace_chain(ll):
    """好件注记+归因：节点全 fork/symbolic-branch，line ⊇ {11,13}，file/column 齐，
    文本携 `@ …get_sign.c:11`。"""
    name = "ir 注记链"
    p = start()
    try:
        s, buf = dc.connect("127.0.0.1", PORT)
        seq = itertools.count(1)
        dc.send(s, seq, "initialize", {"adapterId": "symrepl"})
        r, buf = dc.recv(s, buf)
        report, buf = run_to_report(
            s, buf, seq, {"ktest": KT, "target": str(WORK / "bin" / "get_sign"),
                          "scope": "main,get_sign", "ir": str(ll)})
        nodes = (report or {}).get("report") or []
        text = (report or {}).get("output") or ""
        check(name + ": 跑到收场且报告节点在手",
              report is not None and isinstance(nodes, list) and len(nodes) >= 2)
        ok = bool(nodes) and all(n.get("kind") == "fork"
                                 and n.get("detail") == "symbolic-branch"
                                 for n in nodes)
        check(name + ": 节点全 kind=fork detail=symbolic-branch", ok)
        lines = {n.get("line") for n in nodes}
        check(name + ": line 集 ⊇ {11,13}（行级 join 真线）", {11, 13} <= lines)
        check(name + ": 节点全 file 归因（*get_sign.c）",
              bool(nodes) and all(str(n.get("file", "")).endswith("get_sign.c")
                                  for n in nodes))
        check(name + ": 节点全 column ≥1", bool(nodes) and all(n.get("column", 0) >= 1
                                                               for n in nodes))
        check(name + ": 报告文本携 @ <file>:<line>（get_sign.c:11）",
              "get_sign.c:11" in text and "@" in text)
        finish(s, buf, p, name, seq)
        check(name + ": 无孤儿 inferior/lldb-server", no_orphans())
        if fails:
            print("  nodes:", nodes)
        #end if
    finally:
        if p.poll() is None:
            p.terminate(); p.wait(timeout=5)
        #end if
    #end try
#end def


def no_ir_chain():
    """零增帧：同链无 ir——报告节点在（M2 形状），零 kind 字段。"""
    name = "无 ir 零增帧"
    p = start()
    try:
        s, buf = dc.connect("127.0.0.1", PORT)
        seq = itertools.count(1)
        dc.send(s, seq, "initialize", {"adapterId": "symrepl"})
        r, buf = dc.recv(s, buf)
        report, buf = run_to_report(
            s, buf, seq, {"ktest": KT, "target": str(WORK / "bin" / "get_sign"),
                          "scope": "main,get_sign"})
        nodes = (report or {}).get("report") or []
        check(name + ": 跑到收场且报告节点在（M2 形状不回归）",
              report is not None and isinstance(nodes, list) and len(nodes) >= 2)
        check(name + ": 节点零 kind 字段（M1+M2 层零增帧）",
              all("kind" not in n for n in nodes))
        finish(s, buf, p, name, seq)
    finally:
        if p.poll() is None:
            p.terminate(); p.wait(timeout=5)
        #end if
    #end try
#end def


def noise_a_chain(ll):
    """噪声A 活证：scope=module——运行时函数（kleeRuntest 世界）命中节点
    function-not-in-module；get_sign 节点照旧 fork。"""
    name = "噪声A 活证链"
    p = start()
    try:
        s, buf = dc.connect("127.0.0.1", PORT)
        seq = itertools.count(1)
        dc.send(s, seq, "initialize", {"adapterId": "symrepl"})
        r, buf = dc.recv(s, buf)
        report, buf = run_to_report(
            s, buf, seq, {"ktest": KT, "target": str(WORK / "bin" / "get_sign"),
                          "scope": "module", "ir": str(ll)})
        nodes = (report or {}).get("report") or []
        check(name + ": 跑到收场且报告节点在手（超集路径）",
              report is not None and isinstance(nodes, list) and len(nodes) >= 2)
        details = {n.get("detail") for n in nodes}
        check(name + ": 至少一节点 function-not-in-module（噪声A 活证）",
              "function-not-in-module" in details)
        ok = all(n.get("kind") in ("fork", "runtime") and bool(n.get("detail"))
                 for n in nodes)
        check(name + ": 全节点 kind 二值+detail 三判据（封闭）", ok)
        gs = [n for n in nodes if n.get("function") == "get_sign"]
        check(name + ": get_sign 节点照旧 fork/symbolic-branch",
              bool(gs) and all(n.get("kind") == "fork" for n in gs))
        finish(s, buf, p, name, seq)
        if fails:
            print("  node details:", {n.get("function"): n.get("detail")
                                      for n in nodes})
        #end if
    finally:
        if p.poll() is None:
            p.terminate(); p.wait(timeout=5)
        #end if
    #end try
#end def


def truth_chain():
    """#18 验收真值（ADR-0004 裁决 6）：三 ktest 全对账——
    test1（exit 0）lines={11}（L13 早退不出现）；test2（exit 1）/test3（exit 255）
    lines={11,13}；节点全 fork/symbolic-branch。每 ktest 一新会话（收场即终态）。"""
    name = "验收真值链"
    truth = (("test000001", 0, {11}),
             ("test000002", 1, {11, 13}),
             ("test000003", 255, {11, 13}))
    p = None
    try:
        for tag, want_rc, want_lines in truth:
            p = start()
            s, buf = dc.connect("127.0.0.1", PORT)
            seq = itertools.count(1)
            dc.send(s, seq, "initialize", {"adapterId": "symrepl"})
            r, buf = dc.recv(s, buf)
            kt = str(ROOT / f"third_party/klee/ktest/get_sign_{tag}.ktest")
            dc.send(s, seq, "launch", {"ktest": kt,
                                       "target": str(WORK / "bin" / "get_sign"),
                                       "scope": "main,get_sign",
                                       "ir": str(WORK / "get_sign.ll")})
            recv_or_fail(s, buf, "process")
            recv_or_fail(s, buf, "initialized")
            dc.send(s, seq, "configurationDone")
            drain_until_stop(s, buf, window=15)      # 入口停
            report, exited = None, None
            for i in range(500):
                dc.send(s, seq, "continue")
                f, b, buf = drain_until_stop(s, buf)
                exited = next((x for x in b if is_event(x, "exited")), None)
                if exited is not None:
                    report = report_of(b)
                    break
                #end if
                if (f is None or not is_event(f, "stopped")
                        or (f.get("body") or {}).get("reason") != "breakpoint"):
                    break
                #end if
            #end for
            nodes = (report or {}).get("report") or []
            code = (exited.get("body") or {}).get("exitCode") if exited else None
            lines = {n.get("line") for n in nodes}
            kinds_ok = bool(nodes) and all(n.get("kind") == "fork"
                                           and n.get("detail") == "symbolic-branch"
                                           for n in nodes)
            check(f"{name}: {tag} exitCode={want_rc}", code == want_rc)
            check(f"{name}: {tag} lines={sorted(want_lines)}", lines == want_lines)
            check(f"{name}: {tag} 节点全 fork/symbolic-branch", kinds_ok)
            s.close()
            code = p.wait(timeout=10)
            check(f"{name}: {tag} 会话毕 exit=0", code == 0)
            p = None
        #end for
    finally:
        if p is not None and p.poll() is None:
            p.terminate(); p.wait(timeout=5)
        #end if
    #end try
#end def


def two_objs_chain(two_bin):
    """#15 验收（两 fixtures 源码对齐）：main 节点 file=two_objs.c、line 集={11}、
    column ≥1（if 行归因）。"""
    name = "two_objs 归因链"
    p = start()
    try:
        s, buf = dc.connect("127.0.0.1", PORT)
        seq = itertools.count(1)
        dc.send(s, seq, "initialize", {"adapterId": "symrepl"})
        r, buf = dc.recv(s, buf)
        report, buf = run_to_report(
            s, buf, seq, {"ktest": KT2, "target": str(two_bin), "scope": "main"})
        nodes = (report or {}).get("report") or []
        check(name + ": 跑到收场且报告节点在手", report is not None and len(nodes) >= 1)
        check(name + ": 节点全 function=main",
              bool(nodes) and all(n.get("function") == "main" for n in nodes))
        check(name + ": 节点全 file 归因（*two_objs.c）",
              bool(nodes) and all(str(n.get("file", "")).endswith("two_objs.c")
                                  for n in nodes))
        check(name + ": line 集={11}（源码 if 行对齐）",
              bool(nodes) and {n.get("line") for n in nodes} == {11})
        check(name + ": 节点全 column ≥1", bool(nodes) and all(n.get("column", 0) >= 1
                                                               for n in nodes))
        finish(s, buf, p, name, seq)
        if fails:
            print("  nodes:", nodes)
        #end if
    finally:
        if p.poll() is None:
            p.terminate(); p.wait(timeout=5)
        #end if
    #end try
#end def


if __name__ == "__main__":
    get_bin, two_bin, garbage = make_fixtures()
    bad_ir_chain(garbage)
    ir_trace_chain(WORK / "get_sign.ll")
    no_ir_chain()
    noise_a_chain(WORK / "get_sign.ll")
    two_objs_chain(two_bin)
    truth_chain()
    print("=" * 30)
    print("ALL PASS" if not fails else f"{len(fails)} FAIL: {fails}")
    sys.exit(1 if fails else 0)
#end if
