"""schedule — 壳（Imperative Shell）：accept/拒连/读循环/信号/退出码。

pipeline：msg → step（状态转移）→ build_replies（回包+seq 账本）→ 上线。
壳只做 I/O 与收场；协议状态（ProtoState）由 step 返回值搬运，收场即丢弃。
日志只走 stderr：stdout 将来是说 DAP 协议的（stdio transport），不许污染。
"""
import asyncio
import contextlib
import functools
import signal
import sys
from dataclasses import dataclass, field

from error import TransExitCode
from framing import FrameError, encode_frame, read_frame
from statemachine import Reply, ProtoState, StateEnum, build_replies, parse_request, step


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)
#end def


@dataclass
class ConnState:
    # 世界对象（协议状态之外，永远留在壳里）
    active_writer: asyncio.StreamWriter | None = None
    client_done: asyncio.Event = field(default_factory=asyncio.Event)
    exit_code: int = 0          # TransExitCode 的 int 值（FIN=0 / ABRUPT=2）
    # 壳搬运的两本账：协议状态 + 回包 seq
    pstate: ProtoState = ProtoState(phase=StateEnum.S0)
    seq: int = 1
#end class


async def handle(reader: asyncio.StreamReader,
                 writer: asyncio.StreamWriter,
                 *, conn: ConnState):
    if conn.active_writer is not None:
        # 拒连是 server 的输出动作：RST 只是 close 带未读数据的内核副作用，进程不受影响
        peer = writer.get_extra_info("peername")
        log(f"conn_rejected reason=slot_busy peer={peer}")
        writer.close()
        with contextlib.suppress(Exception):
            await writer.wait_closed()
        return
    #end if

    conn.active_writer = writer
    peer = writer.get_extra_info("peername")
    log(f"connected peer={peer}")
    try:
        while True:
            try:
                msg = await read_frame(reader)
            except FrameError as e:
                # 坏帧→ABRUPT：对齐参考"malformed client frame"exit=2 实证；正式决策留阶段3
                conn.exit_code = TransExitCode.ABRUPT
                log(f"session_end reason=bad_frame peer={peer} err={e}")
                break
            #end try
            if msg is None:
                # FIN：对端体面告别，不是异常
                log(f"session_end reason=eof peer={peer}")
                break
            #end if
            if msg.get("type") != "request":
                log(f"ignored non-request frame type={msg.get('type')!r}")
                continue
            #end if

            parsed = parse_request(msg)       # 边界解析：dict → 类型（parse, don't validate）
            if isinstance(parsed, Reply):
                # 未知命令：parse 阶段直接产出应答，不进状态机
                replies, new_pstate = (parsed,), conn.pstate
            else:
                expect = step(conn.pstate, parsed)
                replies, new_pstate = expect.replies, expect.state
            #end if
            frames, conn.seq = build_replies(replies, conn.seq)
            conn.pstate = new_pstate          # 协议状态由返回值搬运（纯核，无原地突变）
            for out in frames:
                writer.write(encode_frame(out))
                await writer.drain()
            #end for
            if conn.pstate.phase == StateEnum.DEAD:
                break                         # disconnect 应答已送达，会话完毕
        #end while
    # RST：连接被对端/内核枪毙（ECONNRESET，常现于 read/drain）
    except ConnectionResetError as e:
        conn.exit_code = TransExitCode.ABRUPT
        log(f"session_end reason=reset peer={peer} err={e!r}")
    # EPIPE：向已死连接写（往往 RST 先到但 read 没撞上）
    except BrokenPipeError as e:
        conn.exit_code = TransExitCode.ABRUPT
        log(f"session_end reason=epipe peer={peer} err={e!r}")
    finally:
        # 收场清理 = 丢弃 ProtocolState（"清 slot=交易作废"升维为丢弃整个状态）
        conn.pstate = ProtoState(phase=StateEnum.DEAD)
        writer.close()
        with contextlib.suppress(Exception):
            await writer.wait_closed()
        conn.active_writer = None
        log(f"closed peer={peer}")
        conn.client_done.set()
    #end try
#end def


def on_signal(conn: ConnState):
    log("signal_received shutting_down")
    if conn.active_writer is not None:
        conn.active_writer.close()
    else:
        conn.client_done.set()
    #end if
#end def


async def main(host: str, port: int):
    loop = asyncio.get_running_loop()
    conn = ConnState()

    server = await asyncio.start_server(
        functools.partial(handle, conn=conn),
        host, port,
    )
    for sock in server.sockets:
        log(f"listening addr={sock.getsockname()}")

    for s in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(s, functools.partial(on_signal, conn))

    await conn.client_done.wait()
    server.close()
    await server.wait_closed()
    # 终态粗类 → 进程退出码（8 位内；信号路径不改 exit_code，保持 FIN=0）
    sys.exit(conn.exit_code)
#end def


if __name__ == "__main__":
    asyncio.run(main("127.0.0.1", int(sys.argv[1]) if len(sys.argv) > 1 else 5678))
    # §0 观察点：单栈 127.0.0.1（localhost 双栈会在半绑定失败时拖死全体，2026-10-14 实测）
#end if
