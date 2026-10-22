#!/usr/bin/env python3
"""probe_stdio — stdio transport（M2-a 收尾件）验收（2026-10-21）。

需求文本=收尾清单"stdio 模式"：DAP 帧走 stdin/stdout，单"连接"=进程即会话：
- P1 握手：initialize → resp success（stdio 帧=socket 同款 framing）
- P2 状态门：S1 来 threads → 1003（协议面在 stdio 下原样）
- P3 真链：launch→configDone→入口 stopped（真 inferior，stdio 下全程不挂）
- P4 收场：disconnect → [resp, terminated]；stdin EOF → 进程 exit=0
- P5 stdio 卫生：stdout 只有 DAP 帧（解析器全程无炸=隐证）；stderr 只有日志行
"""
import os, select, shutil, subprocess, sys, time, json, itertools
from pathlib import Path

SRV_DIR = str(Path(__file__).resolve().parent.parent / "src" / "server")
KT = str(Path(__file__).resolve().parent.parent / "third_party/klee/ktest/get_sign_test000003.ktest")
TG = "/tmp/opencode/replay-build/get_sign"
LLDB_DIR = os.path.dirname(os.path.realpath(shutil.which("lldb")))
fails = []

def check(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        fails.append(name)
    #end if
#end def

def send_msg(proc, seq, command, arguments=None):
    msg = {"seq": seq, "type": "request", "command": command,
           "arguments": arguments or {}}
    body = json.dumps(msg, ensure_ascii=False).encode("utf-8")
    proc.stdin.write(b"Content-Length: %d\r\n\r\n" % len(body) + body)
    proc.stdin.flush()
#end def

class _Buf:
    """子进程 stdout 的带超时帧读取（select+os.read；EOF 大声）。"""
    def __init__(self, proc):
        self.proc = proc
        self.fd = proc.stdout.fileno()
        self.buf = b""
    #end def

    def _fill(self, timeout):
        r, _, _ = select.select([self.fd], [], [], timeout)
        if not r:
            raise TimeoutError("stdio recv timeout")
        #end if
        chunk = os.read(self.fd, 65536)
        if not chunk:
            raise EOFError("stdio EOF")
        #end if
        self.buf += chunk
    #end def

    def recv_msg(self, timeout=15):
        deadline = time.monotonic() + timeout
        while b"\r\n\r\n" not in self.buf:
            self._fill(deadline - time.monotonic())
        #end while
        head, _, rest = self.buf.partition(b"\r\n\r\n")
        length = int(dict(l.split(b":", 1) for l in head.split(b"\r\n")
                          if b":" in l)[b"Content-Length"])
        self.buf = rest
        while len(self.buf) < length:
            self._fill(deadline - time.monotonic())
        #end while
        body, self.buf = self.buf[:length], self.buf[length:]
        return json.loads(body.decode("utf-8"))
    #end def
#end class


def stdio_chain():
    p = subprocess.Popen(
        [sys.executable, "schedule.py", "--stdio",
         "--backend", "lldb", "--lldb-dir", LLDB_DIR],
        cwd=SRV_DIR, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=False)
    try:
        r = _Buf(p)
        seq = itertools.count(1)
        send_msg(p, next(seq), "initialize", {"adapterId": "symrepl"})
        m = r.recv_msg()
        check("P1 握手 initialize → resp success（stdio framing 同款）",
              m.get("type") == "response" and m.get("success") is True)

        send_msg(p, next(seq), "threads")
        m = r.recv_msg()
        check("P2 S1 threads 被状态门拒（1003，协议面原样）",
              m.get("success") is False and (m.get("body") or {}).get("code") == 1003)

        send_msg(p, next(seq), "launch", {"ktest": KT, "target": TG,
                                          "scope": "main,get_sign"})
        m = r.recv_msg()                      # process 事件
        ok = m.get("event") == "process"
        m = r.recv_msg()                      # initialized 事件
        ok = ok and m.get("event") == "initialized"
        send_msg(p, next(seq), "configurationDone")
        kinds = []
        for _ in range(3):                    # 双帧+stopped，宽容序
            m = r.recv_msg()
            kinds.append(m.get("event") or m.get("command"))
        #end for
        ok = ok and kinds.count("stopped") == 1
        check("P3 真链 launch→configDone→入口 stopped（stdio 全程不挂）", ok)

        send_msg(p, next(seq), "disconnect")
        m = r.recv_msg()
        ok = m.get("command") == "disconnect" and m.get("success") is True
        m = r.recv_msg()
        ok = ok and m.get("event") == "terminated"
        check("P4 disconnect → [resp, terminated] 照旧", ok)

        p.stdin.close()                       # stdin EOF = 告别
        code = p.wait(timeout=15)
        check("P4c stdin EOF → 进程 exit=0", code == 0)

        err = p.stderr.read().decode("utf-8", "replace")
        check("P5 stdio 卫生：stderr 全为日志行（无帧泄漏痕迹）",
              all(("frame" not in ln.lower() or "frame_bad" in ln)
                  for ln in err.splitlines()))
    #end try
    finally:
        if p.poll() is None:
            p.terminate(); p.wait(timeout=5)
        #end if
        err = p.stderr.read().decode("utf-8", "replace") if p.stderr else ""
    #end try
    print("---- stderr tail ----")
    print("\n".join(err.splitlines()[-6:]))
#end def


if __name__ == "__main__":
    stdio_chain()
    print("=" * 30)
    print("ALL PASS" if not fails else f"{len(fails)} FAIL: {fails}")
    sys.exit(1 if fails else 0)
#end if
