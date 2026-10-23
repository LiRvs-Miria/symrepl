"""session_lifecycle — 执行面会话生命周期核（纯核，v5 家法对齐协议面 adapter）。

调试器后端可插拔（session.py 端口面），会话生命周期由端口契约定义、后端无关：
相位字母表、守卫矩阵、转移提交制、事件落靶全部住本册；*_backend.py 是壳——
取数成 plain 值→guard→执行效果→resolve 提交。核内零 I/O、零原地突变、
零裸字符串相位（ExecPhase ADT）。

双触发源：命令转移（step_exec/resolve_exec——效果成功才提交）与事件转移
（ingest_exec——观测事实即落靶）。守卫矩阵是唯一权威表：step 转移行与
读命令白名单同源（_GUARDS），不两处维护。
"""
from __future__ import annotations

from enum import Enum
from typing import NamedTuple, assert_never

from error import SessionErrCode, SessionError


class ExecPhase(Enum):
    """会话相位字母表（值=线上可读名；壳迁移对账用同形）。"""
    ENTRY = "entry"                    # 已构造，未 launch
    ENTRY_STOPPED = "entry_stopped"    # 入口停已到手，等 config_done
    STOPPED = "stopped"                # 停相（可读/可续）
    RUNNING = "running"                # 已放行，完成以事件到达
    DEAD = "dead"                      # 终态（exited/crashed）
#end class


class ExecState(NamedTuple):
    """生命周期状态值：相位+世代票据。世代是守卫输入（stale 判定）故入核；
    引用账本/断点表/轨迹节点是载荷数据，归壳私。"""
    phase: ExecPhase = ExecPhase.ENTRY
    generation: int = 0
#end class


class ExecTrigger(Enum):
    """端口面原语的触发词汇（十一命令；poll_events 是泵不走守卫门）。"""
    LAUNCH = "launch"
    CONTINUE = "continue"
    STEP_NEXT = "step_next"
    STEP_IN = "step_in"
    STEP_OUT = "step_out"
    REPORT_ENTRY_STOP = "report_entry_stop"
    THREADS = "threads"
    STACK_TRACE = "stack_trace"
    SCOPES = "scopes"
    VARIABLES = "variables"
    SET_BREAKPOINTS = "set_breakpoints"
#end class


class ExecEffect(Enum):
    """壳效果词汇（核声明，壳执行；效果成败由 resolve 落靶）。"""
    LAUNCH_INFERIOR = "launch_inferior"      # 起 inferior 至入口停（阻塞等停归壳）
    RESUME_INFERIOR = "resume_inferior"      # 放行（异步，完成以事件到达）
    FINISH_CONFIG = "finish_config"          # 收配置（含按需插桩）
#end class


class ExecOk(NamedTuple):
    """效果成功。"""
#end class


class ExecFail(NamedTuple):
    """效果失败（壳报物理事实；码/话术归壳路异常面）。"""
    detail: str
#end class


ExecOutcome = ExecOk | ExecFail


class StopObserved(NamedTuple):
    """停相观测事实（reason 已由壳侧纯核推断：breakpoint/exception/entry）。"""
    reason: str
#end class


class ExitObserved(NamedTuple):
    """收场观测事实（exit_code=GetExitStatus 语义值）。"""
    exit_code: int
#end class


ExecFact = StopObserved | ExitObserved


class CaptureNode(NamedTuple):
    """取数意图：断点命中节点据实记录（pc/函数/助记符/调用栈/符号值）。"""
#end class


class EmitExit(NamedTuple):
    """发帧意图：收场事件（报告附否=壳按 scope 在册决定）。"""
#end class


class ExecStep(NamedTuple):
    """step_exec 输出：state=提案（resolve 提交，非当场生效）；守卫败臂提案=原值。"""
    state: ExecState
    effect: ExecEffect | None = None
    error: SessionError | None = None
#end class


class ExecIngest(NamedTuple):
    """ingest_exec 输出：state=落靶后状态（事件转移当场生效）；emits=取数/发帧意图。"""
    state: ExecState
    emits: tuple = ()
#end class


# ---------- 守卫矩阵（唯一权威表：行=触发器，格=放行相位集+拒绝码+话术） ----------

_GUARDS: dict[ExecTrigger, tuple[frozenset[ExecPhase], SessionErrCode, str]] = {
    ExecTrigger.LAUNCH: (
        frozenset({ExecPhase.ENTRY}),
        SessionErrCode.ALREADY_LAUNCHED, "launch"),
    ExecTrigger.CONTINUE: (
        frozenset({ExecPhase.STOPPED}),
        SessionErrCode.NOT_STOPPED, "continue"),
    ExecTrigger.STEP_NEXT: (
        frozenset({ExecPhase.STOPPED}),
        SessionErrCode.NOT_STOPPED, "step over"),
    ExecTrigger.STEP_IN: (
        frozenset({ExecPhase.STOPPED}),
        SessionErrCode.NOT_STOPPED, "step into"),
    ExecTrigger.STEP_OUT: (
        frozenset({ExecPhase.STOPPED}),
        SessionErrCode.NOT_STOPPED, "step out"),
    ExecTrigger.REPORT_ENTRY_STOP: (
        frozenset({ExecPhase.ENTRY_STOPPED}),
        SessionErrCode.NOT_LAUNCHED, "finish configuration"),
    ExecTrigger.STACK_TRACE: (
        frozenset({ExecPhase.STOPPED, ExecPhase.ENTRY_STOPPED}),
        SessionErrCode.NOT_STOPPED, "read the stack"),
    ExecTrigger.SCOPES: (
        frozenset({ExecPhase.STOPPED}),
        SessionErrCode.NOT_STOPPED, "read scopes"),
    ExecTrigger.VARIABLES: (
        frozenset({ExecPhase.STOPPED}),
        SessionErrCode.NOT_STOPPED, "read variables"),
    ExecTrigger.THREADS: (
        frozenset({ExecPhase.ENTRY_STOPPED, ExecPhase.STOPPED,
                   ExecPhase.RUNNING, ExecPhase.DEAD}),
        SessionErrCode.NOT_LAUNCHED, "list threads"),
    ExecTrigger.SET_BREAKPOINTS: (
        frozenset({ExecPhase.ENTRY_STOPPED, ExecPhase.STOPPED,
                   ExecPhase.RUNNING, ExecPhase.DEAD}),
        SessionErrCode.NOT_LAUNCHED, "place breakpoints"),
}
#end dict

