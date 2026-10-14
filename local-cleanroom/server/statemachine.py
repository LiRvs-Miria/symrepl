"""statemachine — 会话协议状态机（核心，纯函数版，契约 v3）。

pipeline：parse_request（边界解析，dict→类型）→ step（模式匹配状态转移）
→ build_replies（回包+seq 账本）。核内零 I/O、零原地突变、零裸字符串。

状态与转移全部来自 design-notes.md 定稿：事件=转移条件，状态=能待着的东西。
slot 即状态：PendingLaunch 非空 = S2。结果 ADT 验收单=七种结果（契约 v2）。
"""
from enum import Enum, IntEnum
from typing import NamedTuple
from error import InternalErrCode, ProtoError


class StateEnum(IntEnum):
    S0 = 0        # 已连接，未 initialize
    S1 = 1        # initialize 已答，等 launch
    S2 = 2        # launch 扣着（slot 非空），等 configurationDone
    S3 = 3        # 已放行（skeleton 占位）
    DEAD = 4      # 终态
#end class


class Command(Enum):
    # 不用 IntEnum：命令不做算术，误用应报错；拼错成员名=NameError（大声失败）
    INITIALIZE = "initialize"
    LAUNCH = "launch"
    CONFIGURATION_DONE = "configurationDone"
    DISCONNECT = "disconnect"
#end class

def match_command(cmd: str) -> "Command | None":
    """字符串 → 命令枚举。这里是"已处理命令"的宣言清单：
    每个 case 是一次显式的处理承诺；enum 加了成员而这里没 case = 该命令未实现。"""
    match cmd:
        case "initialize":        return Command.INITIALIZE
        case "launch":            return Command.LAUNCH
        case "configurationDone": return Command.CONFIGURATION_DONE
        case "disconnect":        return Command.DISCONNECT
        case _:                   return None
    #end match
#end def

CAPABILITIES = {"supportsConfigurationDoneRequest": True,
                "supportsTerminateRequest": True}


class ParsedRequest(NamedTuple):
    seq: int | None          # 敌意客户端可能缺/坏 seq：如实透传，不假装有
    command: Command | None   # None 仅出现在 unknown 的 Reply 里，step 永远收不到
    raw_command: str          # 原串保留：线上应答回显客户端发的原名
    arguments: dict
#end class


class PendingLaunch(NamedTuple):
    # slot 内容不可变延伸：堵浅拷贝别名漏水（深层只读靠纪律：request 解析后视为只读）
    request: ParsedRequest   # 扣着的 launch 请求；放行时由 build_replies 现场造应答（契约 v2）
#end class


class ProtoState(NamedTuple):
    phase: StateEnum = StateEnum.DEAD
    slot: PendingLaunch | None = None
#end class


class Reply(NamedTuple):
    """一帧应答的语义描述（线上形状由 build_replies 决定）。"""
    request: ParsedRequest
    success: bool
    payload: dict | None = None
    error: ProtoError | None = None
#end class


MaybeParsedRequest = ParsedRequest | Reply   # parse 的输出代数：正常→step，异常→直接回包

class ExpectState(NamedTuple):
    state: ProtoState
    replies: tuple[Reply, ...] = ()   # 0 帧=扣/无话，1 帧=应答，2 帧=放行
#end class


# ---------- 边界解析（parse, don't validate） ----------

def parse_request(msg: dict) -> MaybeParsedRequest:
    """dict → ParsedRequest | Reply。数据清洗：正常请求→step，异常→直接接到返回报文。"""
    cmd = msg.get("command")
    command = match_command(cmd) if isinstance(cmd, str) else None
    if command is None:
        req = ParsedRequest(seq=msg.get("seq"), command=None,
                            raw_command=cmd if isinstance(cmd, str) else repr(cmd),
                            arguments={})
        return err_reply(req, InternalErrCode.BAD_PROTOCOL,
                         "not implemented", f"unknown command {cmd!r}")
    #end if
    args = msg.get("arguments")
    return ParsedRequest(seq=msg.get("seq"), command=command, raw_command=cmd,
                         arguments=args if isinstance(args, dict) else {})
#end def


# ---------- 应答构造助手 ----------

def ok_reply(request: ParsedRequest, payload: dict | None = None) -> Reply:
    return Reply(request=request, success=True, payload=payload)
#end def

def err_reply(request: ParsedRequest, code: InternalErrCode, message: str, detail: str) -> Reply:
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

def step(pstate: ProtoState, req: ParsedRequest) -> ExpectState:
    phase = pstate.phase
    match phase, req.command:
        case (StateEnum.S0, Command.INITIALIZE):
            return ExpectState(pstate._replace(phase=StateEnum.S1),
                               (ok_reply(req, CAPABILITIES),))

        case (StateEnum.S1, Command.LAUNCH):
            e = validate_launch(req.arguments)
            if e is not None:
                return ExpectState(pstate,
                                   (err_reply(req, InternalErrCode.BAD_PROTOCOL,
                                              "bad launch parameters", e),))
            #end if
            # 放：请求入 slot，扣响应（线上静默——设计灵魂）
            return ExpectState(pstate._replace(phase=StateEnum.S2,
                                               slot=PendingLaunch(request=req)), ())

        case (StateEnum.S2, Command.CONFIGURATION_DONE):
            pending = pstate.slot
            if pending is None:   # 不变量说 S2 蕴含非空；防御一把
                return ExpectState(pstate,
                                   (err_reply(req, InternalErrCode.EVENT_NOT_ALLOWED,
                                              "event not allowed in current state",
                                              "no launch in flight"),))
            #end if
            # 放行：launch 应答从 stash 的 request 现场构造（契约 v2），launch 先发（偏离#6）
            return ExpectState(pstate._replace(phase=StateEnum.S3, slot=None),
                               (ok_reply(pending.request, {}), ok_reply(req, {})))

        case (_, Command.DISCONNECT):
            # 断路器：任何状态无守卫直达终态（偏离#5）；清 slot=丢弃整个状态
            return ExpectState(pstate._replace(phase=StateEnum.DEAD, slot=None),
                               (ok_reply(req, {}),))

        case (StateEnum.S2, Command.LAUNCH):
            # 双发：专属 detail（转移表 S2 行，R7 教训——泛化分支会吞掉它）
            return ExpectState(pstate,
                               (err_reply(req, InternalErrCode.EVENT_NOT_ALLOWED,
                                          "event not allowed in current state",
                                          "launch_already_pending"),))

        case _:
            # 状态门 + 占位态：该事件在当前状态不允许（偏离#1 状态门、#2 重入拒）
            return ExpectState(pstate,
                               (err_reply(req, InternalErrCode.EVENT_NOT_ALLOWED,
                                          "event not allowed in current state",
                                          f"{req.raw_command} not allowed in {phase.name}"),))
    #end match
#end def


# ---------- build_replies：回包段（seq 账本在此） ----------

def build_replies(replies: tuple[Reply, ...], seq: int):
    # 线上形状在此定型：错误帧 body={code, detail}，成功帧 body=payload
    frames = []
    for r in replies:
        m = {"seq": seq, "type": "response", "request_seq": r.request.seq,
             "command": r.request.raw_command, "success": r.success}
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
