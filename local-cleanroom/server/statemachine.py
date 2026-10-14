"""statemachine — 会话协议状态机（核心，转移表的转录）。

状态与转移全部来自 design-notes.md 定稿：事件=转移条件，状态=能待着的东西。
slot 即状态：pending_launch 非空 = S2。处理器纯字典进/字典出，不做 I/O（FC/IS 分层）。
"""
import asyncio
from dataclasses import dataclass, field

from error import InternalErrCode


# 协议状态（S3=放行后，skeleton 占位出②范围）
class Phase:
    S0 = "S0"        # 已连接，未 initialize
    S1 = "S1"        # initialize 已答，等 launch
    S2 = "S2"        # launch 扣着（slot 非空），等 configurationDone
    S3 = "S3"        # 已放行
    DEAD = "dead"    # 终态
#end class

CAPABILITIES = {"supportsConfigurationDoneRequest": True,
                "supportsTerminateRequest": True}


@dataclass
class State:
    # 协议侧
    phase: str = Phase.S0
    slot: dict | None = None     # pending_launch：放=launch / 取=configDone / 非空=拒双发 / 清=收场
    next_seq: int = 1            # server 侧帧 seq
    # 连接侧（壳使用；放这里避免双 State 对账）
    active_writer: asyncio.StreamWriter | None = None
    client_done: asyncio.Event = field(default_factory=asyncio.Event)
    exit_code: int = 0           # TransExitCode 的 int 值（FIN=0 / ABRUPT=2）
#end class


# ---------- 响应构造（A1 分层：粗类码在外，detail 在内） ----------

def response(state: State, req: dict, success: bool, body=None, message=None) -> dict:
    m = {"seq": state.next_seq, "type": "response", "request_seq": req["seq"],
         "command": req["command"], "success": success}
    state.next_seq += 1
    if body is not None:
        m["body"] = body
    if message is not None:
        m["message"] = message
    return m
#end def


def state_error(state: State, req: dict, detail: str) -> dict:
    # 状态不转移 ≠ 不回话：粗类 EVENT_NOT_ALLOWED，细节进 detail
    return response(state, req, False,
                    body={"code": int(InternalErrCode.EVENT_NOT_ALLOWED), "detail": detail},
                    message="event not allowed in current state")
#end def


def param_error(state: State, req: dict, detail: str) -> dict:
    return response(state, req, False,
                    body={"code": int(InternalErrCode.BAD_PROTOCOL), "detail": detail},
                    message="bad launch parameters")
#end def


# ---------- 事件处理器 ----------

def on_initialize(state: State, req: dict):
    if state.phase != Phase.S0:
        # 纯洁性决策：initialize 重入拒（有意偏离 §4-2 实证，偏离清单#2）
        return state_error(state, req, detail=f"initialize not allowed in {state.phase}")
    #end if
    state.phase = Phase.S1
    return response(state, req, True, body=CAPABILITIES)
#end def


def validate_launch(args: dict):
    for key in ("ktest", "target"):
        v = args.get(key)
        if not isinstance(v, str) or not v:
            return f"launch requires a '{key}' path string"
        # 文件存在性检查未做（无实证决策，标注见 design-notes）
    #end for
    return None
#end def


def on_launch(state: State, req: dict):
    if state.phase == Phase.S2:          # slot 非空=双发（状态本身即守卫）
        return state_error(state, req, detail="launch_already_pending")
    #end if
    if state.phase != Phase.S1:          # 状态门（有意偏离参考状态盲，偏离清单#1）
        return state_error(state, req, detail=f"launch not allowed in {state.phase}")
    #end if
    e = validate_launch(req.get("arguments") or {})
    if e:
        return param_error(state, req, e)
    #end if
    # 放：launch + 扣着的响应一起入 slot，不回包（扣响应=设计灵魂）
    state.slot = {"request": req, "response": response(state, req, True, body={})}
    state.phase = Phase.S2
    return None
#end def


def on_configurationDone(state: State, req: dict):
    pending = state.slot
    if state.phase != Phase.S2 or pending is None:
        return state_error(state, req, detail="no launch in flight")
    #end if
    state.slot = None                    # 取
    state.phase = Phase.S3
    # 本设计：launch 响应先发（有意偏离参考——OQ-1，偏离清单#6）
    return [pending["response"], response(state, req, True, body={})]
#end def


def on_disconnect(state: State, req: dict):
    # 断路器：任何状态无守卫直达终态（偏离清单#5，对齐实证）
    state.slot = None                    # 清：launch 交易作废（语义层，非链接死亡）
    state.phase = Phase.DEAD
    return response(state, req, True, body={})
#end def


HANDLERS = {
    "initialize": on_initialize,
    "launch": on_launch,
    "configurationDone": on_configurationDone,
    "disconnect": on_disconnect,
}


def dispatch(state: State, msg: dict) -> list:
    if msg.get("type") != "request":
        return []                        # server 不该收到事件帧；静默忽略（日志由壳打）
    #end if
    cmd = msg.get("command")
    handler = HANDLERS.get(cmd) if isinstance(cmd, str) else None
    if handler is None:
        # 未知命令回规范错误（对齐 §5 实证），分发层处理，不过状态机
        return [response(state, msg, False,
                         body={"code": int(InternalErrCode.BAD_PROTOCOL),
                               "detail": f"unknown command {cmd!r}"},
                         message="not implemented")]
    #end if
    out = handler(state, msg)
    if out is None:
        return []
    return out if isinstance(out, list) else [out]
#end def
