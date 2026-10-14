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
        buf += sock.recv(4096)
    head, _, buf = buf.partition(b"\r\n\r\n")
    length = int(dict(l.split(b":", 1) for l in head.split(b"\r\n")
                      if b":" in l)[b"Content-Length"])
    # 2) 再把 body 读够（可能已经有一部分在 buf 里了）
    while len(buf) < length:
        buf += sock.recv(length - len(buf))
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

def testShutdownAfterDisconn():
    sock, buf = connect("127.0.0.1", 5678)
    seq = count(1)
    print("start a test disconnect after init...")
    send(sock, seq, "initialize", {"adapterId": "symrepl"})
    resp, buf = recv(sock, buf)
    print(resp)
    send(sock, seq, "shutdown")
    resp, buf = recv(sock, buf)
    print(resp)
    send(sock, seq, "disconnect",{"terminateDebuggee": True})
    resp, buf = recv(sock, buf)
    print(resp)
    disconnect(sock, seq)
    print("end test disconnect after init...")
#end def

def testDisconnAfterShutdown():
    sock, buf = connect("127.0.0.1", 5678)
    seq = count(1)
    print("start a test socket disconnect after init...")
    send(sock, seq, "initialize", {"adapterId": "symrepl"})
    resp, buf = recv(sock, buf)
    print(resp)
    send(sock, seq, "disconnect",{"terminateDebuggee": True})
    resp, buf = recv(sock, buf)
    print(resp)
    send(sock, seq, "shutdown")
    resp, buf = recv(sock, buf)
    print(resp)
    disconnect(sock, seq)
    print("end test socket disconnect after init...")
#end def

def testLaunchConfigOrder(port=5679):
    # 帧序先验（2026-10-14）：configDone 响应先、launch 响应后——待参考 server 实测裁决
    ktest = "/home/odot/github/symrepl/third_party/klee/ktest/get_sign_test000003.ktest"
    target = "/tmp/opencode/replay-build/get_sign"
    sock, buf = connect("127.0.0.1", port)
    sock.settimeout(10)   # 放行帧不来就超时报警，别挂死
    seq = count(1)
    print("start frame-order test (prior: configDone response first)...")
    send(sock, seq, "initialize", {"adapterId": "symrepl"})
    show(recv(sock, buf)[0])
    send(sock, seq, "launch", {"ktest": ktest, "target": target})
    # 扣响应：故意不 recv——正常情况下此刻线上静默
    send(sock, seq, "configurationDone")
    sock.settimeout(3)   # 排空窗口
    n = 0
    try:   # 排空1：看放行后事件流
        while True:
            show(recv(sock, buf)[0]); n += 1
    except socket.timeout:
        print("<- (排空1完毕，共 %d 帧)" % n)
    print(">> send threads (假设：响应放行耦合在下一个请求)")
    send(sock, seq, "threads")
    n = 0
    try:   # 排空2：threads 应答 + 疑似被扣的两响应
        while True:
            show(recv(sock, buf)[0]); n += 1
    except socket.timeout:
        print("<- (排空2完毕，共 %d 帧)" % n)
    print(">> send disconnect")
    send(sock, seq, "disconnect", {"terminateDebuggee": True})
    n = 0
    try:   # 排空3：disconnect 应答 + 若仍扣押的响应
        while True:
            show(recv(sock, buf)[0]); n += 1
    except socket.timeout:
        print("<- (排空3完毕，共 %d 帧)" % n)
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
    # 测试序列
    #testInitAfterLaunch()
    #testInitAfterInit()
    #testShutdownAfterDisconn() 
    #testDisconnAfterShutdown()
    testLaunchConfigOrder()