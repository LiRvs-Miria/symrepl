#!/usr/bin/env python3
"""test_core — 纯核直测（不起服务端，喂 ADT；E3 盲区裁决后入常例）。

盖 adapter 核的可纯测行为：step 转移行、参数映射、resolve 落靶、状态门。
探针网走真线（probe_*.py），本网兜定义级/臂级错（漏 case、映射拼错在核内就地炸）。
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "server"))
from adapter import (Command, Done, ForwardRequest, ParsedRequest, ProtoState,
                     Rejected, StateEnum, StepResult, build_replies, resolve, step)
from error import InternalErrCode

fails = []

def check(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        fails.append(name)
    #end if
#end def

def req_of(command: str, arguments: dict | None = None) -> ParsedRequest:
    return ParsedRequest(seq=7, command=Command(command), raw_command=command,
                         arguments=arguments or {})
#end def


# step：(S3, stackTrace) 自回环携 ForwardRequest，参数映射在核
r = req_of("stackTrace", {"threadId": 7, "levels": 5})
res = step(ProtoState(phase=StateEnum.S3), r)
ok = (res.state.phase == StateEnum.S3 and res.frames == ()
      and isinstance(res.action, ForwardRequest)
      and res.action.cmd == "stack_trace"
      and res.action.params == {"thread_id": 7, "levels": 5})
check("step: S3 stackTrace 自回环携 ForwardRequest（参数映射 threadId→thread_id）", ok)

# step：缺参走缺省（threadId→0、levels→20）
r = req_of("stackTrace")
res = step(ProtoState(phase=StateEnum.S3), r)
ok = (isinstance(res.action, ForwardRequest)
      and res.action.params == {"thread_id": 0, "levels": 20})
check("step: 缺参缺省（thread_id=0、levels=20）", ok)

# step：S1 门（fallback 行）
r = req_of("stackTrace", {"threadId": 1})
res = step(ProtoState(phase=StateEnum.S1), r)
frames, _ = build_replies(res.frames, 1)
ok = (res.state.phase == StateEnum.S1 and res.action is None and len(frames) == 1
      and frames[0]["success"] is False
      and frames[0]["body"]["code"] == int(InternalErrCode.EVENT_NOT_ALLOWED)
      and "S1" in frames[0]["body"]["detail"])
check("step: S1 门 1003+detail 携相位名", ok)

# resolve 成臂：Done → 键映射单帧，状态驻 S3
r = req_of("stackTrace", {"threadId": 1})
action = ForwardRequest(req=r, cmd="stack_trace", params={"thread_id": 1, "levels": 20})
res = resolve(ProtoState(phase=StateEnum.S3), action,
              Done({"stack_frames": [{"id": 1000, "name": "main", "line": 4, "column": 5, "path": "/t.c"}],
                    "total_frames": 1, "junk": "x"}))
frames, _ = build_replies(res.frames, 1)
ok = (res.state.phase == StateEnum.S3 and len(frames) == 1
      and frames[0]["success"] is True
      and frames[0]["body"] == {"stackFrames": [{"id": 1000, "name": "main", "line": 4, "column": 5, "path": "/t.c"}],
                                "totalFrames": 1})
check("resolve: 成臂键映射（stack_frames→stackFrames，杂键滤除）", ok)

# resolve 败臂：Rejected → 单帧 2002（普通代理帧形，不烧双帧）
res = resolve(ProtoState(phase=StateEnum.S3), action, Rejected("no_thread"))
frames, _ = build_replies(res.frames, 1)
ok = (res.state.phase == StateEnum.S3 and len(frames) == 1
      and frames[0]["success"] is False
      and frames[0]["body"]["code"] == int(InternalErrCode.UPSTREAM_REFUSED))
check("resolve: 败臂单帧 2002（Rejected）", ok)

# step（表驱动）：scopes 出站映射
r = req_of("scopes", {"frameId": 5})
res = step(ProtoState(phase=StateEnum.S3), r)
ok = (res.state.phase == StateEnum.S3 and isinstance(res.action, ForwardRequest)
      and res.action.cmd == "scopes" and res.action.params == {"frame_id": 5})
check("step: scopes 出站映射（frameId→frame_id）", ok)

# resolve 成臂（表驱动）：scopes 键映射+expensive 父面补
r = req_of("scopes", {"frameId": 5})
action = ForwardRequest(req=r, cmd="scopes", params={"frame_id": 5})
res = resolve(ProtoState(phase=StateEnum.S3), action,
              Done({"scopes": [{"name": "Locals", "variables_reference": 7}], "junk": 1}))
frames, _ = build_replies(res.frames, 1)
ok = (len(frames) == 1 and frames[0]["success"] is True
      and frames[0]["body"] == {"scopes": [{"name": "Locals",
                                            "variablesReference": 7,
                                            "expensive": False}]})
check("resolve: scopes 成臂（键映射+expensive 父面补，杂键滤除）", ok)

# resolve 成臂（表驱动）：variables 四键映射
r = req_of("variables", {"variablesReference": 7})
action = ForwardRequest(req=r, cmd="variables", params={"variables_reference": 7})
res = resolve(ProtoState(phase=StateEnum.S3), action,
              Done({"variables": [{"name": "x", "value": "1",
                                   "type": "int", "variables_reference": 0}]}))
frames, _ = build_replies(res.frames, 1)
ok = (len(frames) == 1 and frames[0]["success"] is True
      and frames[0]["body"] == {"variables": [{"name": "x", "value": "1",
                                               "type": "int", "variablesReference": 0}]})
check("resolve: variables 成臂（variablesReference 键映射）", ok)

# step（表驱动）：continue 出站映射（空参，参考参数忽略）
r = req_of("continue")
res = step(ProtoState(phase=StateEnum.S3), r)
ok = (res.state.phase == StateEnum.S3 and isinstance(res.action, ForwardRequest)
      and res.action.cmd == "continue" and res.action.params == {})
check("step: continue 出站映射（空参）", ok)

# resolve 成臂（表驱动）：continue 单源映射
r = req_of("continue")
action = ForwardRequest(req=r, cmd="continue", params={})
res = resolve(ProtoState(phase=StateEnum.S3), action, Done({"all_threads_continued": True}))
frames, _ = build_replies(res.frames, 1)
ok = (len(frames) == 1 and frames[0]["success"] is True
      and frames[0]["body"] == {"allThreadsContinued": True})
check("resolve: continue 成臂（allThreadsContinued 单源映射）", ok)


if __name__ == "__main__":
    print("=" * 30)
    print("ALL PASS" if not fails else f"{len(fails)} FAIL: {fails}")
    sys.exit(1 if fails else 0)
#end if
