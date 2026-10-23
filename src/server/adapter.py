"""adapter — DAP 适配器的协议面（核心，纯函数版，契约 v4.1）。

业务角色=适配器：对客户端统一 DAP 语义（能力/生命周期/会话状态机）；
实现载体=纯函数状态机（parse_request → step → build_replies 三段管线）。
核内零 I/O、零原地突变、零裸字符串。

状态与转移全部来自 design-notes.md 定稿：事件=转移条件，状态=能待着的东西。
slot 即状态：PendingLaunch 非空 = S2。结果代数=Frame=Reply|Event（契约 v4/v4.1）：
语义由构造点声明，帧数不携带语义；非法状态不可表示（失败必携 ProtoError 是
构造期事实，非运行时检查）；七种结果对号表见 design-notes。
"""
from collections.abc import Callable
from enum import Enum, IntEnum
from typing import NamedTuple, assert_never
from urllib.parse import urlsplit
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
    THREADS = "threads"
    STACK_TRACE = "stackTrace"
    SCOPES = "scopes"
    VARIABLES = "variables"
    CONTINUE = "continue"
    SET_BREAKPOINTS = "setBreakpoints"
    TERMINATE = "terminate"
    NEXT = "next"
    STEP_IN = "stepIn"
    STEP_OUT = "stepOut"
#end class

def match_command(cmd: str) -> "Command | None":
    """字符串 → 命令枚举。这里是"已处理命令"的宣言清单：
    每个 case 是一次显式的处理承诺；enum 加了成员而这里没 case = 该命令未实现。"""
    match cmd:
        case "initialize":        return Command.INITIALIZE
        case "launch":            return Command.LAUNCH
        case "configurationDone": return Command.CONFIGURATION_DONE
        case "disconnect":        return Command.DISCONNECT
        case "threads":           return Command.THREADS
        case "stackTrace":        return Command.STACK_TRACE
        case "scopes":            return Command.SCOPES
        case "variables":         return Command.VARIABLES
        case "continue":          return Command.CONTINUE
        case "setBreakpoints":    return Command.SET_BREAKPOINTS
        case "terminate":         return Command.TERMINATE
        case "next":              return Command.NEXT
        case "stepIn":            return Command.STEP_IN
        case "stepOut":           return Command.STEP_OUT
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
    """收场拆除会话子进程：优雅窗→强杀；拆除尽力而为（落靶不问结局）。"""
    req: ParsedRequest
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
    v = args.get("lldbDir")
    if v is not None and (not isinstance(v, str) or not v):
        return "launch 'lldbDir' must be a non-empty path string"
    return None
#end def


# ---------- 调试器命令层（S3 路由：命令规格表+通用执行器，与协议生命周期分离） ----------

class DebugSpec(NamedTuple):
    """调试命令规格：同构形状（参数翻译→盲发→键重包）写一次，每命令一行。"""
    child_cmd: str                        # 子面核定名（壳盲发）
    params: Callable[[dict], dict]        # 出站映射：DAP arguments → 子面 params
    body: Callable[[dict], dict]          # 回程键映射：子面 result → DAP body
#end class


DEBUG_SPECS: dict[Command, DebugSpec] = {
    Command.THREADS: DebugSpec(
        child_cmd="threads",
        params=lambda args: {},
        body=lambda result: {"threads": result.get("threads", [])}),
    Command.STACK_TRACE: DebugSpec(
        child_cmd="stack_trace",
        params=lambda args: {"thread_id": args.get("threadId", 0),
                             "levels": args.get("levels", 20)},
        body=lambda result: {"stackFrames": [_dap_frame(f)
                                             for f in result.get("stack_frames", [])],
                             "totalFrames": result.get("total_frames", 0)}),
    Command.SCOPES: DebugSpec(
        child_cmd="scopes",
        params=lambda args: {"frame_id": args.get("frameId", 0)},
        body=lambda result: {"scopes": [{"name": x["name"],
                                         "variablesReference": x["variables_reference"],
                                         "expensive": False}
                                        for x in result.get("scopes", [])]}),
    Command.VARIABLES: DebugSpec(
        child_cmd="variables",
        params=lambda args: {"variables_reference": args.get("variablesReference", 0)},
        body=lambda result: {"variables": [{"name": v["name"],
                                            "value": v["value"],
                                            "type": v.get("type", ""),
                                            "variablesReference": v.get("variables_reference", 0)}
                                           for v in result.get("variables", [])]}),
    Command.CONTINUE: DebugSpec(
        child_cmd="continue",
        params=lambda args: {},                      # 参考参数忽略（单线程全续）
        body=lambda result: {"allThreadsContinued": result.get("all_threads_continued", True)}),
    Command.NEXT: DebugSpec(
        child_cmd="next",
        params=lambda args: {},                      # threadId 忽略（单线程契约）
        body=lambda result: {}),                     # DAP step 响应无 body
    Command.STEP_IN: DebugSpec(
        child_cmd="step_in",
        params=lambda args: {},
        body=lambda result: {}),
    Command.STEP_OUT: DebugSpec(
        child_cmd="step_out",
        params=lambda args: {},
        body=lambda result: {}),
    Command.SET_BREAKPOINTS: DebugSpec(
        child_cmd="set_breakpoints",
        params=lambda args: {"path": _fs_path((args.get("source") or {}).get("path")),
                             "lines": [bp.get("line")
                                       for bp in args.get("breakpoints", [])
                                       if isinstance(bp, dict)]},
        body=lambda result: {"breakpoints": [{"verified": bp.get("verified", False),
                                              "line": bp.get("line")}
                                             for bp in result.get("breakpoints", [])]}),
}


