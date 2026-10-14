"""statemachine — 会话协议状态机（核心，纯函数版，契约 v2）。

step 专职状态转移（模式匹配分发），build_replies 专职回包（seq 账本在此）；
slot 存 PendingLaunch(request) 不存预造帧；核内零 I/O、零原地突变。
结果 ADT 的验收单=七种结果（design-notes 契约 v2）。
"""
from enum import IntEnum
from typing import NamedTuple
from error import InternalErrCode, ProtoError


class StateEnum(IntEnum):
    S0 = 0        # 已连接，未 initialize
    S1 = 1        # initialize 已答，等 launch
    S2 = 2        # launch 扣着（slot 非空），等 configurationDone
    S3 = 3        # 已放行（skeleton 占位）
    DEAD = 4      # 终态
#end class

CAPABILITIES = {"supportsConfigurationDoneRequest": True,
                "supportsTerminateRequest": True}

KNOWN_COMMANDS = frozenset({"initialize", "launch", "configurationDone", "disconnect"})


class PendingLaunch(NamedTuple):
    # slot 内容不可变延伸：堵浅拷贝别名漏水（深层只读靠纪律：request 解析后视为只读）
    request: dict            # 扣着的 launch 请求；放行时由 build_replies 现场造应答（契约 v2）
#end class


class ProtoState(NamedTuple):
    phase: StateEnum = StateEnum.DEAD
    slot: PendingLaunch | None = None
#end class


class Reply(NamedTuple):
    """一帧应答的语义描述（线上形状由 build_replies 决定）。"""
    request: dict
    success: bool
    payload: dict | None = None
    error: ProtoError | None = None
#end class


class ExpectState(NamedTuple):
    state: ProtoState
    replies: tuple[Reply, ...] = ()   # 0 帧=扣/无话，1 帧=应答，2 帧=放行
#end class


# ---------- 应答构造助手 ----------

def ok_reply(request: dict, payload: dict | None = None) -> Reply:
    return Reply(request=request, success=True, payload=payload)
#end def

def err_reply(request: dict, code: InternalErrCode, message: str, detail: str) -> Reply:
    # A1 分层：粗类码 + 现象在 message，原因在 detail
    return Reply(request=request, success=False,
                 error=ProtoError(code=code, message=message, detail={"detail": detail}))
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


# ---------- step：模式匹配分发（转移表逐行转录） ----------

def step(pstate: ProtoState, msg: dict) -> ExpectState:
    cmd = msg.get("command")
    if cmd not in KNOWN_COMMANDS:
        # 未知命令回规范错误（对齐 §5 实证），分发层处理，不过状态机
        return ExpectState(pstate,
                           (err_reply(msg, InternalErrCode.BAD_PROTOCOL,
                                      "not implemented", f"unknown command {cmd!r}"),))
    #end if

    phase = pstate.phase
    match phase, cmd:
        case (StateEnum.S0, "initialize"):
            return ExpectState(pstate._replace(phase=StateEnum.S1),
                               (ok_reply(msg, CAPABILITIES),))

        case (StateEnum.S1, "launch"):
            e = validate_launch(msg.get("arguments") or {})
            if e is not None:
                return ExpectState(pstate,
                                   (err_reply(msg, InternalErrCode.BAD_PROTOCOL,
                                              "bad launch parameters", e),))
            #end if
            # 放：请求入 slot，扣响应（线上静默——设计灵魂）
            return ExpectState(pstate._replace(phase=StateEnum.S2,
                                               slot=PendingLaunch(request=msg)), ())

        case (StateEnum.S2, "configurationDone"):
            pending = pstate.slot
            if pending is None:   # 不变量说 S2 蕴含非空；防御一把
                return ExpectState(pstate,
                                   (err_reply(msg, InternalErrCode.EVENT_NOT_ALLOWED,
                                              "event not allowed in current state",
                                              "no launch in flight"),))
            #end if
            # 放行：launch 应答现场构造（存 request 不存帧——契约 v2），launch 先发（偏离#6）
            return ExpectState(pstate._replace(phase=StateEnum.S3, slot=None),
                               (ok_reply(pending.request, {}), ok_reply(msg, {})))

        case (_, "disconnect"):
            # 断路器：任何状态无守卫直达终态（偏离#5）；清 slot=丢弃整个状态
            return ExpectState(pstate._replace(phase=StateEnum.DEAD, slot=None),
                               (ok_reply(msg, {}),))

        case (StateEnum.S2, "launch"):
            # 双发：专属 detail（转移表 S2 行，泛化分支会吞掉它）
            return ExpectState(pstate,
                               (err_reply(msg, InternalErrCode.EVENT_NOT_ALLOWED,
                                          "event not allowed in current state",
                                          "launch_already_pending"),))

        case (_, "launch") | (_, "initialize") | (_, "configurationDone"):
            # 状态门：该事件在当前状态不允许（有意偏离参考状态盲，偏离#1；重入拒，偏离#2）
            return ExpectState(pstate,
                               (err_reply(msg, InternalErrCode.EVENT_NOT_ALLOWED,
                                          "event not allowed in current state",
                                          f"{cmd} not allowed in {phase.name}"),))

        case _:
            # S3 占位与一切未定义组合：占位态不接待客（出②范围）
            return ExpectState(pstate,
                               (err_reply(msg, InternalErrCode.EVENT_NOT_ALLOWED,
                                          "event not allowed in current state",
                                          f"no transition from {phase.name}"),))
    #end match
#end def


# ---------- build_replies：回包段（seq 账本在此） ----------

def build_replies(replies: tuple[Reply, ...], seq: int):
    # 线上形状在此定型：错误帧 body={code, detail}，成功帧 body=payload
    frames = []
    for r in replies:
        m = {"seq": seq, "type": "response", "request_seq": r.request["seq"],
             "command": r.request["command"], "success": r.success}
        seq += 1
        if r.success:
            if r.payload is not None:
                m["body"] = r.payload
            #end if
        else:
            e = r.error
            if e is None:   # 不变量：失败应答必携 ProtoError
                raise ValueError("failed reply missing ProtoError")
            #end if
            m["message"] = e.message
            m["body"] = {"code": int(e.code), "detail": (e.detail or {}).get("detail")}
        #end if
        frames.append(m)
    #end for
    return frames, seq
#end def
