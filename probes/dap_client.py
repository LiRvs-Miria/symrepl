"""DAP 客户端驱动库（网用精简版，2026-10-22 自 client/dap_client.py 裁出：
手工作面板 Manual/manual_chain/replay_chain/show* 随 client 退役，仅存
八网共用的 connect/send/recv 三原语；同步帧协议，buf 接力由调用方持有）。"""
import json, socket


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