def _fs_path(p):
    """客户端 source.path 线形状归一：带 scheme 的资源 URI 取文件系统路径分量
    （vscode-remote://authority/home/... → /home/...，file:// 同款），纯路径原样过。"""
    if isinstance(p, str) and "://" in p:
        return urlsplit(p).path
    return p
#end def


def _dap_frame(f):
    """子帧 → DAP StackFrame：路径包 source 对象（VS Code 只认 frame.source.path，
    帧级裸 path 被无视）；Source.name=文件名；空路径（无行信息的帧如 _start）
    省略 source。"""
    frame = {"id": f["id"], "name": f["name"], "line": f["line"], "column": f["column"]}
    path = f.get("path") or ""
    if path:
        frame["source"] = {"name": path.rsplit("/", 1)[-1], "path": path}
    #end if
    return frame
#end def


def step_debug(pstate: ProtoState, req: ParsedRequest) -> StepResult:
    """调试器层命令路由：查表造 ForwardRequest；表外命令照走状态门。"""
    command = req.command
    assert command is not None   # 窄化给检查器：unknown 在 parse 阶段拦下，step 永远收不到 None
    spec = DEBUG_SPECS.get(command)
    if spec is None:
        # S3 来非调试命令（initialize/launch 等）：照走状态门
        return StepResult(state=pstate,
                          frames=(err_reply(req, InternalErrCode.EVENT_NOT_ALLOWED,
                                            "event not allowed in current state",
                                            f"{req.raw_command} not allowed in {pstate.phase.name}"),))
    #end if
    return StepResult(state=pstate,
                      action=ForwardRequest(req=req, cmd=spec.child_cmd,
                                            params=spec.params(req.arguments)))
#end def


