"""schedule — 壳（Imperative Shell）：accept/拒连/读循环/信号/退出码。

pipeline：msg → step（状态转移）→ build_replies（回包+seq 账本）→ 上线。
壳只做 I/O 与收场；协议状态（ProtoState）由 step 返回值搬运，收场即丢弃。
日志只走 stderr：stdout 将来是说 DAP 协议的（stdio transport），不许污染。
"""
import argparse
import asyncio
import contextlib
import functools
import signal
import sys
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import assert_never

from error import TransExitCode
from framing import FrameBad, FrameBadReason, FrameEof, FrameOk, FrameOut, encode_frame, read_frame
from adapter import (Action, Crashed, Done, ForwardRequest, FulfillLaunch, Gone,
                     MaybeParsedRequest, Outcome, Rejected, SpawnFailed, TimedOut, ProtoState,
                     Reply, StateEnum, StepResult, build_replies, parse_request, resolve, step,
                     translate_child_event)
from childproto import BadChildMsg, ChildEvent, ChildRequest, ChildResponse, parse_child_msg, request_msg

SESSION_SCRIPT = str(Path(__file__).resolve().parent / "session.py")

def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)
#end def

async def spawn_child(argv: list[str]) -> asyncio.subprocess.Process:
    """spawn service 子进程：stdin/stdout 双管道，父↔child 协议通道由此而生。

    选型（用户裁 2026-10-16）：句柄=asyncio.subprocess.Process 裸用不包壳；
    Future 表与 reader task 随 child_request 机制走，不挂此处。
    stderr 继承：child hygiene 前的早期事故（import 失败等）落进本进程
    stderr 日志流；hygiene 后 child 的输出归 --log，此路静默。
    """
    return await asyncio.create_subprocess_exec(
        *argv,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
    )
#end def


class ChildGoneError(RuntimeError):
    """EOF 路（decision 8）：child 已死/失联，请求立即失败，不烧 timeout。"""
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
    #end def
    def __repr__(self) -> str:
        # 默认 repr 只显构造参数（message），code 会隐身——detail 串与日志都靠 repr
        return f"{type(self).__name__}(code={self.code!r}, message={self.message!r})"
    #end def
#end class


class ChildRequestError(RuntimeError):
    """应用级：child 明确拒单（ok=False 应答，携子面错误码）。"""
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
    #end def
    def __repr__(self) -> str:
        # 同 ChildGoneError：code 必须进 repr（子面错误分类学不许压扁成散文）
        return f"{type(self).__name__}(code={self.code!r}, message={self.message!r})"
    #end def
#end class


@dataclass
class _Channel:
    """每 child 一份的缝内状态：pending 表 + reader task + 单写者锁。

    句柄=裸 Process（用户选型），缝内状态挂模块级注册表，不包壳不挂 Process。
    """
    proc: asyncio.subprocess.Process
    pending: dict = field(default_factory=dict)                      # id → Future
    write_lock: asyncio.Lock = field(default_factory=asyncio.Lock)   # 单写者（contract）
    next_id: int = 0
    dead: bool = False
    reader_task: asyncio.Task | None = None
    sink: Callable[[ChildEvent], Awaitable[None]] | None = None   # 事件回流口（conn 侧注入；None=留痕丢弃）
#end class


_CHANNELS: dict = {}   # Process → _Channel；child_close 随册


def _channel_for(child) -> _Channel:
    ch = _CHANNELS.get(child)
    if ch is None:
        ch = _Channel(proc=child)
        _CHANNELS[child] = ch
        ch.reader_task = asyncio.get_running_loop().create_task(_read_child(ch))
    #end if
    return ch
#end def


def _fail_pending(ch: _Channel, detail: str) -> None:
    """EOF/坏帧路：在途请求全部 ChildGoneError 快败（decision 8），表清空（不变量②）。"""
    ch.dead = True
    log(f"child_gone detail={detail}")
    for fut in ch.pending.values():
        if not fut.done():
            fut.set_exception(ChildGoneError("child_gone", detail))
        #end if
    #end for
    ch.pending.clear()
#end def


