"""lldb_session — LLDB 会话原语（纯判定核 + SB 非纯壳）。

纯/非纯劈分：判定全部下沉纯函数——plain 值进出、结果/错误当值返回（家法对齐
FrameBad/BadChildMsg：不抛、不猜、不续）、错误码代数归 error.py 3xxx；
SB 调用只住在 Session 壳里取数与动作——会话生命周期可按 (动作,事件)→状态
严格建模（E4 编排接入口）。

坑清单即契约（phase1 蒸馏+本轮现形）：
- 模块定位：plain import 失败走 `lldb -P`
- 单进程单 debugger：宿主复用 Create 静默 no-op+失败后 Continue 永挂 →
  第二个 Session 构造期大声拒绝
- 事件排空：判定在 classify_event（纯）——非本 process/非进程/Running 过场全丢
- owner-thread：SB API 只许创建 Session 的线程调用；本模块不启任何线程
- lldb-server：本地 launch 硬依赖；发现=find_lldb_server（纯，目录序为入参），
  注入=服务启动参数 debugserver_path（LLDB 经 LLDB_DEBUGSERVER_PATH 消费，
  launch 前单一赋值点）
"""
from __future__ import annotations

import os
import subprocess
import sys
from enum import Enum
from typing import NamedTuple, assert_never

from error import SessionErrCode

DEBUG_SERVER_ENV = "LLDB_DEBUGSERVER_PATH"    # LLDB 的消费通道名（启动层引用）

_lldb = None              # 进程内缓存（load 一次）
_created = False          # 单进程单 debugger 的守门旗
_STATE_TABLE: dict = {}   # 枚举常量→KnownState（load 后灌入，此后纯查表）
_RUNNING_STATE: int = 0   # 同上（classify_event 用）


# ---------- 纯核代数（plain 值进出；结果/错误当值返回；无 SB 调用、无环境读写） ----------

class KnownState(Enum):
    STOPPED = "stopped"
    EXITED = "exited"
    CRASHED = "crashed"
    DETACHED = "detached"
#end class

class UnknownState(NamedTuple):
    """未映射的原始状态：如实携值（不假装认识）。"""
    raw: int
#end class

SessionState = KnownState | UnknownState

class Drop(NamedTuple):
    """排空判定：事件不留（附因）。"""
    reason: str
    detail: str
#end class

class Accept(NamedTuple):
    state: SessionState
#end class

EventVerdict = Drop | Accept

class ServerFound(NamedTuple):
    path: str
#end class

class ServerNotFound(NamedTuple):
    detail: str
#end class

LldbServer = ServerFound | ServerNotFound


def state_label(state: int) -> SessionState:
    return _STATE_TABLE.get(state) or UnknownState(raw=state)
#end def


def classify_event(is_process: bool, proc_id: int | None,
                   state: int, own_pid: int) -> EventVerdict:
    """排空规则（纯）：非进程/异家/Running 过场 → Drop（附因）；余者 Accept 具名态。"""
    if not is_process:
        return Drop(reason="not_process", detail="event is not a process event")
    #end if
    if proc_id != own_pid:
        return Drop(reason="foreign", detail=f"event belongs to pid {proc_id}")
    #end if
    if state == _RUNNING_STATE:
        return Drop(reason="running", detail="running transit state")
    #end if
    return Accept(state=state_label(state))
#end def


def find_lldb_server(search_dirs: list[str]) -> LldbServer:
    """纯查找：按候选目录序找首个可执行 lldb-server*；无则 ServerNotFound（附因）。
    目录序由启动层构造（本函数不碰 PATH、不碰环境）。"""
    for directory in search_dirs:
        try:
            names = sorted(n for n in os.listdir(directory)
                           if n.startswith("lldb-server")
                           and os.path.isfile(os.path.join(directory, n))
                           and os.access(os.path.join(directory, n), os.X_OK))
        except OSError:
            continue
        #end try
        if names:
            return ServerFound(path=os.path.join(directory, names[0]))
        #end if
    #end for
    return ServerNotFound(detail=f"no executable lldb-server* in {len(search_dirs)} dir(s)")
