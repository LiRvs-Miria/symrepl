"""lldb_backend — LLDB 后端适配器（实现 session.py 端口的 DebugBackend 原语面）。

session 不变、调试器可插拔（2026-10-20 用户裁）：本模块=可替换后端之一；
换引擎=写同原语面的 *_backend.py + session.py 工厂表一行。结构性满足
DebugBackend（session.py 内声明），本模块不 import 端口（零循环）。

纯/非纯劈分：判定全部下沉纯函数——plain 值进出、结果/错误当值返回（家法对齐
FrameBad/BadChildMsg：不抛、不猜、不续）、错误码代数归 error.py 3xxx；
SB 调用只住在 Session 壳里取数与动作——会话生命周期归 session_lifecycle 纯核
（相位/守卫矩阵/转移提交制），本模块取数、执行效果、提交。

坑清单即契约（phase1 蒸馏+本轮现形）：
- 模块定位：plain import 失败走 `lldb -P`
- 单进程单 debugger：宿主复用 Create 静默 no-op+失败后 Continue 永挂 →
  第二个 Session 构造期大声拒绝（LLDB 宿主怪癖，守门归本适配器）
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
from pathlib import Path
from typing import NamedTuple, assert_never

from error import SessionErrCode, SessionError
from session_lifecycle import (CaptureNode, EmitExit, ExecOk, ExecState, ExecTrigger,
                          StopObserved, ExitObserved, guard_exec, ingest_exec,
                          poll_blocked, resolve_exec, step_exec)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "replay"))
from branches import is_conditional            # E2 识别代数（本砖首消费点）

DEBUG_SERVER_ENV = "LLDB_DEBUGSERVER_PATH"    # LLDB 的消费通道名（启动层引用）

_INVALID_LOAD_ADDR = 0xFFFFFFFFFFFFFFFF       # M1 规格同款：未解析载入地址哨兵
_MAX_SITES = 5000                             # 插桩滥用守阀（M1 规格同款）

_lldb = None              # 进程内缓存（load 一次）
_created = False          # 单进程单 debugger 的守门旗
_STATE_TABLE: dict = {}   # 枚举常量→KnownState（load 后灌入，此后纯查表）
_RUNNING_STATE: int = 0   # 同上（classify_event 用）
_BREAKPOINT_REASON: int = 0   # 同上（stop_reason_label 用）
_PLAN_COMPLETE_REASON: int = 0   # 同上（stop_reason_label 用：步进完成停相）


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


def stop_reason_label(stop_reason: int, data_count: int,
                      data_first: int) -> tuple[str, list[int]]:
    """停相 reason 推断（纯）：断点命中携 bp id（GetStopReasonDataAtIndex(0)，
    LLDB 约定）；data 计数 0 时不取（越界防）；PlanComplete=step（步进完成）；
    余者 exception。"""
    if stop_reason == _BREAKPOINT_REASON:
        return "breakpoint", ([data_first] if data_count > 0 else [])
    #end if
    if stop_reason == _PLAN_COMPLETE_REASON:
        return "step", []
    #end if
    return "exception", []
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
    global _STATE_TABLE, _RUNNING_STATE, _BREAKPOINT_REASON, _PLAN_COMPLETE_REASON
    _STATE_TABLE = {lldb.eStateStopped: KnownState.STOPPED,
                    lldb.eStateExited: KnownState.EXITED,
                    lldb.eStateCrashed: KnownState.CRASHED,
                    lldb.eStateDetached: KnownState.DETACHED}
    _RUNNING_STATE = int(lldb.eStateRunning)
    _BREAKPOINT_REASON = int(lldb.eStopReasonBreakpoint)
    _PLAN_COMPLETE_REASON = int(lldb.eStopReasonPlanComplete)
#end def


# ---------- 非纯壳：SB 取数→纯判定→SB 动作（异常携 error.py 3xxx 码） ----------


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


def load_lldb(lldb_dir: str):
    """定位 lldb python 绑定：plain import → `lldb_dir/lldb -P` 给路径再 import
    （不碰 PATH）。异常面=SessionError 一类；缺省码 NO_LLDB——一切定位失败都是
    这一个物理事实。"""
    global _lldb
    if _lldb is not None:
        return _lldb
    #end if
    try:
        _lldb = _import_lldb(lldb_dir)
        _ingest_enums(_lldb)
    except Exception as e:
        _lldb = None                            # 回滚半载状态，进程保持可重试
        raise SessionError(_physical(e, SessionErrCode.NO_LLDB), repr(e)) from e
    #end try
    return _lldb
#end def


def _import_lldb(lldb_dir: str):
    """plain import → `lldb_dir/lldb -P` 备援（ImportError=走备援的信号；
    异常交翻译层）。import 成功≠可用：发行版的空命名空间包（dist-packages/lldb
    零成员）会顶掉真 SWIG 模块——import 后验签枚举常量，缺=视为不通走备援；
    空壳已占 sys.modules 槽，重导前先踢。备援二进制按参数目录取，不碰 PATH。"""
    try:
        import lldb as mod
        if hasattr(mod, "eStateStopped"):
            return mod
        #end if
    except ImportError:
        pass                                    # 备援信号，非失败
    #end try
    exe = os.path.join(lldb_dir, "lldb")
    out = subprocess.run([exe, "-P"], capture_output=True, text=True, check=True)
    path = out.stdout.strip().splitlines()[-1].strip() if out.stdout.strip() else ""
    if not path:
        raise RuntimeError("lldb -P produced no python path")
    #end if
    if path not in sys.path:
        sys.path.insert(0, path)
    #end if
    sys.modules.pop("lldb", None)               # 空壳让位：缓存不踢，重导拿回的还是它
    import lldb as mod
    return mod
#end def


class Session:
    """一个 debugger 一条命：target 常驻，launch/resume/wait 是全部原语。

    不复用：构造即守门（见模块 docstring）；close 后本进程不再产 Session。
    """

    def __init__(self, target: str, *, debugserver_path: str,
                 lldb_dir: str) -> None:
        """target 路径常驻；debugserver_path/lldb_dir=工具链参数（端口面注入，
        本模块零 PATH/系统目录猜测）。"""
        global _created
        if _created:
            raise SessionError(SessionErrCode.DEBUGGER_EXISTS,
                               "one debugger per process; host reuse silently no-ops on "
                               "LLDB — start a fresh process instead")
        #end if
        try:
            self.lldb = load_lldb(lldb_dir)
            self.debugserver_path = debugserver_path
            self.dbg = self.lldb.SBDebugger.Create()
            self.dbg.SetAsync(True)
            self.target = self.dbg.CreateTarget(target)
            if not self.target.IsValid():
                raise SessionError(SessionErrCode.BAD_TARGET,
                                   f"lldb could not load target: {target}")
            #end if
            self.listener = self.lldb.SBListener("symrepl-session")
            self.process = None
            self.target_path = target
            self._exec = ExecState()            # 编排态（相位+世代票据）：核所有，壳只提交
            self._var_refs = {}                 # 变量引用账本（适配器私有）：ref→(generation, frame_index)
            self._ref_counter = 0               # 引用计数器：ref=generation*100000+counter（第二 id 空间）
            self._own_breakpoint_ids: list[int] = []   # 自有断点 id 表（全量替换凭册删旧）
            self._scope: str | None = None      # branch-trace 插桩范围（launch 可选参数）
            self._sym_objects: tuple[tuple[str, bytes], ...] = ()  # ktest 对象（父面解析下发，零文件 I/O）
            self._trace_nodes: list[dict] = []  # actual 轨迹节点（断点命中据实记录，零处理）
        except Exception as e:
            _created = True                     # 半初始化的 debugger 也是坑：进程内不许重试
            raise SessionError(_physical(e, SessionErrCode.BAD_TARGET), repr(e)) from e
        #end try
        _created = True
    #end def

    def _commit(self, state: ExecState) -> None:
        """提交编排态（核 resolve 落靶后的唯一写点）：世代有进=壳清引用账本
        （决策 12：resume 使旧引用失效——清账随提交，效果败不进世代）。"""
        if state.generation != self._exec.generation:
            self._var_refs.clear()
        #end if
        self._exec = state
    #end def

    def launch(self, ktest: str, *, scope: str | None = None,
               sym_objects: list[dict] | None = None,
               stdout_path: str | None = None,
               stderr_path: str | None = None,
               timeout: float = 30.0) -> int:
        """起 inferior：KTEST_FILE 单变量最小环境+入口停（对齐 kleeRuntest 契约：
        环境不设 KTEST_FILE 时运行时会卡 stdin）。入口停没等到=失败。
        scope=branch-trace 插桩范围（"module"或逗号分隔函数名；None=不插桩）。
        sym_objects=ktest 对象（父面已解析，{name, data_hex}；hex 坏=BAD_ARGS——
        本模块零 ktest 文件 I/O，对账字节以父面校验过的那份为准）。
        回 inferior pid（端口面 process 事件的 systemProcessId 源）。"""
        try:
            old = self._exec
            step = step_exec(old, ExecTrigger.LAUNCH)
            if step.error is not None:
                raise step.error
            #end if
            if not ktest:
                raise SessionError(SessionErrCode.BAD_ARGS, "ktest path required")
            #end if
            self._scope = scope or None
            objects: list[tuple[str, bytes]] = []
            for o in (sym_objects or ()):
                try:
                    objects.append((o["name"], bytes.fromhex(o["data_hex"])))
                except (KeyError, ValueError) as e:
                    raise SessionError(SessionErrCode.BAD_ARGS,
                                       f"bad sym object {o.get('name')!r}: {e!r}") from e
                #end try
            #end for
            self._sym_objects = tuple(objects)
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
            self._commit(resolve_exec(old, step, ExecOk()))   # 入口停已到手（静默：上报归 config_done）
            return int(self.process.GetProcessID())
        except Exception as e:
            raise SessionError(_physical(e, SessionErrCode.LAUNCH_FAILED), repr(e)) from e
        #end try
    #end def

    def continue_(self) -> dict:
        """放行 inferior（异步：返回即已续跑，完成以事件到达，response 不等它）。
        spike 规则：非阻塞排空队列残事件再续（历史不当新闻）；决策 12：resume
        使旧引用失效——世代推进+账本清空。"""
        old = self._exec
        step = step_exec(old, ExecTrigger.CONTINUE)
        if step.error is not None:
            raise step.error
        #end if
        ev = self.lldb.SBEvent()
        while self.listener.GetNextEvent(ev):
            pass                                # spike 规则：残事件排空
        #end while
        try:
            self.process.Continue()
        except Exception as e:
            raise SessionError(_physical(e, SessionErrCode.RESUME_FAILED), repr(e)) from e
        #end try
        self._commit(resolve_exec(old, step, ExecOk()))   # 世代有进=清账随提交（效果败不进世代）
        return {"all_threads_continued": True}
    #end def

    def _step_resume(self, trigger: ExecTrigger, sb_step) -> dict:
        """step 族公共体（同步阻塞，2026-10-23 用户钉）：守卫→线程步进（等步完
        才返）→提交。resume 家法照 continue_（提案提交制，世代+1）；完成停相
        由事件泵消费，response 不携停相。"""
        old = self._exec
        step = step_exec(old, trigger)
        if step.error is not None:
            raise step.error
        #end if
        try:
            err = sb_step(self.process.GetThreadAtIndex(0))
            # 绑定返回面不统一：StepOver/StepInto/StepOut 成功返 None（非 SBError）
            if err is not None and err.Fail():
                raise SessionError(SessionErrCode.RESUME_FAILED, repr(err))
            #end if
        except Exception as e:
            raise SessionError(_physical(e, SessionErrCode.RESUME_FAILED), repr(e)) from e
        #end try
        self._commit(resolve_exec(old, step, ExecOk()))
        return {}
    #end def

    def step_next(self) -> dict:
        """next=步过（StepOver，同步阻塞）。"""
        return self._step_resume(ExecTrigger.STEP_NEXT, lambda t: t.StepOver())
    #end def

    def step_in(self) -> dict:
        """stepIn=步入（StepInto，同步阻塞）。"""
        return self._step_resume(ExecTrigger.STEP_IN, lambda t: t.StepInto())
    #end def

    def step_out(self) -> dict:
        """stepOut=步出（StepOut，同步阻塞）。"""
        return self._step_resume(ExecTrigger.STEP_OUT, lambda t: t.StepOut())
    #end def

    def poll_events(self) -> list[dict]:
        """非阻塞事件泵（owner 轮询用）：GetNextEvent 不等，判定在 classify_event。
        dead 短路：终态后不再产事件（防忙烧）。"""
        if poll_blocked(self._exec):
            return []
        #end if
        events: list[dict] = []
        ev = self.lldb.SBEvent()
        while self.listener.GetNextEvent(ev):
            verdict = classify_event(*self._read_event(ev),
                                     self.process.GetProcessID())
            match verdict:
                case Drop():
                    continue                    # 排空（判定在纯核）
                case Accept(state=KnownState.STOPPED):
                    thread = self.process.GetThreadAtIndex(0)
                    count = int(thread.GetStopReasonDataCount())
                    reason, bp_ids = stop_reason_label(
                        int(thread.GetStopReason()), count,
                        int(thread.GetStopReasonDataAtIndex(0)) if count else 0)
                    ingest = ingest_exec(self._exec, StopObserved(reason=reason))
                    self._exec = ingest.state
                    params = {"reason": reason,
                              "thread_id": int(thread.GetThreadID()),
                              "all_threads_stopped": True}
                    if bp_ids:
                        params["breakpoint_ids"] = bp_ids
                    #end if
                    for emit in ingest.emits:    # 取数意图核声明，壳执行（记录点=断点命中）
                        match emit:
                            case CaptureNode():
                                self._trace_nodes.append(
                                    self._capture_node(thread, len(self._trace_nodes) + 1))
                            case _:
                                assert_never(emit)
                        #end match
                    #end for
                    events.append({"event": "stopped", "params": params})
                case Accept(state=KnownState.EXITED) | Accept(state=KnownState.CRASHED):
                    code = int(self.process.GetExitStatus())
                    ingest = ingest_exec(self._exec, ExitObserved(exit_code=code))
                    self._exec = ingest.state
                    # 只发执行面事实 exited；terminated 的 DAP 惯例展开归父面翻译（单源）。
                    # branch tracing 启用（scope 在册）才附报告——未启用会话零增帧。
                    params: dict = {"exit_code": code}
                    if self._scope is not None:
                        params["trace_report"] = {"text": self._render_trace(),
                                                  "nodes": self._trace_nodes}
                    #end if
                    for emit in ingest.emits:    # 发帧意图核声明，壳执行
                        match emit:
                            case EmitExit():
                                events.append({"event": "exited", "params": params})
                            case _:
                                assert_never(emit)
                        #end match
                    #end for
                    break                       # 终态：不再产事件
                case _:
                    pass                        # DETACHED/Unknown：留痕丢弃
            #end match
        #end while
        return events
    #end def

    def _capture_node(self, thread, order: int) -> dict:
        """actual 节点（断点命中据实，零处理）：pc/function/mnemonic/调用栈/符号值。
        一致性注记（match/mismatch/ambiguous/unmatched）=派生层，原始值始终如实。"""
        frame = thread.GetFrameAtIndex(0)
        pc = int(frame.GetPC())
        function = frame.GetFunctionName() or f"0x{pc:x}"
        mnemonic = ""
        instructions = self.target.ReadInstructions(frame.GetPCAddress(), 1)
        if instructions.GetSize() > 0:
            mnemonic = instructions.GetInstructionAtIndex(0).GetMnemonic(self.target) or ""
        #end if
        call_stack = []
        for i in range(thread.GetNumFrames()):
            f = thread.GetFrameAtIndex(i)
            call_stack.append(f.GetFunctionName() or f"0x{int(f.GetPC()):x}")
        #end for
        symbols = []
        for name, data in self._sym_objects:
            var = frame.FindVariable(name)
            if not var.IsValid():
                symbols.append({"name": name, "annotation": "unmatched",
                                "detail": "no local variable with this name"})
                continue
            #end if
            value = var.GetValue() or var.GetSummary() or ""
            sb_data = var.GetData()
            size = int(sb_data.GetByteSize())
            raw = bytes(sb_data.uint8) if size else b""
            if size == len(data) and raw == data:
                annotation = "match"
            elif size == len(data):
                annotation = "mismatch"
            else:
                annotation = "ambiguous"
            #end if
            symbols.append({"name": name, "value": value, "annotation": annotation,
                            "ktest_hex": data.hex()})
        #end for
        return {"order": order, "address": pc, "function": function,
                "mnemonic": mnemonic, "call_stack": call_stack, "symbols": symbols}
    #end def

    def threads(self) -> list[dict]:
        """线程清单（单线程契约取 thread 0；process 在即可，无相位守卫）。
        SB 取数不包（裸崩=bug 按家法炸穿，wait_state/close 同款）。"""
        thread = self.process.GetThreadAtIndex(0)
        tid = int(thread.GetThreadID())
        name = thread.GetName() or f"thread-{tid}"
        return [{"id": tid, "name": name}]
    #end def

    def stack_trace(self, thread_id: int, levels: int) -> dict:
        """栈帧（世代票据：id=generation*1000+index）。守卫与线程存在性在此判定，
        帧取数不包（家法同上）。"""
        err = guard_exec(self._exec, ExecTrigger.STACK_TRACE)
        if err is not None:
            raise err
        #end if
        thread = self.process.GetThreadByID(int(thread_id))
        if not thread.IsValid():
            raise SessionError(SessionErrCode.NO_THREAD,
                               f"no thread with id {thread_id}")
        #end if
        total = thread.GetNumFrames()
        frames = []
        for index in range(min(max(int(levels), 1), total)):
            frame = thread.GetFrameAtIndex(index)
            name = frame.GetFunctionName() or f"0x{int(frame.GetPC()):x}"
            line = column = 0
            path = ""
            entry = frame.GetLineEntry()
            if entry.IsValid():
                line = int(entry.GetLine())
                column = int(entry.GetColumn())
                filespec = entry.GetFileSpec()
                if filespec.IsValid():
                    path = filespec.fullpath or filespec.GetFilename() or ""
                #end if
            #end if
            frames.append({"id": self._exec.generation * 1000 + index, "name": name,
                           "line": line, "column": column, "path": path})
        #end for
        return {"stack_frames": frames, "total_frames": total}
    #end def

    def scopes(self, frame_id: int) -> list[dict]:
        """帧的作用域引用（守卫单集 stopped，照参考；OQ-2 分集合首分叉）。
        解码世代→验帧→发引用并登记账本；SB 取数不包（家法）。"""
        err = guard_exec(self._exec, ExecTrigger.SCOPES)
        if err is not None:
            raise err
        #end if
        generation, frame_index = divmod(int(frame_id), 1000)
        if generation != self._exec.generation:
            raise SessionError(SessionErrCode.STALE_REFERENCE,
                               f"frame id from generation {generation}, "
                               f"current generation is {self._exec.generation}")
        #end if
        thread = self.process.GetThreadAtIndex(0)
        if frame_index >= thread.GetNumFrames():
            raise SessionError(SessionErrCode.NO_FRAME, f"no frame {frame_index}")
        #end if
        self._ref_counter += 1
        ref = generation * 100000 + self._ref_counter
        self._var_refs[ref] = (generation, frame_index)
        return [{"name": "Locals", "variables_reference": ref}]
    #end def

    def variables(self, ref: int) -> list[dict]:
        """引用背后的变量（平铺无子）。解引用验世代；SB 取数不包（家法）。"""
        err = guard_exec(self._exec, ExecTrigger.VARIABLES)
        if err is not None:
            raise err
        #end if
        entry = self._var_refs.get(int(ref))
        if entry is None or entry[0] != self._exec.generation:
            raise SessionError(SessionErrCode.STALE_REFERENCE,
                               f"variables reference {ref} is not from the current stop")
        #end if
        _, frame_index = entry
        thread = self.process.GetThreadAtIndex(0)
        frame = thread.GetFrameAtIndex(frame_index)
        variables = []
        for value in frame.GetVariables(True, True, False, True):
            variables.append(
                {
                    "name": value.GetName() or "<unnamed>",
                    "value": value.GetValue() or value.GetSummary() or "",
                    "type": value.GetTypeName() or "",
                    "variables_reference": 0,  # 平铺：无子引用
                }
            )
        #end for
        return variables
    #end def

    def set_breakpoints(self, path: str, lines: list[int]) -> list[dict]:
        """断点全量替换（DAP per-source 语义，参考简化随迁：单一全局断点集）。
        守卫：process 无→NOT_LAUNCHED；空 path→BAD_ARGS（类型域归 child）。
        删旧凭册→逐行 BreakpointCreateByLocation；invalid 或 pending（零 location，
        路径解析不到）→verified False 如实报；resolved→location address 的 line
        entry 解析 resolved+记册。
        回断点行裸清单（{"breakpoints": …} 键重包归 child，家法同构）。"""
        err = guard_exec(self._exec, ExecTrigger.SET_BREAKPOINTS)
        if err is not None:
            raise err
        #end if
        if not path:
            raise SessionError(SessionErrCode.BAD_ARGS,
                               "source path must be a string")
        #end if
        for bp_id in self._own_breakpoint_ids:
            self.target.BreakpointDelete(bp_id)
        #end for
        self._own_breakpoint_ids = []
        breakpoints = []
        for line in lines:
            bp = self.target.BreakpointCreateByLocation(path, line)
            if bp.IsValid():
                self._own_breakpoint_ids.append(int(bp.GetID()))
            #end if
            if not bp.IsValid() or bp.GetNumLocations() == 0:
                # pending（零 location）=未验证：谎报 True 会让客户端显示实心
                # 红点却永不命中（真客户端 URI 形状路径实证）
                breakpoints.append({"line": line, "verified": False})
                continue
            #end if
            resolved = line
            if bp.GetNumLocations() > 0:
                # resolved line 经 location 的地址回解析（SBBreakpointLocation 无 GetLineInfo）
                address = bp.GetLocationAtIndex(0).GetAddress()
                if address.IsValid():
                    entry = address.GetLineEntry()
                    if entry.IsValid():
                        resolved = int(entry.GetLine())
                #end if
            #end if
            breakpoints.append({"line": resolved, "verified": True})
        #end for
        return breakpoints
    #end def

    def report_entry_stop(self) -> dict:
        """config_done 原语：入口停上报（entry_stopped→stopped），回 stopped 事件 params。
        thread_id=thread 0 的真 SB id（单线程契约；stopped 线程选取随 continue 砖细化）。
        scope 在册=先自动插桩（入口停内做——PIE 载入地址此时才解析），插桩失败=大声炸。"""
        old = self._exec
        step = step_exec(old, ExecTrigger.REPORT_ENTRY_STOP)
        if step.error is not None:
            raise step.error
        #end if
        if self._scope:
            self._instrument_scope()
        #end if
        self._commit(resolve_exec(old, step, ExecOk()))
        thread = self.process.GetThreadAtIndex(0)
        return {"reason": "entry", "thread_id": int(thread.GetThreadID()),
                "all_threads_stopped": True}
    #end def

    def _instrument_scope(self) -> None:
        """branch-trace 自动插桩：预扫描主模块 code symbols 反汇编，
        is_conditional（E2 识别代数，x86 单表——kind 权威路已裁掉）筛条件站点，
        按载入地址设断并记册（凭册与客户端断点同册——后续 setBreakpoints
        全量替换会清它，MVP 接受）。站点数超阀=scope 滥用，大声失败（M1 规格）。"""
        wanted = (None if self._scope == "module"
                  else {n.strip() for n in self._scope.split(",") if n.strip()})
        module = self.target.GetModuleAtIndex(0)
        addresses: list[int] = []
        seen: set[int] = set()
        for i in range(module.GetNumSymbols()):
            symbol = module.GetSymbolAtIndex(i)
            if symbol.GetType() != int(self.lldb.eSymbolTypeCode):
                continue
            #end if
            name = symbol.GetName() or ""
            if wanted is not None and name not in wanted:
                continue
            #end if
            instructions = symbol.GetInstructions(self.target)
            for k in range(instructions.GetSize()):
                ins = instructions.GetInstructionAtIndex(k)
                if not is_conditional(ins.GetMnemonic(self.target)):
                    continue
                #end if
                address = int(ins.GetAddress().GetLoadAddress(self.target))
                if address in (0, _INVALID_LOAD_ADDR) or address in seen:
                    continue
                #end if
                seen.add(address)
                addresses.append(address)
            #end for
        #end for
        if len(addresses) > _MAX_SITES:
            raise SessionError(SessionErrCode.BAD_ARGS,
                               f"scope {self._scope!r} instruments "
                               f"{len(addresses)} sites (>{_MAX_SITES}); "
                               "narrow the scope")
        #end if
        addresses.sort()
        for address in addresses:
            bp = self.target.BreakpointCreateByAddress(address)
            if bp.IsValid():
                self._own_breakpoint_ids.append(int(bp.GetID()))
            #end if
        #end for
    #end def

    def _read_event(self, ev) -> tuple[bool, int | None, int]:
        """SB 事件取数成 plain 值（wait_state/poll_events 共享）。"""
        lldb = self.lldb
        is_process = bool(lldb.SBProcess.EventIsProcessEvent(ev))
        if is_process:
            proc_id = lldb.SBProcess.GetProcessFromEvent(ev).GetProcessID()
            state = int(lldb.SBProcess.GetStateFromEvent(ev))
        else:
            proc_id, state = None, 0
        #end if
        return is_process, proc_id, state
    #end def

    def wait_state(self, timeout: float = 30.0) -> SessionState:
        """事件排空等待（阻塞版，launch 入口停专用）：取数成 plain 值，
        判定在 classify_event（纯）。"""
        lldb = self.lldb
        ev = lldb.SBEvent()
        seconds = int(timeout)                # SWIG 绑定收 uint32，float 直接 TypeError
        while True:
            if not self.listener.WaitForEvent(seconds, ev):
                raise SessionError(SessionErrCode.EVENT_TIMEOUT,
                                   "no process event in time")
            #end if
            verdict = classify_event(*self._read_event(ev),
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

    def _render_trace(self) -> str:
        """轨迹报告文本（据实序列+节点详情；一致性注记=派生层标注，非记录点处理）。"""
        lines = [f"branch trace: scope={self._scope!r}, "
                 f"{len(self._trace_nodes)} hit(s) in execution order:"]
        for node in self._trace_nodes:
            line = (f"  #{node['order']} 0x{node['address']:x} "
                    f"<{node['function']}> {node['mnemonic']}".rstrip())
            syms = "  ".join(f"{s['name']}={s.get('value', '?')}"
                             f"[{s['annotation']}]" for s in node["symbols"])
            if syms:
                line += f"  {syms}"
            #end if
            lines.append(line)
        #end for
        return "\n".join(lines)
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


def make_backend(lldb_dir: str, target: str) -> Session:
    """端口面工厂入口（session.py 插拔册的值）：lldb_dir=工具链目录参数
    （lldb+lldb-server 同册；不读 PATH、不猜系统目录——2026-10-20 用户裁），
    target=被调试靶路径。debugserver 发现只在给定目录内做（纯查
    find_lldb_server），结果经构造参数注入=LLDB 消费唯一通道
    （LLDB_DEBUGSERVER_PATH 赋值在 launch）。"""
    found = find_lldb_server([lldb_dir])
    if not isinstance(found, ServerFound):
        raise SessionError(SessionErrCode.NO_LLDB_SERVER,
                           f"{found.detail} (lldb_dir={lldb_dir!r})")
    #end if
    return Session(target, debugserver_path=found.path, lldb_dir=lldb_dir)
#end def
