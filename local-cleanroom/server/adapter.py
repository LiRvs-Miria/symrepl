"""adapter — DAP 适配器的协议面（核心，纯函数版，契约 v4.1）。

业务角色=适配器：对客户端统一 DAP 语义（能力/生命周期/会话状态机）；
实现载体=纯函数状态机（parse_request → step → build_replies 三段管线）。
核内零 I/O、零原地突变、零裸字符串。

状态与转移全部来自 design-notes.md 定稿：事件=转移条件，状态=能待着的东西。
slot 即状态：PendingLaunch 非空 = S2。结果代数=Frame=Reply|Event（契约 v4/v4.1）：
语义由构造点声明，帧数不携带语义；非法状态不可表示（失败必携 ProtoError 是
构造期事实，非运行时检查）；七种结果对号表见 design-notes。
"""
from enum import Enum, IntEnum
from typing import NamedTuple, assert_never
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


class OkReply(NamedTuple):
    """应答（成功）：payload=None=线上无 body。"""
    request: ParsedRequest
    payload: dict | None = None
#end class


class ErrReply(NamedTuple):
    """应答（失败）：error 必携——构造期强制，非法状态不可表示（契约 v4.1）。"""
    request: ParsedRequest
    error: ProtoError
#end class


Reply = OkReply | ErrReply   # 一帧应答的代数（契约 v4.1）：success 布尔退役，劈为两变体


MaybeParsedRequest = ParsedRequest | Reply   # parse 的输出代数：正常→step，异常→直接回包


class Event(NamedTuple):
    event: str               # DAP 事件名（stopped/output/exited…）
    body: dict | None = None
#end class


Frame = Reply | Event        # 线上帧的代数（契约 v4）：语义由构造点声明；Event 今日无生产者（dead until 阶段 2/3）


# ---------- 动作与效果（核声明，壳执行） ----------

class FulfillLaunch(NamedTuple):
    """launch 履约动作：spawn 会话子进程→ping 握手→递 launch 单。
    req 携单（先行诊断已过，两键保非空串）。"""
    req: ParsedRequest
#end class

class ForwardRequest(NamedTuple):
    """调试命令薄代理：父收单转递对端，应答原路回。
    cmd/params=子进程侧命令（核定名，壳盲发）；req=原始 DAP 请求（resolve 造应答用）。"""
    req: ParsedRequest
    cmd: str
    params: dict
#end class

class TearDownChild(NamedTuple):
    """收场拆除会话子进程：优雅窗→强杀（未接——砖4 disconnect 首撞落位）。"""
#end class

Action = FulfillLaunch | ForwardRequest | TearDownChild
                             # 动作词汇表；执行侧 match 须穷尽（未接成员=显式大声失败）


class Done(NamedTuple):
    """对端按单应答（ok=True）。result 原样上交：内容壳不看，形状闸在 resolve。"""
    result: dict
#end class

class TimedOut(NamedTuple):
    """约期无应答。"""
    detail: str
#end class

class Gone(NamedTuple):
    """在途失联：EOF/管道断/发前已死。"""
    detail: str
#end class

class SpawnFailed(NamedTuple):
    """对端进程没起来。"""
    detail: str
#end class

class Rejected(NamedTuple):
    """对端明确拒单（ok=False 应答）。"""
    detail: str
#end class

class Crashed(NamedTuple):
    """执行器未预期异常（默认臂）：差事没走完，责任在父侧执行。"""
    detail: str
#end class

Outcome = Done | TimedOut | Gone | SpawnFailed | Rejected | Crashed
                             # 壳只报物理事实（异常→构造器）；码/话术/靶态归 resolve


class StepResult(NamedTuple):
    state: ProtoState
    frames: tuple[Frame, ...] = ()   # 帧数不携带语义：扣的静默归 slot 承载（slot 即 S2），放行=构造点排好的双帧
    action: Action | None = None     # 动作槽：非空=转移在途，落靶由 resolve 按 Outcome 选
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
    assert isinstance(cmd, str)   # command 非 None 蕴含 cmd 是 str（match_command 只认 str）；窄化给检查器
    return ParsedRequest(seq=msg.get("seq"), command=command, raw_command=cmd,
                         arguments=args if isinstance(args, dict) else {})
#end def


