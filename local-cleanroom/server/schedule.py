"""schedule — 壳（Imperative Shell）：accept/拒连/读循环/信号/退出码。

分层：framing=分帧（结构件）→ statemachine=协议核心（转录自 design-notes.md）
→ 本文件只做 I/O 与收场。日志只走 stderr：stdout 将来是说 DAP 协议的。
"""
import asyncio
import contextlib
import functools
import signal
import sys

from error import TransExitCode
from framing import FrameError, encode_frame, read_frame
from statemachine import Phase, State, dispatch


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)
#end def


async def handle(reader: asyncio.StreamReader,
                 writer: asyncio.StreamWriter,
                 *, state: State):
    if state.active_writer is not None:
        # 拒连是 server 的输出动作：RST 只是 close 带未读数据的内核副作用，进程不受影响
        peer = writer.get_extra_info("peername")
        log(f"conn_rejected reason=slot_busy peer={peer}")
        writer.close()
        with contextlib.suppress(Exception):
            await writer.wait_closed()
        return
    #end if

    state.active_writer = writer
    peer = writer.get_extra_info("peername")
    log(f"connected peer={peer}")
    try:
        while True:
            try:
                msg = await read_frame(reader)
            except FrameError as e:
                # 坏帧→ABRUPT：对齐参考"malformed client frame"exit=2 实证；正式决策留阶段3
                state.exit_code = TransExitCode.ABRUPT
                log(f"session_end reason=bad_frame peer={peer} err={e}")
                break
            #end try
            if msg is None:
                # FIN：对端体面告别，不是异常
                log(f"session_end reason=eof peer={peer}")
                break
            #end if
            for out in dispatch(state, msg):
                writer.write(encode_frame(out))
                await writer.drain()
            #end for
            if state.phase == Phase.DEAD:
                break                    # disconnect 应答已送达，会话完毕
        #end while
    # RST：连接被对端/内核枪毙（ECONNRESET，常现于 read/drain）
    except ConnectionResetError as e:
        state.exit_code = TransExitCode.ABRUPT
        log(f"session_end reason=reset peer={peer} err={e!r}")
    # EPIPE：向已死连接写（往往 RST 先到但 read 没撞上）
    except BrokenPipeError as e:
        state.exit_code = TransExitCode.ABRUPT
        log(f"session_end reason=epipe peer={peer} err={e!r}")
    finally:
        state.slot = None                # 清：交易作废（单出口结构红利）
        writer.close()
        with contextlib.suppress(Exception):
            await writer.wait_closed()
        state.active_writer = None
        log(f"closed peer={peer}")
        state.client_done.set()
    #end try
#end def


def on_signal(state: State):
    log("signal_received shutting_down")
    if state.active_writer is not None:
        state.active_writer.close()
    else:
        state.client_done.set()
    #end if
#end def


async def main(host: str, port: int):
    loop = asyncio.get_running_loop()
    state = State()

    server = await asyncio.start_server(
        functools.partial(handle, state=state),
        host, port,
    )
    for sock in server.sockets:
        log(f"listening addr={sock.getsockname()}")

    for s in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(s, functools.partial(on_signal, state))

    await state.client_done.wait()
    server.close()
    await server.wait_closed()
    # 终态粗类 → 进程退出码（8 位内；信号路径不改 exit_code，保持 FIN=0）
    sys.exit(state.exit_code)
#end def


if __name__ == "__main__":
    asyncio.run(main("127.0.0.1", int(sys.argv[1]) if len(sys.argv) > 1 else 5678))  # §0 观察点：单栈 127.0.0.1（localhost 双栈会在半绑定失败时拖死全体，2026-10-14 实测）
#end if
