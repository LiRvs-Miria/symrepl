import socket, json, subprocess, time, signal, sys, os, shutil
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "client"))
import dap_client as dc

SRV_DIR = str(Path(__file__).resolve().parent.parent / "server")
KT = str(Path(__file__).resolve().parent.parent.parent / "third_party/klee/ktest/get_sign_test000003.ktest")
TG = "/tmp/opencode/replay-build/get_sign"
LLDB_DIR = os.path.dirname(os.path.realpath(shutil.which("lldb")))   # 操作方侧解析一次，服务零猜测
fails = []

def start(port=5681):
    subprocess.run(["fuser","-k",f"{port}/tcp"], capture_output=True)  # 自洁：前轮泄漏僵尸会毒害本轮
    time.sleep(0.2)
    p = subprocess.Popen(["python3","schedule.py",str(port),"--lldb-dir",LLDB_DIR], cwd=SRV_DIR,
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    for _ in range(50):              # 就绪等待：只查监听不碰会话（单会话拓扑下试探连接=自杀）
        r = subprocess.run(["ss", "-tln"], capture_output=True, text=True)
        if f":{port} " in r.stdout:
            return p
        if p.poll() is not None:
            raise RuntimeError("server died at boot:\n" + p.stdout.read())
        time.sleep(0.1)
    #end for
    raise RuntimeError("server never listened")
#end def

def session(port=5681):
    s, buf = dc.connect("127.0.0.1", port); s.settimeout(10)   # 真 LLDB launch 1-3s，窗口放宽
    return s, buf, __import__("itertools").count(1)

def one(seq_msg):  # 发一帧收一帧
    s, buf, seq = seq_msg
    return s

def code_of(resp):
    b = resp.get("body") or {}
    return b.get("code"), b.get("detail")

def check(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond: fails.append(name)

# R1: S0 launch -> 状态错误 1003
p = start(); s, buf, seq = session()
dc.send(s, seq, "launch", {"ktest": KT, "target": TG})
r, buf = dc.recv(s, buf)
check("R1 S0 launch 被状态门拒", r["success"] is False and code_of(r)[0] == 1003 and "S0" in code_of(r)[1])
s.close(); p.terminate(); p.wait()

# R2: S0 initialize -> caps, 进 S1；R3: S1 initialize -> 拒（纯洁性）；R4: S1 未知命令 -> not implemented
p = start(); s, buf, seq = session()
dc.send(s, seq, "initialize", {"adapterId": "symrepl"})
r, buf = dc.recv(s, buf)
check("R2 S0 initialize 回 caps", r["success"] is True and r["body"].get("supportsConfigurationDoneRequest") is True)
dc.send(s, seq, "initialize", {"adapterId": "symrepl"})
r, buf = dc.recv(s, buf)
check("R3 S1 initialize 重入拒", r["success"] is False and code_of(r)[0] == 1003)
dc.send(s, seq, "shudown")
r, buf = dc.recv(s, buf)
check("R4 未知命令回 not implemented", r["success"] is False and r["message"] == "not implemented")
s.close(); p.terminate(); p.wait()

# R5: S1 launch 坏参数 -> 1001；R6: 好单 -> 两事件广播；R7: S2 双发 -> launch_already_pending
p = start(); s, buf, seq = session()
dc.send(s, seq, "initialize", {"adapterId": "symrepl"}); dc.recv(s, buf)
dc.send(s, seq, "launch", {"ktest": KT})
r, buf = dc.recv(s, buf)
check("R5 S1 launch 缺 target -> 1001", r["success"] is False and code_of(r)[0] == 1001)
# R6（砖2 翻案）: launch 好单→壳履约→两事件广播（process 带 pid 先，initialized 随后）；
# launch 响应仍扣着（R8 验放行）
dc.send(s, seq, "launch", {"ktest": KT, "target": TG})
e1, buf = dc.recv(s, buf)
e2, buf = dc.recv(s, buf)
check("R6 launch 好单→两事件（process 带 pid 先，initialized 随后）",
      e1.get("type") == "event" and e1.get("event") == "process"
      and isinstance((e1.get("body") or {}).get("systemProcessId"), int)
      and e2.get("type") == "event" and e2.get("event") == "initialized")
dc.send(s, seq, "launch", {"ktest": KT, "target": TG})
r, buf = dc.recv(s, buf)
check("R7 S2 双发拒 launch_already_pending", r["success"] is False and "launch_already_pending" in code_of(r)[1])

# R8: configurationDone -> 放行帧（launch 响应先）；砖3 起 stopped 事件同场竞争（三帧收齐验）
dc.send(s, seq, "configurationDone")
got = []
for _ in range(3):
    f, buf = dc.recv(s, buf)
    got.append(f)
#end for
li = next((i for i, f in enumerate(got) if f.get("command") == "launch"), None)
ci = next((i for i, f in enumerate(got) if f.get("command") == "configurationDone"), None)
check("R8 放行两帧且 launch 响应先", None not in (li, ci) and li < ci
      and got[li]["success"] and got[ci]["success"])
dc.send(s, seq, "disconnect", {"terminateDebuggee": True})
r3, buf = dc.recv(s, buf)
e, buf = dc.recv(s, buf)
s.shutdown(socket.SHUT_RDWR); s.close(); time.sleep(0.4)
check("R9 disconnect 成功响应", r3["success"] and r3["body"] == {})
check("R9e 收场事件 terminated 随响应", e.get("type") == "event" and e.get("event") == "terminated")
check("R9b 会话结束进程 exit=0", p.poll() == 0)

# R10: S0 直接 disconnect（断路器）-> 成功 + terminated + exit 0
p = start(); s, buf, seq = session()
dc.send(s, seq, "disconnect", {"terminateDebuggee": True})
r, buf = dc.recv(s, buf)
e, buf = dc.recv(s, buf)
s.shutdown(socket.SHUT_RDWR); s.close(); time.sleep(0.4)
check("R10 断路器：S0 disconnect 无守卫成功", r["success"] and r["body"] == {})
check("R10e 收场事件 terminated 随响应", e.get("type") == "event" and e.get("event") == "terminated")
check("R10b exit=0", p.poll() == 0)

print("=" * 30)
print("ALL PASS" if not fails else f"FAILED: {fails}")