# ---------- 应答构造助手 ----------

def err_reply(request: ParsedRequest, code: InternalErrCode, message: str, detail: str) -> ErrReply:
    # A1 分层：粗类码 + 现象在 message，原因在 detail
    return ErrReply(request=request,
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

def step(pstate: ProtoState, req: ParsedRequest) -> StepResult:
    phase = pstate.phase
    match phase, req.command:
        case (StateEnum.S0, Command.INITIALIZE):
            return StepResult(state=pstate._replace(phase=StateEnum.S1),
                              frames=(OkReply(request=req, payload=CAPABILITIES),))

        case (StateEnum.S1, Command.LAUNCH):
            e = validate_launch(req.arguments)
            if e is not None:
                return StepResult(state=pstate,
                                  frames=(err_reply(req, InternalErrCode.BAD_PROTOCOL,
                                                    "bad launch parameters", e),))
            #end if
            # 诊断过：自回环驻 S1，履约交壳执行，落靶归 resolve（成→S2+slot，败→留 S1 可重递）。
            # 请求不在此入 slot："slot 非空=S2"不变量原样保留
            return StepResult(state=pstate, action=FulfillLaunch(req))

        case (StateEnum.S2, Command.CONFIGURATION_DONE):
            pending = pstate.slot
            if pending is None:   # 不变量说 S2 蕴含非空；防御一把
                return StepResult(state=pstate,
                                  frames=(err_reply(req, InternalErrCode.EVENT_NOT_ALLOWED,
                                                    "event not allowed in current state",
                                                    "no launch in flight"),))
            #end if
            # 放行先递对端（对齐参考 server.py:461 config_done 先于双帧）：携动作自回环驻 S2，
            # slot 原样扣着；落靶归 resolve（成→S3+双帧，败→双帧皆败）
            return StepResult(state=pstate,
                              action=ForwardRequest(req=req, cmd="config_done", params={}))

        case (_, Command.DISCONNECT):
            # 断路器：任何状态无守卫直达终态（偏离#5）；清 slot=丢弃整个状态；
            # 帧序=响应先、terminated 后（2026-10-15 裁决 a：两门形状统一，对齐参考实测）
            return StepResult(state=pstate._replace(phase=StateEnum.DEAD, slot=None),
                              frames=(OkReply(request=req, payload={}),
                                      Event(event="terminated", body={})))

        case (StateEnum.S2, Command.LAUNCH):
            # 双发：专属 detail（转移表 S2 行，R7 教训——泛化分支会吞掉它）
            return StepResult(state=pstate,
                              frames=(err_reply(req, InternalErrCode.EVENT_NOT_ALLOWED,
                                                "event not allowed in current state",
                                                "launch_already_pending"),))

        case _:
            # 状态门 + 占位态：该事件在当前状态不允许（偏离#1 状态门、#2 重入拒）
            return StepResult(state=pstate,
                              frames=(err_reply(req, InternalErrCode.EVENT_NOT_ALLOWED,
                                                "event not allowed in current state",
                                                f"{req.raw_command} not allowed in {phase.name}"),))
    #end match
#end def


# ---------- resolve：choice 落靶（在途转移按 Outcome 定靶与帧；码/话术/靶态全在此，壳无语义） ----------

def _refused(pstate: ProtoState, req: ParsedRequest, message: str, detail: str) -> StepResult:
    """2002 败臂：留 S1 可重递；slot 从未建立，状态账自动干净。"""
    return StepResult(state=pstate,
                      frames=(err_reply(req, InternalErrCode.UPSTREAM_REFUSED, message, detail),))
#end def


def _release_failed(pstate: ProtoState, req: ParsedRequest,
                    code: InternalErrCode, message: str, detail: str) -> StepResult:
    """放行败臂：双帧皆败（slot 扣的 launch 单+configDone），留 S2+slot 可重递
    （立场乙同类；参考烧 slot 的形状不取）。"""
    launch_req = pstate.slot.request if pstate.slot is not None else req
    return StepResult(state=pstate,
                      frames=(err_reply(launch_req, code, message, detail),
                              err_reply(req, code, message, detail)))
#end def


def resolve(pstate: ProtoState, action: Action, outcome: Outcome) -> StepResult:
    """完成在途转移：action×outcome 逐对落靶。占位动作在此即大声失败（未被机器声明过）。"""
    match action, outcome:
        case (FulfillLaunch(req), Done(result)):
            pid = result.get("pid")
            if not isinstance(pid, int):          # 形状闸：管对面的承诺不兑现=拒单
                return _refused(pstate, req, "session refused launch",
                                f"launch result pid is not an int: {pid!r}")
            #end if
            return StepResult(
                state=pstate._replace(phase=StateEnum.S2, slot=PendingLaunch(request=req)),
                frames=(Event(event="process",
                              body={"systemProcessId": pid,
                                    "isLocalProcess": True,
                                    "startMethod": "launch",
                                    "name": req.arguments["target"]}),
                        Event(event="initialized", body={})))
        case (FulfillLaunch(req), TimedOut(detail)):
            return StepResult(state=pstate,
                              frames=(err_reply(req, InternalErrCode.UPSTREAM_TIMEOUT,
                                                "session did not answer launch in time", detail),))
        case (FulfillLaunch(req), Gone(detail)) | (FulfillLaunch(req), SpawnFailed(detail)):
            return _refused(pstate, req, "session unavailable", detail)
        case (FulfillLaunch(req), Rejected(detail)):
            return _refused(pstate, req, "session refused launch", detail)
        case (FulfillLaunch(req), Crashed(detail)):
            return _refused(pstate, req, "launch errand crashed", detail)
        case (ForwardRequest(req), Done()):
            pending = pstate.slot
            if pending is None:   # 不变量说 S2 蕴含非空；防御一把
                return StepResult(state=pstate,
                                  frames=(err_reply(req, InternalErrCode.EVENT_NOT_ALLOWED,
                                                    "event not allowed in current state",
                                                    "no launch in flight"),))
            #end if
            # 放行双帧：launch 应答从 slot 扣单现场构造，launch 先发
            return StepResult(state=pstate._replace(phase=StateEnum.S3, slot=None),
                              frames=(OkReply(request=pending.request, payload={}),
                                      OkReply(request=req, payload={})))
        case (ForwardRequest(req), TimedOut(detail)):
            return _release_failed(pstate, req, InternalErrCode.UPSTREAM_TIMEOUT,
                                   "session did not answer configurationDone in time", detail)
        case (ForwardRequest(req), Gone(detail)) | (ForwardRequest(req), SpawnFailed(detail)):
            return _release_failed(pstate, req, InternalErrCode.UPSTREAM_REFUSED,
                                   "session unavailable", detail)
        case (ForwardRequest(req), Rejected(detail)):
            return _release_failed(pstate, req, InternalErrCode.UPSTREAM_REFUSED,
                                   "session refused configurationDone", detail)
        case (ForwardRequest(req), Crashed(detail)):
            return _release_failed(pstate, req, InternalErrCode.UPSTREAM_REFUSED,
                                   "configurationDone errand crashed", detail)
        case _:
            raise AssertionError(f"unhandled transition: {action!r} / {outcome!r}")
    #end match
#end def


# ---------- build_replies：回包段（seq 账本在此） ----------

def build_replies(frames: tuple[Frame, ...], seq: int):
    # 线上形状在此定型：Response→应答帧；Event→事件帧（同样吃 seq 账本，账本唯一归本函数）
    out = []
    for f in frames:
        match f:
            case OkReply():
                m = {"seq": seq, "type": "response", "request_seq": f.request.seq,
                     "command": f.request.raw_command, "success": True}
                if f.payload is not None:
                    m["body"] = f.payload
                #end if
            case ErrReply():
                e = f.error   # 失败必携 ProtoError=类型事实，运行时检查已死（契约 v4.1）
                m = {"seq": seq, "type": "response", "request_seq": f.request.seq,
                     "command": f.request.raw_command, "success": False,
                     "message": e.message,
                     "body": {"code": int(e.code), "detail": (e.detail or {}).get("detail")}}
            case Event():
                m = {"seq": seq, "type": "event", "event": f.event}
                if f.body is not None:
                    m["body"] = f.body
                #end if
            case _:
                assert_never(f)   # 穷尽性：新 variant 忘加 case=构造期大声失败（match_command 同哲学）
        #end match
        seq += 1
        out.append(m)
    #end for
    return out, seq
#end def
