import json, socket
from itertools import count

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

def testInitAfterLaunch():
    sock, buf = connect("127.0.0.1", 5678)
    seq = count(1)
    print("start a test launch after init...")
    send(sock, seq, "launch")
    resp, buf = recv(sock, buf)
    print(resp)
    send(sock, seq, "initialize", {"adapterId": "symrepl"})
    resp, buf = recv(sock, buf)
    print(resp)
    disconnect(sock, seq)
    print("end test launch after init...")

def testInitAfterInit():
    sock, buf = connect("127.0.0.1", 5678)
    seq = count(1)
    print("start a test init after init...")
    send(sock, seq, "initialize", {"adapterId": "symrepl"})
    resp, buf = recv(sock, buf)
    print(resp)
    send(sock, seq, "initialize", {"adapterId": "symrepl"})
    resp, buf = recv(sock, buf)
    print(resp)
    disconnect(sock, seq)
    print("end test init after init...")
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

def testLaunchConfigOrder():
    # 帧序裁决（OQ-1 已结案 2026-10-15）：放行=configDone 本身，launch resp 先、configDone resp 后
    ktest = "/home/odot/github/symrepl/third_party/klee/ktest/get_sign_test000003.ktest"
    target = "/tmp/opencode/replay-build/get_sign"
    sock, buf = connect("127.0.0.1", 5678)
    sock.settimeout(10)   # 放行帧不来就超时报警，别挂死
    seq = count(1)
    print("start frame-order test (fixed: buf 接力)...")
    send(sock, seq, "initialize", {"adapterId": "symrepl"})
    show(recv(sock, buf)[0])
    send(sock, seq, "launch", {"ktest": ktest, "target": target})
    # 扣响应：故意不 recv——注意 process/initialized 事件此刻已在 TCP 缓冲里排队
    send(sock, seq, "configurationDone")
    buf = drain(sock, buf, "排空1", 3)    # 期望：process, initialized, launch resp, configDone resp
    print(">> send threads")
    send(sock, seq, "threads")
    buf = drain(sock, buf, "排空2", 3)    # threads 应答（若进程已跑死：exited/terminated 也在此）
    print(">> send disconnect")
    send(sock, seq, "disconnect", {"terminateDebuggee": True})
    buf = drain(sock, buf, "排空3", 3)    # disconnect 应答 + terminated
    disconnect(sock, seq)
    print("end frame-order test...")
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
    # 手动驱动入口：每测一个要重启一次 server（会话结束 server 自退场）。
    # 全矩阵自动跑在 ../probes/。
    # 用法：python3 dap_client.py [port]（默认 5678）
    import sys
    _port = int(sys.argv[1]) if len(sys.argv) > 1 else 5678
    #testInitAfterLaunch()
    #testInitAfterInit()
    #testLaunchConfigOrder()