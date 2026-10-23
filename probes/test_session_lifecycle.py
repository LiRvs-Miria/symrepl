#!/usr/bin/env python3
"""test_session_lifecycle — 执行面会话生命周期核直测（不起服务、不起 LLDB，喂 ADT）。

盖 session_lifecycle 核的可纯测行为：守卫矩阵逐行（触发×五相位，冻结现状含
错误码与两轴怪相）、命令转移 step/resolve（提交制：效果成功才提交——
continue 失败臂世代不进=本刀唯一声明行为差）、事件 ingest（StopObserved/
ExitObserved→状态+声明）、poll 短路谓词、铸名值与旧字符串同形（壳迁移对账）。
探针网走真线（probe_*.py=行为冻结证据），本网兜定义级/臂级错（矩阵行写错、
提交制漏臂在核内就地炸）。

契约源=design-notes/server.md「E4 编排代数化契约（2026-10-22）」。
RED 预期：模块缺位=全查 FAIL（纯件新册无桩可对，清单即需求文本）。
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src" / "server"))
from error import SessionErrCode, SessionError

MODULE_OK = True
try:
    from session_lifecycle import (CaptureNode, EmitExit, ExecEffect, ExecFail, ExecOk,
                              ExecPhase, ExecState, ExecTrigger, StopObserved,
                              ExitObserved, guard_exec, ingest_exec, poll_blocked,
                              resolve_exec, step_exec)
except ImportError as e:
    MODULE_OK = False
    _IMPORT_DETAIL = f"{type(e).__name__}: {e}"
#end try

fails = []

def check(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        fails.append(name)
    #end if
#end def


# ---------- 铸名与值同形（壳迁移对账：ExecPhase 值=旧散装字符串） ----------

def phase_values():
    return {p.value for p in ExecPhase} == {"entry", "entry_stopped",
                                            "stopped", "running", "dead"}
#end def

def default_state():
    return (ExecState().phase == ExecPhase.ENTRY
            and ExecState().generation == 0)
#end def


# ---------- 守卫矩阵（冻结现状：threads/set_breakpoints 两轴怪相逐行照抄） ----------

def expected_matrix():
    """trigger → phase → None | SessionErrCode；防御行（端口不可达区）随契约声明。"""
    ns, nl, al = (SessionErrCode.NOT_STOPPED, SessionErrCode.NOT_LAUNCHED,
                  SessionErrCode.ALREADY_LAUNCHED)
    e, es, s, r, d = (ExecPhase.ENTRY, ExecPhase.ENTRY_STOPPED, ExecPhase.STOPPED,
                      ExecPhase.RUNNING, ExecPhase.DEAD)
    return {
        ExecTrigger.LAUNCH:          {e: None, es: al, s: al, r: al, d: al},
        ExecTrigger.CONTINUE:        {e: ns, es: ns, s: None, r: ns, d: ns},
        ExecTrigger.REPORT_ENTRY_STOP: {e: nl, es: None, s: nl, r: nl, d: nl},
        ExecTrigger.STACK_TRACE:     {e: ns, es: None, s: None, r: ns, d: ns},
        ExecTrigger.SCOPES:          {e: ns, es: ns, s: None, r: ns, d: ns},
        ExecTrigger.VARIABLES:       {e: ns, es: ns, s: None, r: ns, d: ns},
        ExecTrigger.THREADS:         {e: nl, es: None, s: None, r: None, d: None},
        ExecTrigger.SET_BREAKPOINTS: {e: nl, es: None, s: None, r: None, d: None},
    }
#end def

def matrix_ok(trigger):
    """全五相位逐格对：None 行=放行（无错误），码行=SessionError 搭配码。"""
    for phase, code in expected_matrix()[trigger].items():
        err = guard_exec(ExecState(phase=phase), trigger)
        if code is None:
            if err is not None:
                return False
            #end if
        elif not (isinstance(err, SessionError) and err.code == code):
            return False
        #end if
    #end for
    return True
#end def


# ---------- 命令转移 step/resolve（提交制：效果成功才提交） ----------

def step_launch():
    st0 = ExecState()
    s = step_exec(st0, ExecTrigger.LAUNCH)
    return (s.error is None and s.effect == ExecEffect.LAUNCH_INFERIOR
            and s.state == ExecState(ExecPhase.ENTRY_STOPPED, 0))
#end def

def resolve_launch():
    st0 = ExecState()
    s = step_exec(st0, ExecTrigger.LAUNCH)
    return (resolve_exec(st0, s, ExecOk()) == ExecState(ExecPhase.ENTRY_STOPPED, 0)
            and resolve_exec(st0, s, ExecFail("launch failed")) == st0)
#end def

def step_finish():
    st0 = ExecState(ExecPhase.ENTRY_STOPPED, 0)
    s = step_exec(st0, ExecTrigger.REPORT_ENTRY_STOP)
    return (s.error is None and s.effect == ExecEffect.FINISH_CONFIG
            and s.state == ExecState(ExecPhase.STOPPED, 0))
#end def

def resolve_finish():
    st0 = ExecState(ExecPhase.ENTRY_STOPPED, 0)
    s = step_exec(st0, ExecTrigger.REPORT_ENTRY_STOP)
    return (resolve_exec(st0, s, ExecOk()) == ExecState(ExecPhase.STOPPED, 0)
            and resolve_exec(st0, s, ExecFail("instrument blew")) == st0)
#end def

def step_continue():
    st0 = ExecState(ExecPhase.STOPPED, 2)
    s = step_exec(st0, ExecTrigger.CONTINUE)
    return (s.error is None and s.effect == ExecEffect.RESUME_INFERIOR
            and s.state == ExecState(ExecPhase.RUNNING, 3))
#end def

def resolve_continue():
    st0 = ExecState(ExecPhase.STOPPED, 2)
    s = step_exec(st0, ExecTrigger.CONTINUE)
    return (resolve_exec(st0, s, ExecOk()) == ExecState(ExecPhase.RUNNING, 3)
            and resolve_exec(st0, s, ExecFail("resume failed")) == st0)
#end def

def step_guard_fail():
    st0 = ExecState(ExecPhase.RUNNING, 1)
    s = step_exec(st0, ExecTrigger.CONTINUE)
    return (isinstance(s.error, SessionError)
            and s.error.code == SessionErrCode.NOT_STOPPED
            and s.state == st0 and s.effect is None)
#end def

def resolve_error_step():
    st0 = ExecState(ExecPhase.RUNNING, 1)
    s = step_exec(st0, ExecTrigger.CONTINUE)
    try:
        resolve_exec(st0, s, ExecOk())
    except AssertionError:
        return True
    return False
#end def

def reads_rejected():
    try:
        step_exec(ExecState(ExecPhase.STOPPED, 0), ExecTrigger.STACK_TRACE)
    except AssertionError:
        return True
    return False
#end def


# ---------- 事件 ingest（双触发源之二：事件即转移） ----------

def ingest_breakpoint_stop():
    g = ingest_exec(ExecState(ExecPhase.RUNNING, 1), StopObserved(reason="breakpoint"))
    return (g.state == ExecState(ExecPhase.STOPPED, 1)
            and g.emits == (CaptureNode(),))
#end def

def ingest_exception_stop():
    g = ingest_exec(ExecState(ExecPhase.STOPPED, 1), StopObserved(reason="exception"))
    return (g.state == ExecState(ExecPhase.STOPPED, 1) and g.emits == ())
#end def

def ingest_entry_stop():
    # 冻结行：现状 poll STOPPED 臂不问来源相位一律落 stopped（entry 停实由
    # wait_state 消费、launch 提交，本行=防御性同形）
    g = ingest_exec(ExecState(ExecPhase.ENTRY_STOPPED, 0), StopObserved(reason="entry"))
    return (g.state == ExecState(ExecPhase.STOPPED, 0) and g.emits == ())
#end def

def ingest_exit():
    g = ingest_exec(ExecState(ExecPhase.RUNNING, 2), ExitObserved(exit_code=3))
    return (g.state == ExecState(ExecPhase.DEAD, 2)
            and g.emits == (EmitExit(),))
#end def


# ---------- poll 短路谓词（dead=冻结真行为；entry=防御行） ----------

def poll_blocked_rows():
    st = ExecState
    return (poll_blocked(st(ExecPhase.DEAD, 0)) is True
            and poll_blocked(st(ExecPhase.ENTRY, 0)) is True
            and poll_blocked(st(ExecPhase.STOPPED, 0)) is False
            and poll_blocked(st(ExecPhase.RUNNING, 0)) is False
            and poll_blocked(st(ExecPhase.ENTRY_STOPPED, 0)) is False)
#end def


CHECKS = [
    ("ExecPhase 五成员且值与旧字符串同形", phase_values),
    ("ExecState 默认=ENTRY+世代 0", default_state),
    ("ExecFail 携 detail 当值", lambda: ExecFail("boom").detail == "boom"),
    ("step LAUNCH：effect=LAUNCH_INFERIOR 提案=ENTRY_STOPPED 世代不动", step_launch),
    ("resolve LAUNCH：Ok 提交 / Fail 留 ENTRY（提交制）", resolve_launch),
    ("step REPORT_ENTRY_STOP：effect=FINISH_CONFIG 提案=STOPPED", step_finish),
    ("resolve REPORT_ENTRY_STOP：Ok 提交 / Fail 留 ENTRY_STOPPED", resolve_finish),
    ("step CONTINUE：effect=RESUME_INFERIOR 提案=RUNNING 世代+1", step_continue),
    ("resolve CONTINUE：Ok 提交 / Fail 留 STOPPED 世代不进（行为差③钉）", resolve_continue),
    ("step 守卫败臂：error 携码、提案=原值、effect 空", step_guard_fail),
    ("resolve 对守卫败臂 step=核误用，大声失败", resolve_error_step),
    ("step 对读命令=核误用（读只走 guard），大声失败", reads_rejected),
    ("ingest 停（breakpoint）：→STOPPED 世代不动+声明 CaptureNode", ingest_breakpoint_stop),
    ("ingest 停（exception）：→STOPPED 无 CaptureNode", ingest_exception_stop),
    ("ingest 停（entry，ENTRY_STOPPED 相位）：→STOPPED（冻结现状不问来源）", ingest_entry_stop),
    ("ingest 退：→DEAD 世代不动+声明 EmitExit", ingest_exit),
    ("poll_blocked：DEAD True（防忙烧）ENTRY True（防御）余 False", poll_blocked_rows),
]
CHECKS += [(f"guard 矩阵逐行：{name}×五相位（冻结现状）",
            lambda n=name: matrix_ok(ExecTrigger[n]))
           for name in ("LAUNCH", "CONTINUE", "REPORT_ENTRY_STOP", "STACK_TRACE",
                        "SCOPES", "VARIABLES", "THREADS", "SET_BREAKPOINTS")]

if __name__ == "__main__":
    if MODULE_OK:
        for name, fn in CHECKS:
            try:
                check(name, fn())
            except Exception as e:
                check(name + f"（网炸 {type(e).__name__}: {e}）", False)
            #end try
        #end for
    else:
        print(f"session_lifecycle 模块缺位（RED 全红基线）：{_IMPORT_DETAIL}")
        for name, _ in CHECKS:
            check(name, False)
        #end for
    #end if
    print("=" * 30)
    print("ALL PASS" if not fails else f"{len(fails)} FAIL: {fails}")
    sys.exit(1 if fails else 0)
#end if
