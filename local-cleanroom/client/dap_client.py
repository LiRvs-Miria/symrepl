import json, socket, subprocess, time
from itertools import count
from pathlib import Path

#: 真饵缺省（手动测试用；探针各自带饵）。
KT_DEFAULT = str(Path(__file__).resolve().parent.parent.parent
                 / "third_party/klee/ktest/get_sign_test000003.ktest")
TG_DEFAULT = "/tmp/opencode/replay-build/get_sign"   # 易失：配方见 tests/conftest.py:23-61

def connect(host="127.0.0.1", port=5678):
    s = socket.create_connection((host, port))
    s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)  # 小帧别被 Nagle 攒着
    return s, b""          # 返回 (socket, 剩余缓冲区)

def disconnect(sock, seq):
    # 不做假设协议栈已经被读完，直接关闭
    sock.settimeout(None)
    sock.shutdown(socket.SHUT_RDWR)   #全关，通知对端我要走了
    sock.close()

def send(sock, seq, command, arguments=None):
    msg = {"seq": next(seq), "type": "request", "command": command,
           "arguments": arguments or {}}
    body = json.dumps(msg, ensure_ascii=False).encode("utf-8")
    sock.sendall(b"Content-Length: %d\r\n\r\n" % len(body) + body)

def recv(sock, buf):
    # 1) 先把帧头读全
    while b"\r\n\r\n" not in buf:
        chunk = sock.recv(4096)
        if not chunk:
            raise EOFError("peer closed mid-header")   # EOF 必须大声：原版在这里 b"" 死旋
        buf += chunk
    #end if
    head, _, buf = buf.partition(b"\r\n\r\n")
    length = int(dict(l.split(b":", 1) for l in head.split(b"\r\n")
                      if b":" in l)[b"Content-Length"])
    # 2) 再把 body 读够（可能已经有一部分在 buf 里）
    while len(buf) < length:
        chunk = sock.recv(length - len(buf))
        if not chunk:
            raise EOFError("peer closed mid-body")
        buf += chunk
    #end if
    body, buf = buf[:length], buf[length:]
    return json.loads(body.decode("utf-8")), buf

def start_server(port=5678, lldb_dir=None, backend="lldb"):
    """起服（fuser 自洁+ss 就绪探测；只查监听不试连——单会话拓扑试连=自杀）。
    lldb_dir=None 时由操作方 PATH 解析一次（client=操作方侧，允许；服务侧零猜测）。"""
    import os, shutil
    subprocess.run(["fuser", "-k", f"{port}/tcp"], capture_output=True)
    time.sleep(0.2)
    if lldb_dir is None:
        which = shutil.which("lldb")
        assert which, "lldb not on PATH — pass lldb_dir explicitly"
        lldb_dir = os.path.dirname(os.path.realpath(which))
    #end if
    srv_dir = Path(__file__).resolve().parent.parent / "server"
    p = subprocess.Popen(["python3", "schedule.py", str(port),
                          "--backend", backend, "--lldb-dir", lldb_dir],
                         cwd=str(srv_dir),
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


def drain(sock, buf, tag, window=3):
    """排空：**接力 buf** 逐帧打印，直到超时/断链。残余缓冲必须接回来——
    同段 TCP 的粘帧就住在里面，丢了=吃帧（2026-10-14 四跑假象的根因）。"""
    sock.settimeout(window)
    n = 0
    try:
        while True:
            m, buf = recv(sock, buf)
            show(m); n += 1
    except socket.timeout:
        print("<- (%s 完毕，共 %d 帧)" % (tag, n))
    except (ConnectionResetError, EOFError) as e:
        print("<- (%s 连接断（%s），共 %d 帧)" % (tag, type(e).__name__, n))
    #end try
    return buf
#end def


class Manual:
    """手动测试驱动器：python3 -i dap_client.py 后 Manual(5678) 逐命令开。
    buf/seq 内部接力；req=一发一收并 show；事件（process/stopped…）不算应答，
    归 drain 或下一次 recv。"""

    def __init__(self, port=5678, host="127.0.0.1", timeout=10):
        self.s, self.buf = connect(host, port)
        self.seq = count(1)
        self.timeout = timeout
    #end def

    def req(self, command, arguments=None, timeout=None):
        """发请求+收**下一帧**并 show——可能是应答，也可能是先到的事件
        （launch 的 process/initialized、放行段的 stopped 常插队）；其余归 drain。"""
        send(self.s, self.seq, command, arguments)
        return self.recv(timeout)
    #end def

    def recv(self, timeout=None):
        self.s.settimeout(timeout or self.timeout)
        m, self.buf = recv(self.s, self.buf)
        show(m)
        return m
    #end def

    def drain(self, tag="drain", window=3):
        """收残帧到超时：逐帧 show，返回帧数；粘帧残余在 self.buf 内部接力。"""
        self.s.settimeout(window)
        n = 0
        try:
            while True:
                m, self.buf = recv(self.s, self.buf)
                show(m); n += 1
        except socket.timeout:
            print(f"<- ({tag} 完毕，共 {n} 帧)")
        except (ConnectionResetError, EOFError) as e:
            print(f"<- ({tag} 连接断（{type(e).__name__}），共 {n} 帧）")
        #end try
        return n
    #end def

    def close(self):
        disconnect(self.s, self.seq)
    #end def
#end class


def manual_chain(port=5678, ktest=KT_DEFAULT, target=TG_DEFAULT):
    """全链代驾：真饵一条龙逐帧打印（起服→init→S1 负路→launch→放行→两步 stackTrace→
    no_thread 负路→disconnect→exit0）。要手动改哪步，把 Manual 用法抄走即可。"""
    p = start_server(port)
    try:
        m = Manual(port)
        m.req("initialize", {"adapterId": "symrepl"})
        m.req("threads")                       # 负路演示：S1 状态门 1003
        m.req("launch", {"ktest": ktest, "target": target})
        m.drain("launch 段（process+initialized）")
        m.req("configurationDone")
        m.drain("放行段（双帧+stopped，宽容序）")
        tid = m.req("threads")["body"]["threads"][0]["id"]   # 真tid=OS tid，两步走
        m.req("stackTrace", {"threadId": tid})
        m.req("stackTrace", {"threadId": 99})  # 负路演示：no_thread→2002
        m.req("disconnect")
        m.drain("收场（resp+terminated）")
        m.close()
        code = p.wait(timeout=20)
        print("server exit =", code)
    finally:
        if p.poll() is None:
            p.terminate(); p.wait(timeout=5)
        #end if
    #end try
#end def


def show(m):
    # 帧打一行：type/command/request_seq 足够认帧，响应 vs 事件一目了然
    if m.get("type") == "response":
        print("<- response cmd=%s req_seq=%s success=%s body=%s message=%s"
              % (m.get("command"), m.get("request_seq"), m.get("success"),
                 m.get("body"), m.get("message")))
    else:
        print("<- %s event=%s body=%s" % (m.get("type"), m.get("event"), m.get("body")))
#end def

if __name__ == "__main__":
    # 手动驱动入口：全矩阵自动跑在 ../probes/。
    #   一键代驾：python3 dap_client.py [port]
    #   交互逐帧：python3 -i dap_client.py   然后见下方两行引导
    import sys
    _port = int(sys.argv[1]) if len(sys.argv) > 1 else 5678
    if "-i" in sys.argv:
        print("交互模式：m = Manual(%d) 起手；m.req('initialize', {'adapterId': 'symrepl'}) 逐命令开" % _port)
    else:
        manual_chain(_port)
    #end if
#end if