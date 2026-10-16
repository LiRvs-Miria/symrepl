"""framing 值 ADT 验收探针（2026-10-15 契约，设计档案 design-notes「framing 重构设计档案」）。

三洞矩阵 + FIN 回归。每案：自起自杀 server → 裸 socket 打字节 → 断言
[自退场退出码 + stderr 含 FrameBadReason 名]。原因名是探针的分类锚点
（契约：stderr 日志必须含 reason 名）。
实现前预期：R-F1/R-F3 不自退场（超时 FAIL）、R-F2/R-F4 原因名不符 FAIL、R-F5 绿（FIN 现状即对）。
回归门：probe_transition_table 14 原样全过（probe_shutdown 8 已随 shutdown 退役删除，2026-10-16）。
"""
import subprocess, sys, socket, time
from pathlib import Path

SRV_DIR = str(Path(__file__).resolve().parent.parent / "server")
PORT = 5692
fails = []

def start(port=PORT):
    subprocess.run(["fuser", "-k", f"{port}/tcp"], capture_output=True)  # 自洁
    time.sleep(0.2)
    p = subprocess.Popen(["python3", "schedule.py", str(port)], cwd=SRV_DIR,
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
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

def blast_and_settle(payload, hold_open=0.0, pin_early_exit=False):
    """裸 socket 打字节；hold_open>0 则发完保持连接。
    pin_early_exit=True 时在 FIN 之前先验 server 已自退（钉'声明即拒不等字节'）。
    返回 (进程退出码, stderr 全文, 早退判定)。"""
    p = start()
    s = socket.create_connection(("127.0.0.1", PORT), timeout=5)
    s.sendall(payload)
    early = None
    if hold_open:
        time.sleep(hold_open)
        if pin_early_exit:
            early = (p.poll() is not None)   # 客户端还挂着，server 必须已退
        #end if
    #end if
    try:
        s.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass
    #end try
    s.close()
    try:
        code = p.wait(timeout=6)
    except subprocess.TimeoutExpired:
        p.terminate(); p.wait()
        code = "not exited"
    #end try
    err = p.stderr.read() if p.stderr else ""
    return code, err, early
#end def

def case(name, payload, want_exit, want_reason, hold_open=0.0, pin_early_exit=False):
    code, err, early = blast_and_settle(payload, hold_open, pin_early_exit)
    if pin_early_exit:
        check(name + "c 声明即拒（FIN 前 server 已自退）", early is True)
    #end if
    check(name, code == want_exit)
    check(name + "b 原因名 " + want_reason, want_reason in err)
#end def

# R-F1: 洞1 巨型声明长度——声明即拒，客户端不发 body 且 socket 保持打开
case("R-F1 巨型长度 LENGTH_OVERSIZE+ABRUPT",
     b"Content-Length: 99999999999\r\n\r\n",
     2, "LENGTH_OVERSIZE", hold_open=2.0, pin_early_exit=True)

# R-F2: 洞2 宽松解析——+12 不得被 int() 吞成合法
case("R-F2 坏格式 LENGTH_FORMAT+ABRUPT",
     b"Content-Length: +12\r\n\r\n",
     2, "LENGTH_FORMAT")

# R-F3: 洞3 头超限——LimitOverrunError 收编，不 crash 不漏连接
case("R-F3 头超限 HEADER_LIMIT+ABRUPT",
     b"A" * 100_000,
     2, "HEADER_LIMIT")

# R-F4: 半帧（声明 100 只到 5 字节就关）
case("R-F4 半帧 STREAM_TRUNCATED+ABRUPT",
     b"Content-Length: 100\r\n\r\nshort",
     2, "STREAM_TRUNCATED")

# R-F5: 干净 FIN → Eof → exit 0（回归锚）
case("R-F5 干净 FIN exit=0", b"", 0, "")

print("=" * 30)
print("ALL PASS" if not fails else f"FAILED: {fails}")
sys.exit(1 if fails else 0)
