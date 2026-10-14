import asyncio
import contextlib
import functools
import signal
import sys
from dataclasses import dataclass, field

from error import TransExitCode

# 日志只走 stderr：stdout 将来是说 DAP 协议的（stdio transport），不许污染
def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)
#end def

@dataclass
class State:
    active_writer: asyncio.StreamWriter | None = None
    client_done: asyncio.Event = field(default_factory=asyncio.Event)
    # 终态粗类，默认体面收场；只有 ABRUPT 分支会改写
    exit_code: TransExitCode = TransExitCode.FIN
#end class

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
    #正常读TCP数据，TCP数据送完结束
    try:
        while True:
            data = await reader.read(4096)
            if not data:
                # FIN：对端体面告别（read() 返回 b""），不是异常
                log(f"session_end reason=eof peer={peer}")
                break
            writer.write(data)
            await writer.drain()
    # RST：连接被对端/内核枪毙（ECONNRESET，常现于 read/drain）
    except ConnectionResetError as e:
        state.exit_code = TransExitCode.ABRUPT
        log(f"session_end reason=reset peer={peer} err={e!r}")
    # EPIPE：向已死连接写（往往 RST 先到但 read 没撞上）
    except BrokenPipeError as e:
        state.exit_code = TransExitCode.ABRUPT
        log(f"session_end reason=epipe peer={peer} err={e!r}")
    finally:
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
    sys.exit(int(state.exit_code))
#end def

if __name__ == "__main__":
    asyncio.run(main("localhost", 5678))
#end if