#end def


def _ingest_enums(lldb) -> None:
    """枚举常量→plain 表（load 后一次性；此后判定纯查表）。"""
    global _STATE_TABLE, _RUNNING_STATE
    _STATE_TABLE = {lldb.eStateStopped: KnownState.STOPPED,
                    lldb.eStateExited: KnownState.EXITED,
                    lldb.eStateCrashed: KnownState.CRASHED,
                    lldb.eStateDetached: KnownState.DETACHED}
    _RUNNING_STATE = int(lldb.eStateRunning)
#end def


# ---------- 非纯壳：SB 取数→纯判定→SB 动作（异常携 error.py 3xxx 码） ----------

class SessionError(RuntimeError):
    """会话原语失败（壳路异常）；码族归 error.py 3xxx，构造期逼显式无默认。"""
    def __init__(self, code: SessionErrCode, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
    #end def
#end class


def _physical(e: Exception, fallback: SessionErrCode) -> SessionErrCode:
    """异常→粗类码（schedule 同款+业务缺省）：认类型给码，认不出=fallback；
    SessionError 已带码如实透传。分类学只改本函数，except 面不再动。"""
    match e:
        case SessionError():
            return e.code
        case TimeoutError():
            return SessionErrCode.EVENT_TIMEOUT
        case _:
            return fallback                     # 末臂：调用点按业务给缺省码
    #end match
#end def


def load_lldb():
    """定位 lldb python 绑定：plain import → `lldb -P` 给路径再 import。
    异常面=SessionError 一类；缺省码 NO_LLDB——一切定位失败都是这一个物理事实。"""
    global _lldb
    if _lldb is not None:
        return _lldb
    #end if
    try:
        _lldb = _import_lldb()
        _ingest_enums(_lldb)
    except Exception as e:
        _lldb = None                            # 回滚半载状态，进程保持可重试
        raise SessionError(_physical(e, SessionErrCode.NO_LLDB), repr(e)) from e
    #end try
    return _lldb
#end def


def _import_lldb():
    """plain import → `lldb -P` 备援（ImportError=走备援的信号；异常交翻译层）。"""
    try:
        import lldb as mod
        return mod
    except ImportError:
        pass                                    # 备援信号，非失败
    #end try
    out = subprocess.run(["lldb", "-P"], capture_output=True, text=True, check=True)
    path = out.stdout.strip().splitlines()[-1].strip() if out.stdout.strip() else ""
    if not path:
        raise RuntimeError("lldb -P produced no python path")
    #end if
    if path not in sys.path:
        sys.path.insert(0, path)
    #end if
    import lldb as mod
    return mod
#end def


class Session:
    """一个 debugger 一条命：target 常驻，launch/resume/wait 是全部原语。

    不复用：构造即守门（见模块 docstring）；close 后本进程不再产 Session。
    """

    def __init__(self, target: str, *, debugserver_path: str | None = None) -> None:
        global _created
        if _created:
            raise SessionError(SessionErrCode.DEBUGGER_EXISTS,
                               "one debugger per process; host reuse silently no-ops on "
                               "LLDB 22.1.8 — start a fresh process instead")
        #end if
        try:
            self.lldb = load_lldb()
            self.debugserver_path = debugserver_path
            self.dbg = self.lldb.SBDebugger.Create()
            self.dbg.SetAsync(True)
            self.target = self.dbg.CreateTarget(target)
            if not self.target.IsValid():
                raise SessionError(SessionErrCode.BAD_TARGET,
                                   f"lldb could not load target: {target}")
            #end if
            self.listener = self.lldb.SBListener("cleanroom-session")
            self.process = None
            self.target_path = target
        except Exception as e:
            _created = True                     # 半初始化的 debugger 也是坑：进程内不许重试
            raise SessionError(_physical(e, SessionErrCode.BAD_TARGET), repr(e)) from e
        #end try
        _created = True
    #end def

    def launch(self, ktest: str, *,
               stdout_path: str | None = None,
               stderr_path: str | None = None,
               timeout: float = 30.0) -> None:
        """起 inferior：KTEST_FILE 单变量最小环境+入口停（对齐 kleeRuntest 契约：
        环境不设 KTEST_FILE 时运行时会卡 stdin）。入口停没等到=失败。"""
        try:
            if not ktest:
                raise SessionError(SessionErrCode.BAD_ARGS, "ktest path required")
            #end if
            if self.debugserver_path:
                # LLDB 消费该配置的唯一通道；参数驱动的单点赋值（发现归启动层）
                os.environ[DEBUG_SERVER_ENV] = self.debugserver_path
            #end if
            error = self.lldb.SBError()
            self.process = self.target.Launch(
                self.listener,
                [self.target_path],                   # argv
                [f"KTEST_FILE={ktest}"],              # envp：仅此一变量（确定性）
                None,                                 # stdin（KTEST_FILE 恒设，永不被读）
                stdout_path,
                stderr_path,
                None,                                 # workdir
                0,                                    # launch flags
                True,                                 # stop at entry
                error,
            )
            if not self.process.IsValid() or error.GetCString():
                detail = error.GetCString() or "invalid process"
                raise SessionError(SessionErrCode.LAUNCH_FAILED,
                                   f"lldb failed to launch: {detail}")
            #end if
            state = self.wait_state(timeout)
            if state is not KnownState.STOPPED:
                raise SessionError(SessionErrCode.ENTRY_NOT_STOPPED,
                                   f"entry wait landed in {state!r}")
            #end if
        except Exception as e:
            raise SessionError(_physical(e, SessionErrCode.LAUNCH_FAILED), repr(e)) from e
        #end try
    #end def

    def continue_(self) -> None:
        """放行 inferior（异步：返回即已续跑，状态走事件）。"""
        try:
            self.process.Continue()
        except Exception as e:
            raise SessionError(_physical(e, SessionErrCode.RESUME_FAILED), repr(e)) from e
        #end try
    #end def

    def wait_state(self, timeout: float = 30.0) -> SessionState:
        """事件排空等待：取数成 plain 值，判定在 classify_event（纯）。"""
        lldb = self.lldb
        ev = lldb.SBEvent()
        seconds = int(timeout)                # SWIG 绑定收 uint32，float 直接 TypeError
        while True:
            if not self.listener.WaitForEvent(seconds, ev):
                raise SessionError(SessionErrCode.EVENT_TIMEOUT,
                                   "no process event in time")
            #end if
            is_process = bool(lldb.SBProcess.EventIsProcessEvent(ev))
            if is_process:
                proc_id = lldb.SBProcess.GetProcessFromEvent(ev).GetProcessID()
                state = int(lldb.SBProcess.GetStateFromEvent(ev))
            else:
                proc_id, state = None, 0
            #end if
            verdict = classify_event(is_process, proc_id, state,
                                     self.process.GetProcessID())
            match verdict:
                case Drop():
                    continue                          # 排空（判定在纯核）
                case Accept(state=st):
                    return st
                case _:
                    assert_never(verdict)             # 穷尽性：加员忘接=大声失败
            #end match
        #end while
    #end def

    def exit_code(self) -> int:
        try:
            if self.process.GetState() != self.lldb.eStateExited:
                raise SessionError(SessionErrCode.NOT_EXITED, "exit code only after exit")
            #end if
            return self.process.GetExitStatus()
        except Exception as e:
            raise SessionError(_physical(e, SessionErrCode.NOT_EXITED), repr(e)) from e
        #end try
    #end def

    def close(self) -> None:
        self.lldb.SBDebugger.Destroy(self.dbg)
        self.lldb.SBDebugger.Terminate()
    #end def
#end class