async def _read_child(ch: _Channel) -> None:
    """reader task：帧分流（response→resolve Future；event→砖3 留位；EOF/坏帧→child-gone）。"""
    reader = ch.proc.stdout
    assert reader is not None                # spawn_child 保证 stdout=PIPE
    try:
        while True:
            frame = await read_frame(reader)
            match frame:
                case FrameOk(payload=msg):
                    parsed = parse_child_msg(msg)
                    match parsed:
                        case ChildResponse():
                            fut = ch.pending.pop(parsed.id, None)
                            if fut is not None and not fut.done():
                                if parsed.ok:
                                    fut.set_result(parsed.result or {})
                                else:
                                    err = parsed.error or {}
                                    fut.set_exception(ChildRequestError(
                                        str(err.get("code", "unknown")),
                                        str(err.get("message", ""))))
                                #end if
                            #end if
                        case ChildEvent():
                            if ch.sink is not None:
                                await ch.sink(parsed)          # 回流：翻译+领 seq+投递（D3）
                            else:
                                log(f"child_event event={parsed.event} params={parsed.params}")   # 无 sink=留痕丢弃
                            #end if
                        case BadChildMsg(reason=reason, detail=detail):
                            _fail_pending(ch, f"malformed child message: {reason} {detail}")
                            return
                        case _:
                            assert_never(parsed)
                    #end match
                case FrameEof():
                    _fail_pending(ch, "service session exited (stdout EOF)")
                    return
                case FrameBad(reason=reason, detail=detail):
                    _fail_pending(ch, f"malformed child frame: {reason.name} {detail}")
                    return
                case _:
                    assert_never(frame)   # 穷尽性：ADT 加员忘接=构造期大声失败
            #end match
        #end while
    except asyncio.CancelledError:
        raise                              # close 路取消：随关闭消亡
    except Exception as e:                 # stdout 已死等读崩：同 child-gone 路
        _fail_pending(ch, f"child reader crashed: {e!r}")
    #end try
#end def


async def child_request(child, cmd: str, params: dict | None = None, timeout: float = 10.0) -> dict:
    """一次内部请求-应答往返（correlation=id→Future）。

    decision 8：发前 child 已死或通道已死 → ChildGoneError 立即，不烧 timeout；
    超时/EOF 抢同一在途请求=先到先得（不变量④），失败路 pending 必清（不变量②）。
    """
    ch = _channel_for(child)
    if ch.dead or (child.returncode is not None):   # asyncio 版 poll()：watcher 回填 returncode
        raise ChildGoneError("child_gone", "service session is no longer running")
    #end if
    ch.next_id += 1
    req_id = ch.next_id
    fut = asyncio.get_running_loop().create_future()
    ch.pending[req_id] = fut
    try:
        try:
            async with ch.write_lock:      # 单写者：字节序权威（contract）
                msg = request_msg(ChildRequest(id=req_id, cmd=cmd, params=params or {}))
                child.stdin.write(encode_frame(msg))
                await child.stdin.drain()
            #end with
        except Exception as e:                # 写路一断=通道死（单一结局）；翻译在此一行
            raise ChildGoneError("child_gone", f"service child unreachable: {e!r}") from e
        #end try
        return await asyncio.wait_for(fut, timeout=timeout)
    except asyncio.TimeoutError:
        raise TimeoutError(f"child request {cmd!r} timed out after {timeout}s")
    finally:
        ch.pending.pop(req_id, None)       # 成功/超时/gone 三路同清（不变量②）
    #end try
#end def


async def child_ping(child, timeout: float = 10.0) -> dict:
    """启动握手探活（=child_request("ping") 薄别名；心跳已退役，见 design-notes）。"""
    return await child_request(child, "ping", {}, timeout)
#end def


async def child_close(child) -> None:
    """收场：关 stdin 走 child 自杀路（exit0）→ 优雅窗 2s → 强杀。幂等（不变量③）。"""
    ch = _CHANNELS.pop(child, None)
    if ch is not None and ch.reader_task is not None:
        ch.reader_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await ch.reader_task
        #end with
    #end if
    if child.stdin is not None and not child.stdin.is_closing():
        child.stdin.close()
    #end if
    try:
        await asyncio.wait_for(child.wait(), timeout=2.0)
    except asyncio.TimeoutError:
        child.kill()
        await child.wait()
    #end try
#end def

def _physical(e: Exception) -> Outcome:
    """异常→物理事实：认类型给构造器（TimeoutError ⊂ OSError，序在前）；
    末臂默认收编——异常分类学只改本函数，except 面不再动。"""
    match e:
        case TimeoutError():
            return TimedOut(repr(e))
        case ChildGoneError():
            return Gone(repr(e))
        case ChildRequestError():
            return Rejected(repr(e))
        case OSError():
            return SpawnFailed(repr(e))
        case _:
            return Crashed(repr(e))
    #end match
#end def


async def forward_event(conn: "ConnState", ev: ChildEvent) -> None:
    """事件回流：核翻译（只翻不判）→ 领 seq（D3：无请求事件吃账本）→ 写客户端。
    领号到回写不跨 await=原子；写完整帧=帧原子，与 handle 路的帧序竞争不钉（探针已宽容）。
    客户端已走/写崩：事件无处投递即丢弃——异常沿 reader 路走，砖4/砖5 再裁边界。"""
    event = translate_child_event(ev.event, ev.params)
    if event is None:
        log(f"child_event_dropped event={ev.event}")
        return
    #end if
    frames, conn.seq = build_replies((event,), conn.seq)
    writer = conn.active_writer
    if writer is None:
        log("child_event_dropped reason=no_client")
        return
    #end if
    for out in frames:
        writer.write(encode_frame(out))
        await writer.drain()
    #end for
#end def