# 转移行（只列状态变迁者；读命令无转移，核误用大声失败）：
# 行=触发器 →（效果，靶态，世代增量）——放行相位集查 _GUARDS 同行。
_TRANSITIONS: dict[ExecTrigger, tuple[ExecEffect, ExecPhase, int]] = {
    ExecTrigger.LAUNCH: (ExecEffect.LAUNCH_INFERIOR, ExecPhase.ENTRY_STOPPED, 0),
    ExecTrigger.REPORT_ENTRY_STOP: (ExecEffect.FINISH_CONFIG, ExecPhase.STOPPED, 0),
    ExecTrigger.CONTINUE: (ExecEffect.RESUME_INFERIOR, ExecPhase.RUNNING, 1),
    ExecTrigger.STEP_NEXT: (ExecEffect.RESUME_INFERIOR, ExecPhase.RUNNING, 1),
    ExecTrigger.STEP_IN: (ExecEffect.RESUME_INFERIOR, ExecPhase.RUNNING, 1),
    ExecTrigger.STEP_OUT: (ExecEffect.RESUME_INFERIOR, ExecPhase.RUNNING, 1),
}
#end dict


def guard_exec(state: ExecState, trigger: ExecTrigger) -> SessionError | None:
    """相位守卫判定（纯）：矩阵查行，不当相位=SessionError 当值返回。"""
    row = _GUARDS.get(trigger)
    if row is None:
        raise AssertionError(f"unlisted trigger: {trigger!r}")
    #end if
    allowed, code, action = row
    if state.phase not in allowed:
        expected = tuple(sorted(p.value for p in allowed))
        return SessionError(code, f"cannot {action} in phase {state.phase.value!r} "
                                  f"(expected one of {expected})")
    #end if
    return None
#end def


def step_exec(state: ExecState, trigger: ExecTrigger) -> ExecStep:
    """命令转移声明（纯）：守卫过→提案（靶态+世代增量）+效果声明，提交归
    resolve_exec；守卫败→error 搭提案=原值、effect 空。读命令不转移=核误用
    大声失败（读只走 guard_exec）。"""
    row = _TRANSITIONS.get(trigger)
    if row is None:
        raise AssertionError(f"non-transitioning trigger in step_exec: {trigger!r}")
    #end if
    err = guard_exec(state, trigger)
    if err is not None:
        return ExecStep(state=state, error=err)
    #end if
    effect, target, gen_delta = row
    return ExecStep(state=ExecState(phase=target,
                                    generation=state.generation + gen_delta),
                    effect=effect)
#end def


def resolve_exec(state: ExecState, step: ExecStep, outcome: ExecOutcome) -> ExecState:
    """转移提交（纯）：效果成→提交提案；败→原值原样（世代不进=提交制语义）。
    守卫败臂 step 无提案可提=核误用大声失败。"""
    if step.error is not None:
        raise AssertionError(f"no proposal to resolve: {step.error!r}")
    #end if
    match outcome:
        case ExecOk():
            return step.state
        case ExecFail():
            return state
        case _:
            assert_never(outcome)
    #end match
#end def


def ingest_exec(state: ExecState, fact: ExecFact) -> ExecIngest:
    """事件转移（纯）：观测事实落靶+声明。停相不问来源相位一律落 STOPPED
    （入口停由 launch 路消费，不经本口）；断点命中声明 CaptureNode 取数意图；
    收场落 DEAD+声明 EmitExit。世代是 continue 专属，事件不触碰。"""
    match fact:
        case StopObserved(reason="breakpoint"):
            return ExecIngest(state=ExecState(ExecPhase.STOPPED, state.generation),
                              emits=(CaptureNode(),))
        case StopObserved():
            return ExecIngest(state=ExecState(ExecPhase.STOPPED, state.generation))
        case ExitObserved():
            return ExecIngest(state=ExecState(ExecPhase.DEAD, state.generation),
                              emits=(EmitExit(),))
        case _:
            assert_never(fact)
    #end match
#end def


def poll_blocked(state: ExecState) -> bool:
    """事件泵短路谓词（纯）：终态不再产事件（防忙烧）；entry 相位无可取事件源。"""
    return state.phase in (ExecPhase.DEAD, ExecPhase.ENTRY)
#end def