def settle_debug(pstate: ProtoState, req: ParsedRequest, result: dict) -> StepResult:
    """调试命令成臂落靶：查表造 OkReply（键映射）。"""
    command = req.command
    assert command is not None   # 窄化给检查器：同 step_debug
    spec = DEBUG_SPECS[command]  # 路由保证 command ∈ 表；miss=核 bug，KeyError 大声失败
    return StepResult(state=pstate,
                      frames=(OkReply(request=req, payload=spec.body(result)),))
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

        case (StateEnum.S2 | StateEnum.S3, Command.DISCONNECT):
            # 收场：S2/S3 有肉身（不变量：履约 Done 才落 S2/S3），disconnect 先拆后别——
            # 携动作自回环驻原态（frames 静默），双帧归 resolve（拆除后发，对齐参考
            # "terminated 是拆除副产品"帧序）
            return StepResult(state=pstate, action=TearDownChild(req=req))

        case (StateEnum.S2 | StateEnum.S3, Command.TERMINATE):
            # 收场第二门：terminate 与 disconnect 同形（拆除臂：先拆 child 后别，
            # 双帧归 resolve=拆除后发）
            return StepResult(state=pstate, action=TearDownChild(req=req))

        case (_, Command.TERMINATE):
            # 断路器：S0/S1（及 DEAD）无肉身=纯转移直达终态（与 disconnect 同形）
            return StepResult(state=pstate._replace(phase=StateEnum.DEAD, slot=None),
                              frames=(OkReply(request=req, payload={}),
                                      Event(event="terminated", body={})))

        case (_, Command.DISCONNECT):
            # 断路器：S0/S1（及 DEAD）无肉身，无可拆=纯转移直达终态（偏离#5）；
            # 清 slot=丢弃整个状态；帧序=响应先、terminated 后（2026-10-15 裁决 a：
            # 两门形状统一，对齐参考实测）
            return StepResult(state=pstate._replace(phase=StateEnum.DEAD, slot=None),
                              frames=(OkReply(request=req, payload={}),
                                      Event(event="terminated", body={})))

        case (StateEnum.S2, Command.LAUNCH):
            # 双发：专属 detail（转移表 S2 行，R7 教训——泛化分支会吞掉它）
            return StepResult(state=pstate,
                              frames=(err_reply(req, InternalErrCode.EVENT_NOT_ALLOWED,
                                                "event not allowed in current state",
                                                "launch_already_pending"),))

        case (StateEnum.S2 | StateEnum.S3, _):
            # 调试器层整层路由（S2 配置期含）：查表自回环转发子进程，不触发
            # 适配器层状态迁移；表外命令照走状态门
            return step_debug(pstate, req)

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
    """完成在途转移：action×outcome 逐对落靶；未声明的组合在此大声失败。"""
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
        case (ForwardRequest(req), Done(result)):
            # 成臂按 DAP 命令分业务：放行=双帧（slot 扣单+现单）；普通代理=body 映射单帧
            match req.command:
                case Command.CONFIGURATION_DONE:
                    pending = pstate.slot
                    if pending is None:   # 不变量说 S2 蕴含非空；防御一把
                        return StepResult(state=pstate,
                                          frames=(err_reply(req, InternalErrCode.EVENT_NOT_ALLOWED,
                                                            "event not allowed in current state",
                                                            "no launch in flight"),))
                    #end if
                    return StepResult(state=pstate._replace(phase=StateEnum.S3, slot=None),
                                      frames=(OkReply(request=pending.request, payload={}),
                                              OkReply(request=req, payload={})))
                case _:
                    # 调试命令成臂整层路由：落靶在 settle_debug（与 step_debug 对称）
                    return settle_debug(pstate, req, result)
            #end match
        case (ForwardRequest(req), outcome):
            # 败臂按 DAP 命令分帧形：放行=双帧皆败（扣单+现单）；普通代理=单帧。码随物理事实。
            match outcome:
                case TimedOut(detail):
                    code, message = (InternalErrCode.UPSTREAM_TIMEOUT,
                                     f"session did not answer {req.raw_command} in time")
                case Gone(detail) | SpawnFailed(detail):
                    code, message = InternalErrCode.UPSTREAM_REFUSED, "session unavailable"
                case Rejected(detail):
                    code, message = (InternalErrCode.UPSTREAM_REFUSED,
                                     f"session refused {req.raw_command}")
                case Crashed(detail):
                    code, message = (InternalErrCode.UPSTREAM_REFUSED,
                                     f"{req.raw_command} errand crashed")
                case _:
                    raise AssertionError(f"unhandled outcome {outcome!r}")
            #end match
            if req.command is Command.CONFIGURATION_DONE:
                return _release_failed(pstate, req, code, message, detail)
            return StepResult(state=pstate, frames=(err_reply(req, code, message, detail),))
        case (TearDownChild(req), _):
            # 收场落靶：拆除尽力而为、会话照终（立场乙收场）——任何 outcome 同靶；
            # 码/话术不因拆除结局变。DEAD+slot 清+双帧（响应先、terminated 后，
            # 与纯转移断路器两门同形）
            return StepResult(state=pstate._replace(phase=StateEnum.DEAD, slot=None),
                              frames=(OkReply(request=req, payload={}),
                                      Event(event="terminated", body={})))
        case _:
            raise AssertionError(f"unhandled transition: {action!r} / {outcome!r}")
    #end match
#end def


# ---------- 子事件翻译（执行面词法 → DAP 词法；只翻不判：事件不改协议状态） ----------

def translate_child_event(event: str, params: dict | None) -> tuple[Event, ...]:
    """子事件 → DAP 事件（可能一变多：exited 携 terminated，DAP 惯例）；
    不认识的事件名=()（壳丢弃留痕）。线形状对齐参考 server.py:769-793。"""
    params = params or {}
    if event == "stopped":
        body: dict = {"reason": params.get("reason", "exception"),
                      "threadId": params.get("thread_id", 0),
                      "allThreadsStopped": bool(params.get("all_threads_stopped", True))}
        if params.get("description"):
            body["description"] = params["description"]
        #end if
        if params.get("breakpoint_ids"):
            body["hitBreakpointIds"] = params["breakpoint_ids"]
        #end if
        return (Event(event="stopped", body=body),)
    #end if
    if event == "exited":
        # 报告随收场事实（child 单源）：output 先发（过程输出先于终态告别），
        # terminated 随 exited（DAP 惯例一变二；携报告时一变三）
        report = params.get("trace_report") or {}
        out: list[Event] = []
        if report.get("text"):
            out.append(Event(event="output",
                             body={"output": report["text"] + "\n",
                                   "category": "console",
                                   "report": report.get("nodes") or []}))
        #end if
        out.append(Event(event="exited", body={"exitCode": params.get("exit_code", 0)}))
        out.append(Event(event="terminated", body={}))
        return tuple(out)
    #end if
    if event == "terminated":
        return (Event(event="terminated", body={}),)
    #end if
    return ()
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