async def run_effect(conn: "ConnState", action: Action) -> Outcome:
    """效果执行器：核声明、壳执行。成功只落册肉身与上交 result（内容不看），
    失败收尸（幂等）并报物理事实。"""
    match action:
        case FulfillLaunch(req):
            child = None
            try:
                child = await spawn_child([sys.executable, SESSION_SCRIPT,
                                           "--backend", conn.backend,
                                           "--lldb-dir", conn.lldb_dir])
                await child_ping(child)
                result = await child_request(child, "launch",
                                             {"ktest": req.arguments["ktest"],
                                              "target": req.arguments["target"]})
            except Exception as e:                # 全捕获：收尸无条件；分类归 _physical
                if child is not None:
                    await child_close(child)      # 半死也收；幂等
                #end if
                outcome = _physical(e)
                log(f"effect_failed action={type(action).__name__} "
                    f"outcome={type(outcome).__name__} detail={e!r}")
                return outcome
            #end try
            conn.child = child                    # 会话肉身落册（砖3 事件源/砖4 拆除由此取）
            _channel_for(child).sink = functools.partial(forward_event, conn)   # 回流口随肉身就位
            return Done(result)
        case ForwardRequest(cmd=cmd, params=params):
            child = conn.child
            if child is None:                     # S2 必然已履约；None=不变量洞，大声失败
                raise AssertionError("ForwardRequest with no session child")
            #end if
            try:
                result = await child_request(child, cmd, params)
            except Exception as e:                # 全捕获；肉身非本差事产物不收尸（砖4 管拆除），
                outcome = _physical(e)            # 对端死了自有 reader 的 EOF 路报丧
                log(f"effect_failed action={type(action).__name__} "
                    f"outcome={type(outcome).__name__} detail={e!r}")
                return outcome
            #end try
            return Done(result)
        case _:
            raise AssertionError(f"action not wired: {action!r}")
    #end match
#end def


@dataclass
class ConnState:
    # 世界对象（协议状态之外，永远留在壳里）
    active_writer: asyncio.StreamWriter | None = None
    client_done: asyncio.Event = field(default_factory=asyncio.Event)
    exit_code: int = 0          # TransExitCode 的 int 值（FIN=0 / ABRUPT=2）
    child: asyncio.subprocess.Process | None = None   # 会话肉身（run_effect 成功后落册；砖4 拆除取用）
    # 壳搬运的两本账：协议状态 + 回包 seq
    pstate: ProtoState = ProtoState(phase=StateEnum.S0)
    seq: int = 1
    # 后端插拔参数（main 按 argv 填真值；spawn 时下传子进程）
    backend: str = "lldb"
    lldb_dir: str = ""
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
            frame_out: FrameOut = await read_frame(reader)
            match frame_out:
                case FrameOk(payload=msg):
                    parsed: MaybeParsedRequest = parse_request(msg)  # 边界解析：正常→step，异常→直接回包
                    if isinstance(parsed, Reply):
                        # 未知命令：parse 阶段直接产出应答，不进状态机
                        frames, new_pstate = (parsed,), conn.pstate
                    else:
                        result = step(conn.pstate, parsed)
                        if result.action is not None:
                            # 在途转移：壳执行动作，核落靶（协议语义不过壳）
                            outcome = await run_effect(conn, result.action)
                            result = resolve(result.state, result.action, outcome)
                        #end if
                        frames, new_pstate = result.frames, result.state
                    #end if
                    out_frames, conn.seq = build_replies(frames, conn.seq)
                    conn.pstate = new_pstate          # 协议状态由返回值搬运（纯核，无原地突变）
                    for out in out_frames:
                        writer.write(encode_frame(out))
                        await writer.drain()
                    #end for
                    if conn.pstate.phase == StateEnum.DEAD:
                        break                         # disconnect 应答已送达，会话完毕
                case FrameBad(reason=reason, detail=detail):
                    # 坏帧→ABRUPT：no-resync（对齐参考 decision 2），reason 名=探针分类锚点
                    conn.exit_code = TransExitCode.ABRUPT
                    log(f"session_end reason=bad_frame peer={peer} "
                        f"frame_bad={reason.name} detail={detail}")
                    break
                case FrameEof():
                    # FIN：对端体面告别，不是异常
                    log(f"session_end reason=eof peer={peer}")
                    break
                case _:
                    assert_never(frame_out)   # 穷尽性：ADT 加员忘接=构造期大声失败
            #end match

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


async def main(host: str, port: int, *, backend: str, lldb_dir: str):
    loop = asyncio.get_running_loop()
    conn = ConnState(backend=backend, lldb_dir=lldb_dir)

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
    parser = argparse.ArgumentParser(prog="schedule")
    parser.add_argument("port", nargs="?", type=int, default=5678)
    parser.add_argument("--backend", default="lldb",
                        help="debugger backend name（插拔册键）")
    parser.add_argument("--lldb-dir", required=True, metavar="DIR",
                        help="toolchain dir containing lldb and lldb-server")
    _a = parser.parse_args()
    asyncio.run(main("127.0.0.1", _a.port,
                     backend=_a.backend, lldb_dir=_a.lldb_dir))
    # §0 观察点：单栈 127.0.0.1（localhost 双栈会在半绑定失败时拖死全体，2026-10-14 实测）
#end if
