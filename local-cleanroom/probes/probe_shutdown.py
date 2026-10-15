"""shutdown 验收探针（2026-10-15 需求转录，设计档案见 design-notes「shutdown 设计档案」）。

矩阵：S0 裸 / S1 init 后 / S2 slot 非空（launch 响应须作废不泄漏）/ S3 放行后。
每案断言：resp（success、无 body）先于 terminated；S2/S3 加 request_seq 对账；
收场断言：server **自退场 exit=0**（会话立刻结束=不等客户端告别，finish() 里验）。
回归门：probe_transition_table.py 12 探针须原样全过（R4 的 `shudown` 拼写必须保持 unknown）。
实现前本探针预期全 RED（shutdown 现走 parse-level "not implemented"）。
"""
import socket, subprocess, sys, time
from itertools import count
from pathlib import Path
sys.path.insert(0, "/home/odot/github/symrepl/local-cleanroom/client")
import dap_client as dc

SRV_DIR = str(Path(__file__).resolve().parent.parent / "server")
KT = "/home/odot/github/symrepl/third_party/klee/ktest/get_sign_test000003.ktest"
TG = "/tmp/opencode/replay-build/get_sign"
PORT = 5691   # 避开 5678/5679（用户手动）/5681（转移表探针）
fails = []

def start(port=PORT):
    subprocess.run(["fuser", "-k", f"{port}/tcp"], capture_output=True)  # 自洁
    time.sleep(0.2)
    p = subprocess.Popen(["python3", "schedule.py", str(port)], cwd=SRV_DIR,
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
#end def

def finish(p, name):
    """收场断言：server 自退场 exit=0（会话立刻结束，不需要客户端告别）。"""
    try:
        code = p.wait(timeout=5)
    except subprocess.TimeoutExpired:
        p.terminate(); p.wait()
        code = "not exited"
    #end try
    check(name, code == 0)
#end def

def bye(s):
    try:
        s.close()
    except Exception:
        pass
#end def

def shutdown_tail_asserts(s, buf, want_req_seq):
    """断言 [shutdown resp(success,无 body,request_seq 对账), terminated]，返回 (ok, buf)。
    EOF/RESET=server 已退场而 terminated 未上线——同样是 terminated 缺位，不是探针故障。"""
    try:
        r, buf = dc.recv(s, buf)
    except socket.timeout:
        print("  （超时：连响应都没来）"); return False, buf
    except (EOFError, ConnectionResetError) as e:
        print(f"  （连接断 {type(e).__name__}：连响应都没来）"); return False, buf
    #end try
    ok1 = (r.get("type") == "response" and r.get("command") == "shutdown"
           and r.get("success") is True and "body" not in r
           and r.get("request_seq") == want_req_seq)
    if not ok1:
        print(f"  resp 形状/对账不符: {r}")
    #end if
    try:
        e, buf = dc.recv(s, buf)
    except (socket.timeout, EOFError, ConnectionResetError) as err:
        print(f"  （terminated 缺位：{type(err).__name__}——server 在二帧前就退场/断链）")
        return False, buf
    #end try
    ok2 = e.get("type") == "event" and e.get("event") == "terminated"
    if not ok2:
        print(f"  terminated 缺位或序错: {e}")
    #end if
    return ok1 and ok2, buf
#end def

# R11: S0 裸连接 shutdown（断路器，无守卫）
p = start(); s, buf = dc.connect("127.0.0.1", PORT); s.settimeout(5); seq = count(1)
dc.send(s, seq, "shutdown")                # 第 1 个请求
ok, buf = shutdown_tail_asserts(s, buf, 1)
check("R11 S0 shutdown 断路器放行（resp 无 body + terminated）", ok)
finish(p, "R11b 自退场 exit=0"); bye(s)

# R12: S1 initialize 之后 shutdown
p = start(); s, buf = dc.connect("127.0.0.1", PORT); s.settimeout(5); seq = count(1)
dc.send(s, seq, "initialize", {"adapterId": "symrepl"})
r, buf = dc.recv(s, buf)
dc.send(s, seq, "shutdown")                # 第 2 个请求
ok, buf = shutdown_tail_asserts(s, buf, 2)
check("R12 S1 shutdown 放行（resp+terminated）", ok)
finish(p, "R12b 自退场 exit=0"); bye(s)

# R13: S2 slot 非空——launch 响应作废，无泄漏
p = start(); s, buf = dc.connect("127.0.0.1", PORT); s.settimeout(5); seq = count(1)
dc.send(s, seq, "initialize", {"adapterId": "symrepl"}); dc.recv(s, buf)
dc.send(s, seq, "launch", {"ktest": KT, "target": TG})   # 第 2 个请求：入 slot，扣响应
dc.send(s, seq, "shutdown")                # 第 3 个请求
ok, buf = shutdown_tail_asserts(s, buf, 3)
check("R13 S2 slot 作废：shutdown resp 先到（无 launch 泄漏）+terminated", ok)
finish(p, "R13b 自退场 exit=0"); bye(s)

# R14: S3 放行后——configDone 两帧（launch 先）照旧，再 shutdown
p = start(); s, buf = dc.connect("127.0.0.1", PORT); s.settimeout(5); seq = count(1)
dc.send(s, seq, "initialize", {"adapterId": "symrepl"}); dc.recv(s, buf)
dc.send(s, seq, "launch", {"ktest": KT, "target": TG})
dc.send(s, seq, "configurationDone")
r1, buf = dc.recv(s, buf); r2, buf = dc.recv(s, buf)
pre = r1.get("command") == "launch" and r2.get("command") == "configurationDone"
if not pre:
    print(f"  放行两帧形状不符: {r1} | {r2}")
#end if
dc.send(s, seq, "shutdown")                # 第 4 个请求
ok, buf = shutdown_tail_asserts(s, buf, 4)
check("R14 S3 shutdown 放行（放行两帧照旧+resp+terminated）", pre and ok)
finish(p, "R14b 自退场 exit=0"); bye(s)

print("=" * 30)
print("ALL PASS" if not fails else f"FAILED: {fails}")
sys.exit(1 if fails else 0)
