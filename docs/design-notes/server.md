# ② 会话状态机设计稿（2026-10-14，v6 转录稿）

> 状态/转移/守卫/动作全部来自用户与 AI 攻防对话的定稿决策，本文为转录。
> 复述判据待过：用户需能一口气讲出本链路，② 方计入经验。
> 已记录的"对参考实现的有意偏离"见文末清单——偏离带理由=决策，无理由=事故。

## 状态

| 名 | 本质 |
|---|---|
| S0 | 已连接，未 initialize |
| S1 | initialize 已答，等 launch |
| S2 | launch 已收、响应扣着（pending_launch slot 非空），等 configurationDone |
| S3 | 已放行（skeleton 阶段占位，出②范围） |
| 终态 | 既有收场路（client_done 单出口） |

slot 即状态：`pending_launch` 非空 = 处于 S2。与 `active_writer` 之于连接拓扑同一模式。

## 转移表

| 当前状态 | 事件 | 守卫 | 动作 | 新状态 |
|---|---|---|---|---|
| S0 | initialize | 无（adapterId 守卫已裁：实证不敏感+自家客户端即用 adapterId） | 回 capability 清单（实证 body：configurationDone/terminate 两项） | S1 |
| S0 | 其余 DAP 事件 | — | 状态不转移；回粗类错误码 + ProtoError(message=现象, detail=原因) | S0 |
| S1 | launch | 参数校验：解析、ktest 路径字符串（存在性检查=无实证决策，标注） | 过：**放 slot（launch+扣着的响应）**，不回包；败：回参数错误（细节在 detail） | S2 / 留 S1 |
| S1 | initialize | — | **拒**（决策：状态机纯洁>易用性；偏离 §4-2 实证，已记录） | 留 S1 |
| S1 | configurationDone | — | 拒（无实证，纯决策） | 留 S1 |
| S2 | launch | slot 非空 | 拒双发（detail: launch_already_pending） | 留 S2 |
| S2 | configurationDone | — | **取 slot** → 回两帧；帧序=**本设计 launch 先**（有意偏离：参考实测四种驱动流均不发此二响应，触发条件未定——开放问题 OQ-1，阶段 2 解封对答案） | S3 |
| S2 | initialize | — | 拒（同 S1 纯洁性决策） | 留 S2 |
| S0/S1/S2 | disconnect | 无（**断路器**，裁守卫：逃生门内侧不上锁；§4-3 实证 body={}） | 回成功响应（OK，非"错误码"）→ **清 slot** → 收场 | 终态 |
| S0/S1/S2 | 信号 | 无 | 掐活会话 → 既有收场路 | 终态 |

## 伪代码

```python
def on_launch(msg):                    # ≤5 行
    if state.slot is not None:         # 拒：slot 非空=双发
        return err(DETAIL_LAUNCH_ALREADY_PENDING)
    if e := validate(msg):             # 参数校验（ktest 路径等）
        return err(e)
    state.slot = (msg, deferred_ok_response(msg))   # 放：扣响应，不发
    # → S2

def on_configurationDone(msg):         # ≤5 行
    _, resp = state.slot               # 取
    state.slot = None                  # 清（本路径上的清）
    send(resp)                         # 放行扣着的 launch 响应
    send(ok_response(msg))             # 帧序=实测定（先验：本行在前）
    # → S3

def on_teardown():                     # 清理题答案
    # 清 slot：丢弃扣着的 launch 响应。
    # 理由=语义层"launch 交易作废"，非传输层"链接已死"
    # （disconnect 回包仍送达，§4-3 实证；作废的只是 launch 响应）。
    # 单出口结构下 State 消亡自动覆盖，无需显式逐路清理。
    state.slot = None
```

## 错误回包分层（A1 决策）

- 粗类在外：InternalErrCode（新增"状态不允许该事件"类，1xxx 段）
- 细节在内：ProtoError.message=现象，detail=守卫/原因
- 有意偏离：参考实现嘴碎直说原因（"requires a 'ktest' path string"）；
  本设计粗类+detail 分层。理由=统一分层结构（威胁模型：环回单客户端自己人）

## 已记录的对参考实现偏离（决策清单，挂账简称「六偏离」）

1. 状态门：本设计 S0/S1/S2 拒非法事件回状态错；参考状态盲（§4-1 launch 在 S0 进参数校验）
2. initialize 重入：拒；参考允许（§4-2 第二次 success 同 body）。理由=状态机纯洁
3. 错误详情：分层粗类；参考直说
4. adapterId 守卫：无。对齐实证（§3 不敏感）
5. disconnect：断路器无守卫。对齐实证（§4-3）
6. 扣响应释放点：本设计=configurationDone 取 slot 即发两帧；参考=四种驱动流均不发（触发条件未定，OQ-1）

## 开放问题（阶段 2 解封后对答案）

- **OQ-1 扣响应触发条件**：launch/configDone 响应在 initialize→launch→configurationDone→threads→continue/stackTrace→disconnect 全流中均未出现（2026-10-14 四跑一致）；demo 曾见"踩响"。候选：entry_stopped 相位后 / continue / 特定请求
- **OQ-2 子会话 phase 机**：错误文案实证存在 'dead' / 'stopped' / 'entry_stopped' 相位，continue/stackTrace 带相位守卫（锚点：session.py:24-27）
- **OQ-3 configDone=spawn**：process 事件紧随 configurationDone——slot 内容更像"待 spawn 的 launch 参数"而非"待发的响应"；与 v5-C slot 语义提示的偏差待源码定夺
- **OQ-4 entry-stopped 竞态**：有跑停住（stopped 事件），有直接跑死到 'dead'，机理未查

- **对案（2026-10-15，黑盒+源码双证，详见账本 2026-10-15 行）**：
  - **OQ-1 结案**——放行=configDone 本身（server.py:448-473 取 `_pending_launch` 立即回两帧，launch 先），不等任何 stop 相位。10-14 四跑不见响应=**客户端驱动丢弃 recv 残余缓冲**吃掉粘段帧（原版客户端复现）——**偏离#6 融化**：参考行为与本章 R8 行（configDone 放两帧、launch 先）一致
  - **OQ-3 修正**——slot 存请求 seq 非 launch 参数（:102,:400）；inferior spawn 在 launch 处理内完成（child_request 返回 pid），process 事件 launch 时已发出，"configDone 触发 spawn"是客户端晚读错觉
  - **OQ-4 破案**——非竞态=**stopOnEntry 参数**（:427-428，默认 False；demo 传了 true）
  - **OQ-2 增补**——相位守卫按命令分集合（stackTrace 收 `('stopped','entry_stopped')`，scopes/continue 只收 `('stopped',)`，threads 无守卫）；trace 报告走 trace_report→output 事件于 exit 后推（:803-810）；参考真命令名=`replay.branchTrace`（:121，decision 5 前缀）

## 黑盒锚点（2026-10-14 帧序实验副产品）

- launch 必需参数：`ktest` + `target` 两个路径字符串（错误文案逐步引导）
- branch trace 经 output 事件送达（category=stdout），86 site 54 hit，get_sign INT_MIN 路径（x=-2147483648）实测确认
- "no launch in flight"——slot 术语在参考错误文案中现形
- ktest 现货：`third_party/klee/ktest/get_sign_test000003.ktest`；target 构建配方 conftest.py:23-61，已重建于 /tmp/opencode/replay-build/get_sign（易失）

## 运维注记（结构决策的代价，2026-10-14 实测）

- **"会话结束即退"使 TCP 就绪探测变自杀**：单会话拓扑下，连上即断的端口探测=一次完整会话=进程退场。就绪检查必须用 `ss -tln` 查监听，不得试连。结构的刚性在此显形——参考实现同样如此（"单 accept、会话结束即退"）
- **localhost 双栈半绑定失败拖死全体**：绑 127.0.0.1 成功、::1 被占 → asyncio 关掉已绑的 v4 半边 → 整进程死。故单栈 127.0.0.1（§0 观察点）

## FC 重构刀法（进行中，用户亲刀）

**刀劈 State**：不劈 State 的函数式重构是纯仪式。三类成员三种命运：

| 成员 | 去向 |
|---|---|
| phase / slot / next_seq | **ProtocolState**（核的全部记忆，纯数据） |
| active_writer / client_done | 壳（世界对象，不可复制） |
| exit_code | 壳（壳的账本） |

**核的契约 v2（2026-10-14，pipeline 修订——用户设计，替代 v1 内联帧方案）**：

```python
ProtocolState: phase + slot(PendingLaunch|None，存 request 不存预造帧)   # next_seq 移出：线格式关注点
step(pstate, msg) -> ExpectState          # 专职状态转移，产出语义结果
build_replies(expect, seq_counter) -> frames   # 专职回包（含放行双帧的现场构造），自带计数器账本
# 壳把两段接成 pipeline：msg → step → build_replies → 上线
```

- seq 所有权归 build_replies：四 handler 均不读 seq 做决策，线格式关注点不入协议语义（用户判定）
- **结果 ADT（ExpectState 内的 outcome）待用户设计**——验收单：须能表达 7 种结果
  （caps 应答 / 状态错 / 参数错 / 双发错 / **launch=0 帧入 slot** / **configDone=放行双帧** / disconnect 应答）
  → **v4 已结案（2026-10-15，见文末「核的契约 v4」）**——转录曾跳过本设计环节，design review 逮回
- slot 不可变延伸：`PendingLaunch(NamedTuple)`，dict 直接入 slot 会别名漏水（浅拷贝反例已演示）
- ProtoError.code 默认值：**已裁决（2026-10-15 用户裁：去默认逼显式**，理由=静默成功是地雷，构造期报错优于 review 眼力）。考古注：代码存档自 745ed7d 起 code 即无默认（"暂用默认"仅存在于会话现场，从未落码），本行此前为陈旧尾注；裁决当日 12 探针复跑全过

**不变量**：

1. 核内零 I/O、零原地突变——变更走 `dataclasses.replace`（浅拷贝够用）
2. 壳职责不变：accept/拒连、读写帧、信号、退出码、client_done；壳对协议状态的唯一动作是 `step()` + **收场时丢弃 ProtocolState 对象**（"清 slot=交易作废"升维为丢弃整个状态）
3. 行为等价：`probe_transition_table.py` 12 探针必须原样全过（含 R8 帧序=launch 先，偏离清单#6 不动）
4. 壳判终态改用 **step 返回的新状态**（原 Mutation 版靠活对象，纯版没了）
5. 收场路径 slot 清理：壳丢弃状态即清，无需显式 None

**预埋攻击点**（转录时最容易翻车的四处，自查）：

- `response()` 现在原地 `next_seq += 1`——纯版必须把 seq 线程化（进状态、出新状态）
- `on_launch` 现在返回 None 表示"无输出但状态变了"——纯版元组里空列表显式表达，None 惯例消失
- `on_configurationDone` 返回两帧——元组第二元素是列表，别拆散
- 壳的 `if state.phase == Phase.DEAD: break`——改用 step 返回值判断，且断路器仍然"回完应答再退"

## 核的契约 v4（2026-10-15，帧类型代数——用户裁定，阶段 1 后 design review 产出）

**review 发现**：上节验收单的"结果 ADT 待用户设计"环节被转录跳过——ExpectState 交了
`tuple[Reply, ...]`，语义靠帧数编码（0=扣/1=应答/2=放行），七种结果塌缩成三种帧数。
探针全过：**回归网测行为等价，测不出类型表达力缺位**。

**用户裁定**：帧类型代数——**语义由构造点声明，消费端禁止按帧数/位置反推**。

```python
class Event(NamedTuple):
    event: str               # DAP 事件名（stopped/output/exited…）
    body: dict | None = None

Frame = Reply | Event        # Reply=Response 语义载体，不改名
# StepResult.state + frames: tuple[Frame, ...]
```

**命名**：ExpectState → **StepResult**（2026-10-15 用户裁，名实对齐——装的是转移结果不是状态；纯机械改名零行为，12 探针复跑全过。本文 v2 节及 review 记录中的旧名保留，属历史记录）。

**追加裁决 v4.1（同日）：Reply 劈分 OkReply/ErrReply**——失败必携 ProtoError 从 build_replies
运行时 `raise ValueError` 升级为**构造期不可能**（非法状态不可表示；与 ProtoError.code 去默认同哲学）。
`Reply = OkReply | ErrReply`（success 布尔退役）；build_replies match 穷尽扩为三 variant + assert_never；
线格式零变化，12 探针全过。

**同日否决案（记录带理由）**：frames 类型上界 `tuple[()] | tuple[Frame] | tuple[Frame, Frame]`——
①界是当前转移表的观察非协议事实，阶段 2/3 事件一来必破；②union 只拦"≥3 帧"，而 step 是封闭
构造点、该错误类不存在，真手滑（放行忘发第二帧）因 Single 全局合法拦不住——归 R8 行为网守；
③语义命名变体（Silent/Released）=帧数编码回潮。裁决：不封顶，劈 Reply。

**三项子裁决**（用户定稿）：①Event 现在就声明+定序列化（今日无生产者，dead until 阶段 2/3，探针覆盖不到如实入档）；②不加 outcome 标签（typed frames+slot 已承载全部语义，双写=冗余）；③命名 `Frame = Reply | Event`（Reply 不改名）。

**序列化规则**：Response→线上形状不变；Event→`{"seq", "type": "event", "event", "body"?}`，事件同样吃 seq 账本（账本唯一归 build_replies，不变）。

**验收单 7 种结果对号 v4**：扣的静默改由 slot 承载（"slot 即 S2"的推论），帧数退役——
caps=`(Response(ok,CAPS),)`；状态错/参数错/双发错=`(Response(err 码+detail),)` 靠码区分不靠帧数；扣=`()` 且 slot 入账；放行=构造点排好的双帧（launch 先，偏离#6/R8 不动）；disconnect=`(Response(ok),)`。

**预埋攻击点（转录自查）**：build_replies 的 match 必须**穷尽** Frame 两 variant——`assert_never`，将来加 variant 忘加 case=构造期大声失败（match_command 同哲学）。落地记录：12 探针全过；LSP 另逮到壳发送循环误迭代语义层（本轮改名引出，当场修）。

## shutdown 设计档案（2026-10-15 用户定稿；**阶段 3 改道：只做 clean-room，真代码库 shutdown 押后**）

**语义决策（用户五答+三钉，全部用户产出，本文为转录）**：

1. 回包：success、**无 body**（seq/request_seq/原始命令标准形）
2. 收场：响应发出后**补一发 terminated 事件**，随即会话结束（server 自退场，不等客户端告别）
3. 退出码：**0=FIN**（两层退出码代数的 FIN 粗类，非 RST）
4. 状态策略：**断路器统一放行**——任意状态（S0/S1/S2/S3）无守卫，同 disconnect 哲学（逃生门内侧不上锁）
5. S3 内部细分（正常运行/命中或没命中断点）后置设计，不在本 cycle（design TODO 挂账）
6. capability 红线（半对，追问挂复述）：`supportsTerminateRequest` 为何要广告而 shutdown 不用？方向=capability 只广告**可选**能力

**转移表增补行**：

| 当前状态 | 事件 | 守卫 | 动作 | 新状态 |
|---|---|---|---|---|
| 全部（S0-S3） | shutdown | 无（断路器） | 回 ok 响应（无 body）→ 发 terminated 事件（**帧序=响应先**，序不变量"响应先于其引发的事件"） | 终态（DEAD） |

**转录落点（实现=用户；壳零改动——DEAD 判终态 schedule.py:86-87、收场丢状态+exit0 :97-105/:139 全是既有路）**：

1. `Command.SHUTDOWN = "shutdown"`（enum 增员）
2. match_command 宣言清单 += `case "shutdown"`（显式处理承诺）
3. step += `case (_, Command.SHUTDOWN)` → `ExpectState(pstate._replace(phase=DEAD, slot=None), (ok_reply(req), Event("terminated", {})))`

**S2 特例断言**：slot 非空时 shutdown——扣着的 launch 响应**作废丢弃**（不变量 5：收场=丢弃整个状态），客户端只应收到 [shutdown resp, terminated]，绝无 launch 帧泄漏。

**Frame 代数兑现**：terminated 是 Event variant 的**第一个生产者**——response+event 同转移混排一处输出，v3 的 `tuple[Reply, ...]` 无法表达，v4 留位当日兑现。

**验收网（三件套）**：
- 契约先行=本节；回归网=12 探针原样全过（R4 的 `shudown` 拼写探针必须保持 unknown）+ `probe_shutdown.py`（AI 转录：S0/S1/S2/S3 四状态矩阵+帧序+slot 不泄漏+自退场 exit0）；复述照走（挂账见下）
- 客户端需求文本：dap_client.py 新增 shutdown 断言函数（AI 转录）

**复述挂账（shutdown 链）**：断路器哲学为何覆盖 shutdown / 帧序为什么响应先 / S2 slot 作废的依据 / capability 追问

**落地记录（2026-10-15 当日）**：用户实现三小件+首验两偏（terminated 缺席、`ok_reply(req,{})` 使 body:{} 上线）→ 同日裁决 **ok_reply 助手退役**（直构 `OkReply(request=req)`，payload 缺省在调用点可见化；err_reply 留任=A1 约定集中点，`{"detail": detail}` 包法不散抄）→ 机械清扫 5 调用点 → **probe_shutdown 8 项+转移表 12 探针全绿**。Event variant 首个生产者上线，v4 留位兑现。

**构造约定（2026-10-15 用户裁定）**：ADT 构造一律**具名成员**——`StepResult(state=…, frames=…)`、`Event(event=…, body=…)`、`OkReply(request=…)`，位置式禁用（可读性：`frames=(OkReply(request=req), Event(event="terminated"))` 自释帧序与种类）。

**收场事件对齐（2026-10-15 裁决 a）**：disconnect 与 shutdown **两门形状统一**——resp 之后都发 terminated 再收场。缘起：clean-room 的 disconnect 原本光杆（R9 只验 resp，从不验事件），而参考的 disconnect 后 terminated 是 child 杀 inferior 的副产品（server.py:790 翻译），clean-room 无 child 故无此帧——shutdown 人工补了、disconnect 没有，两门分叉无原则。裁决=都补：①对齐参考实测（capture 两回记录 resp→terminated）；②客户端收场序列与走哪扇门无关。R9/R10 探针已补 terminated 断言（网洞：事件有无从未被断言过——网只查你想到要查的，第三次显灵）。

## shutdown 退役（2026-10-16，用户裁决）

**裁决**：shutdown 命令整体退役——非标准 DAP 命令（LSP 习语；用户原话："我又不是做lsp"）。考古实锤：ADR-0003 decision 4 诚实子集本就无 shutdown（参考收到即回 not implemented，黑盒 §4-5 撞的正是它）；标准 DAP 会话终结由 disconnect（会话面）+terminate（debuggee 面）覆盖，adapter 进程死是隐式的。

**capability 再验挂账就此撞结**：复述结算中"shutdown 为规范必答核心请求"有误——规范无此命令，"必答"只对 disconnect 成立。代念收尾项撞实证修正，验灵兑现。

**教学资产无损移交**：Event 首个生产者/响应先于事件帧序/断路器统一/S2 slot 作废——全由 disconnect 门承载（裁决 a 已两门同形，shutdown 的 terminated 生产者身份可由 disconnect 顶替）。

**落地**：statemachine 三处（enum 成员/match_command 行/step 断路器行）；dap_client 七个 shutdown 函数+两个 §4 顺序实验脚本；probe_shutdown.py 整档删除（回归网 30→转移表 14+framing）。退役红利：`shutdown`/`shudown` 两种拼写同流 unknown→not implemented，行为对齐参考。退役后回归：转移表 14+framing 全绿。若将来确需协议侧退场门，重新立项走完整周期。

## framing 重构设计档案（2026-10-15 用户定稿：值 ADT/外包缓冲/错误分家）

**总纲（用户原话）**：FP=类型驱动的严格性+错误前置，不是为函数式而函数式。重构脊柱=堵三洞。

**三洞→分类映射**：

| 洞 | 线上场景 | 现状 | 归类 |
|---|---|---|---|
| 洞1 巨型长度 | `Content-Length: 2000000000` → readexactly 干等/巨分配 | 无闸 | **LENGTH_OVERSIZE**（声明即拒，64MiB 闸对齐参考） |
| 洞2 宽松解析 | `+12`/`1_2`/带空格 被 int() 吞成合法 | 等到 body 阶段才炸 | **LENGTH_FORMAT**（纯数字闸，头解析期拒） |
| 洞3 超限头裸穿 | >64KiB 无分隔符头 → LimitOverrunError 炸穿壳的 except | 裸异常逃出 FrameError 契约 | **HEADER_LIMIT**（收编） |

**三项裁决**：①错误形态=**值 ADT**（ErrReply 提案被否——坏帧构不成 request_seq/command，且 no-resync 语义下 pipeline 不继续：坏帧后流已烂，下帧边界未知）；②缓冲=**继续外包 asyncio**（StreamReader 即状态机，不自建）；③错误**现在分家**（任务 B 语义定型，不留糊一层）。

**输出代数**：

```python
FrameBadReason(Enum): LENGTH_OVERSIZE / LENGTH_FORMAT / HEADER_LIMIT / STREAM_TRUNCATED / BAD_BODY
FrameOut = FrameOk(payload: dict) | FrameEof | FrameBad(reason, detail)
```

**壳接线**：FrameEof → FIN（exit 0）；FrameBad → ABRUPT（exit 2；no-resync=参考 decision 2 对齐：坏帧后无恢复路径）；FrameOK → 进 parse_request。半帧/未读完就关=STREAM_TRUNCATED；body 非 UTF-8/JSON=BAD_BODY。

**已知注记**：无冒号头行继续忽略（参考会拒——对齐留任务 B）；MAX_FRAME_BYTES=64MiB 对齐参考；IncompleteReadError 是 EOFError 子类，except 顺序注意；stderr 日志须含 reason 名（探针 grep 依此判分类）。

**验收**：probe_framing.py（RED 起步）：R-F1 超长声明（客户端不发 body、socket 保持打开——拒绝不得等字节来齐）→LENGTH_OVERSIZE+ABRUPT；R-F2 `+12`→LENGTH_FORMAT+ABRUPT；R-F3 10 万字节无分隔符头→HEADER_LIMIT+ABRUPT（不 crash 不漏连接）；R-F4 半帧→STREAM_TRUNCATED+ABRUPT；R-F5 干净 FIN→exit0；回归门=转移表 14+shutdown 8 原样全过。**实现=用户**（framing.py 重写 read_frame 返回 ADT+enum+闸；schedule.py 调用点接线），转录（本契约+探针）=AI。

**落地记录（2026-10-15 当日）**：AI 转录实现（用户骨架+契约），网炸出**两个转录 bug**：①Enum 成员值全 `=...`（typing 占位习惯）→同值即别名，五成员全是 LENGTH_OVERSIZE 的别名，reason.name 永远打第一个——auto() 才是无值声明；②isdigit 闸没 strip 冒号后空格→**所有正常帧被拒**（server 收合法帧当 FrameBad 关连接，转移表/shutdown 探针 EOF 三连）——替换旧宽容（int() 吞空白）前先弄清它宽容了什么。修复后 30 探针全绿（framing 8+转移表 14+shutdown 8）。两 bug 由两张不同的网各自抓获——单网单点，多网交叉才见全貌。探针迁 `local-cleanroom/probes/`（工装与源码分居）。**复述结算（2026-10-15，部分自达+纠错代念，挂账清）**：层次切分✓（ErrReply 生于 request 解析后，坏帧走不到）；chaos 映射 2/3✓（半帧、未读完就关→STREAM_TRUNCATED）；没踩过坑如实说✓（不死者不诈）。纠错三处入账：no-resync——回完也没有"之后"；坏 CL=FORMAT ≠ 太长=OVERSIZE（两个洞别合并）；外包代价=依赖方实现细节漏进错误契约（洞3 即实证）+混沌测试只能走真 socket，收益=少写一个状态机（鲁棒性是转包不是变好）

**复述结算（2026-10-15，部分自达+纠错代念，挂账清）**：层次切分✓（ErrReply 生于 request 解析后，坏帧走不到）；chaos 映射 2/3✓（半帧、未读完就关→STREAM_TRUNCATED）；没踩过坑如实说✓（不死者不诈）。纠错三处入账：no-resync——回完也没有"之后"；坏 CL=FORMAT ≠ 太长=OVERSIZE（两个洞别合并）；外包代价=依赖方实现细节漏进错误契约（洞3 即实证）+混沌测试只能走真 socket，收益=少写一个状态机（鲁棒性是转包不是变好）

## 砖1 契约：child 缝（childproto.py + schedule 缝函数）（2026-10-16，AI 转录稿待用户过目）

**先验与收敛裁决（用户）**：ping=启动握手专用；心跳+置信区间**撤除**——裁决口径=**复杂度/收益权衡妥协非证伪**（stdio 管道故障面不可穷举；个人 PC 可用性>可靠性，"能用即使稍微不易用不可靠可接受"），EOF+超时双网实证漏检时此门可复议。**退场门统一：EOF（死）∪ 请求超时（挂死）→ 同一扇门**；不问原因按 observable 判死（对齐 2026-10-14 账本教训）。child 日志=child 自己的 `--log` 参数（参考同款 service.py:392-398）。

**childproto.py**（形状转录自参考 service.py:28-34；用户裁决⑥=独立文件自定义形状）：
- `VERSION = 1`（版本校验行为未裁——挂钉，撞到即裁）
- ADT（NamedTuple，具名构造同 adapter 哲学）：`ChildRequest(id, cmd, params)` / `ChildResponse(id, ok, result, error)` / `ChildEvent(event, params)`
- `request_msg(req) -> dict`；`parse_child_msg(msg: dict) -> ChildResponse | ChildEvent | BadChildMsg`（值 ADT，错误前置；type 分流 response/event/未知）
- 线格式复用 framing（同 Content-Length；读法同 asyncio StreamReader——"缓冲外包 asyncio"决策直接继承）

**schedule.py 缝函数（签名）**：
- `spawn_child(argv: list[str]) -> Child`（asyncio create_subprocess_exec，stdin/stdout PIPE；interp 不存在=立即抛）
- `child_ping(child, timeout) -> dict`（=child_request("ping") 薄别名）
- `child_request(child, cmd, params, timeout) -> dict`（correlation=id→Future 表；**decision 8：child 已死立即失败不烧 timeout**）
- `child_close(child) -> None`（关 stdin 走 child 自杀路 exit 0；优雅窗 2s 过后强杀；**幂等**）
- 异常两类：`ChildGoneError`（EOF 路）与内建 `TimeoutError`（超时路）——两路喂同一退场门但异常可辨
- 单写者：stdin 写唯一入口（参考 decision 7 同款）

**不变量**：①全部住壳/缝，零入 adapter（协议面）②Future 表在超时/EOF 路必须 pop（无泄漏）③close 幂等（已死再关不炸）④EOF 与超时竞争同一在途请求=先到先得，两路都能败

**预埋攻击点**：stdin 并发写交错 / Future 泄漏 / close 重入 / interp 不存在的 spawn

**探针**：`probe_childseam.py` T0–T6（代数往返/握手+干净退场/spawn 快败/挂死超时/EOF 快败/泛化请求/close 幂等），RED 起步；SYMREPL_PY 环境变量覆盖 child 解释器（缺省 `~/venvs/symrepl/bin/python`）

**挂钉（撞到即裁）**：版本校验行为 / 请求级失败回给客户端的线形状（归砖2 接状态机时）

**通道选型 review（2026-10-16，对等攻防后维持管道）**：管道/UDS/TCP/shm/RPC/进程内六路过堂。EOF 可靠性管道≈UDS>TCP（自纠：此前把 EOF 列为管道对 socket 的优势不精确，赢在零管理非 EOF 本身）；shm 无阻塞语义无 EOF（stdin-EOF 自杀作废）；TCP 半开死角复活心跳机；rpc 引第三方违反纯 Python 红线。结构性理由：fork-and-hold 亲缘关系的原生通道=继承 fd；收场哲学（退场门/stdin-EOF 自杀/decision 8）全踩在"死亡=EOF"上。解耦四维称重：代码/知识耦合管道是解耦器本身（session.py 纯 stdlib 零父依赖为证），契约耦合=不可再减最小值（childproto 收拢），部署耦合是唯一真输家且=单会话架构的故意。外部校准：LSP/DAP 标准传输即 stdio。保险丝：通道被缝关着，跨机/独立 child 之需出现时只动 spawn/读写/session.py io 三处。

**M1↔M2 同构观察（2026-10-16 用户发现，填真逻辑阶段的施工图）**：父-子架构本身就是 M1 一次性回放架构的交互态泛化——①spawn 隔离契约逐字保留（ADR "verbatim"）②LLDB 只在子 ③ktest 经父传入（argv→env 两跳；decision 11 原始路径不重序列化）④回放引擎（预扫分支/resume 循环/trace 采集/走向推断）=待填主体。
**同段翻案（2026-10-16 用户裁）**："复用铺路/非从零写"结论**作废**——M2 cleanroom 对原 replay 引擎**零代码继承**：LLDB 件直接对真实 lldb API 原生实现；回放核心（ktest 解析/预扫描断点/走向推断/trace 采集报告）在 cleanroom **真正重写**，多范式（更多 FP）+错误前置比旧代码更严格。M1 引擎角色=**行为 oracle**（同 ktest/target 上对账输出），非代码来源。知识传承走规格不走代码：ktest 格式知识（phase1 账本逐字节）+行为规则语义（decision 7 owner-thread/11 原路径/12 世代票据）+坑清单（read1/复用 no-op/事件排空）。M1 CLI 产品路径不动（旧引擎继续服务一次性回放），cleanroom 平行第二实现，收敛/删旧押后不预设。

**砖2 先验攻防定稿（2026-10-16，用户三剧本+两钉裁）**：①**两事件对齐参考**——launch 履约成功即广播 `process`（body 带 pid）+`initialized`（可配置了），位于专屬状态→S2 转移的 frames；configDone 放行=[launch resp(body={}), configDone resp]（caps 只在 initialize 应答，R8 不变）。理由：initialized 是 DAP 标准握手（标准客户端等它），且无事件则砖2 客户端席位零可观测增量。②**立场乙裁**：child 死/无应答→**父活陪到底**（后续+在途请求快败，对齐参考 decision 8）——**六答①"验证不通过进终态"部分翻案**（失败=ErrReply+留 S1 可重递；slot 销毁语义保留=扣的东西丢弃）；退场门收缩为客户端侧三路（FIN/RST/坏帧）；error.py InternalErrCode"会话不死"由冲突转对齐（上游 2xxx 装"会话员没应答"）。③**launch 履约两步结构**：先行诊断（父侧路径/ktest 验证，败=ErrReply 直接回，不惊动会话员）→ spawn+握手（ping 不通/起不来=上游类失败，ErrReply，父活）。④桩 pid 裁（代码先行）：桩回**自身真 pid**，探针钉形状不钉值。
**砖2 转录落地（同日，RED 基线）**：桩 session.py 增 launch 接单确认（result.pid=自身真 pid）；`probe_launch.py` L1 好单全链/L2 废单不惊动——**2 FAIL 精确落在 process 事件超时未达**（本砖唯一增量），其余全过=砖1 行为完全兼容；childseam 8 绿（桩加 launch 不伤探活）、转移表 ALL PASS。**冻结点**：接线实现待开工（承包=AI）；专属状态名待用户铸（暂名 LAUNCHING 可用，改名=机械件）。

**子进程依赖裁决（2026-10-16，A 案）**：维持 `-m symrepl.dap.service` 原包依赖至 5c。理由：拷贝快照断不了奶（framing 旧类 API/ session 两行 import 仍指原包；改用 cleanroom framing 需重写阻塞读线程=5c 的活提前）+漂移影子文件；真断奶时刻=5c（新 service 用 cleanroom framing+重写 session），届时拷入就地重写。**修订（2026-10-22，B 案裁决，终局）**：A 案的「5c 拷入就地重写」不再发生——归一改道 B 案，cleanroom **原地转正**为 DAP 产品面，src/symrepl/dap/ 整册退役，`symrepl dap`=cli 子进程 spawn 本册 schedule.py；本节 A 案表述作废存史（299/300 行的「5c 时」字样随之失效）。
**同日翻案（用户裁）→ 桩案**：不依赖原包，cleanroom 自持**桩 service.py**（形状=真线协议：Content-Length 帧/pong 形状 `{pong:true, service_version:1}`/id 回显/stdin-EOF exit0；真逻辑后续填，文件连续演进）。session.py 桩暂不立（YAGNI，撞到 launch 桩再立）。探针 child 解释器翻转为 `sys.executable`+本地桩，SKIP 分支退役，砖 1 网全封闭。风险闸：桩对真分歧由探针钉死真线形状防住（形状源=真 service.py:28-34/:94）；"死讯传得回"语义不变——桩必须忠实实现 stdin-EOF exit0。

**砖1 落地（2026-10-16，AI 转录，契约+网先行）**：childproto（T0）→ spawn_child（T2）→ child_request 机制全块（_Channel 注册表+reader task 帧分流+Future 表+单写者锁+close 优雅窗/强杀/幂等）。**网炸两处转录错**：①`poll()` 是 Popen API，asyncio Process 无此物——`returncode is not None` 才是 asyncio 版 liveness（T1-T5 齐 RED 当场逮）；②T4 探针设计洞——会应答的桩答得太快，kill 前请求已完结，"在途"是假的；改哑 child（不答话）保真在途。**契约增补**：应用级 `ChildRequestError`（child 应答 ok=False，参考 _ChildRequestError 同形 server.py:153-155）——"异常两类"之外的第三类，属应答面非退场门。**T7 增补**：decision 8 前半（发前已死→快败）原探针没钉，补齐。终态：**8 探针全绿+转移表 14+framing 无回归**。

**命名（2026-10-16 用户铸名）**：statemachine.py → **adapter.py**——业务角色名（DAP 适配器的协议面：能力/生命周期/会话状态机）替代实现载体名（状态机）。childproto 定位=**执行层协议的词汇表+编解码**（边界层）：它解析内部协议的帧形状，不解析命令语义——命令语义与 LLDB 操作在管道对端子进程（`_dispatch`→session）；管道不是调用栈。5c 重写时 childproto 可升级为父子共享契约层。
**桩子进程文件铸名（2026-10-16 用户裁）**：service.py → **session.py**——"进程即会话"（ADR "one child per session"，进程生死=会话生死是它的结构本质）；与 src 语义层 session.py 短期同名靠目录分辨，5c 归一。

**砖2 接线落地（2026-10-19，AI 转录三案定稿一波三折）**：先验四裁未动，动的是建模。**病灶**：旧转移代数纯反应式 `(状态,事件)→(状态′,帧)`，缺转移五件中的 action——履约这类不纯动作无处安放，中途态不可表达。**案1 废（LAUNCHING 专属态）**：await 期间读循环阻塞，任何请求都遇不到该态——"状态=能待着的东西"被自己违反，伪状态。**案2 废（fulfill 旗）**：StepResult 塞 bool 第三通道=把表达不出的东西缝进去；且旗无载荷无语义。**案3 定（action+choice，用户裁根解）**：转移带动作起飞、choice 落靶——step 吐 `(S1 自回环, 静默, action=FulfillLaunch(req))`（"slot 非空=S2"不变量原样保留，请求由壳在手成时补挂）；壳 `run_effect` 认 Action 盲执行；核 `resolve(action, outcome)` 逐对落靶（两事件帧序/pid 形状闸/上游码/话术全归核——协议语义不过壳，壳连 result 内容都不看）。**词汇表**：Action 三员（FulfillLaunch 在役；ForwardRequest=砖3 S3 薄代理、TearDownChild=砖4 拆除，占位入册防忘位——用户裁；占位员进执行器/落靶即大声失败）；Outcome 六员=差事物理事实（Done/TimedOut/Gone/SpawnFailed/Rejected/Crashed）。**网炸一处（用户逮）**：except 只列四种异常=分类学第二拷贝+四种外异常弃尸逃生——改 `except Exception` 全捕获无条件收尸（child_close 幂等），`_physical` 单点分类学+末臂默认收编；收编需诚实新事实故 Outcome 增 Crashed（责任在父侧执行），resolve 给 2002+如实话术，会话仍不死（立场乙）。**协议砖红利**：resolve/占位动作可纯核直测（喂 ADT 不起服务端），本次七路直测先行、漏导入当场逮住。**网况**：probe_launch L1/L2 RED→全绿；转移表 R6 网眼依法翻案（静默→两事件断言，先验①的可观测兑现）；四网全绿。**机械件**：探针 odot 硬编码 sys.path→相对路径（跨机可跑）、KT 相对化（TG 仍不透明载荷）；本机装 psmisc（fuser 回归原样自洁）。**码注纪律（用户裁）**：注释只说代码本身；裁决史/形式化出处/作废史一律归本档案。**预留**：砖3 child 事件=真异步源，届时才修 await 顶点+内部事件门（今日修门是给假事件修的）；E 线动作（ResumeRun 等）同槽扩展。

**砖3 落地（2026-10-19，实施驱动：AI 小步转录+用户逐步 review）**：范围=放行真触发+stopped 事件回流（D3）。**放行**：`(S2,configDone)` 改携 `ForwardRequest(req,cmd,params)` 自回环驻 S2（子命令名核定在核，壳盲发；对齐参考 server.py:461 先递 `config_done` 后放帧）；resolve 增放行落靶——成→S3+双帧（launch 用 slot 扣单）、败→**双帧皆败**（参考形状）但**留 S2+slot 可重递**（立场乙同类；参考烧 slot 不取，用户 review 放行）。**回流**：核 `translate_child_event` 只翻不判（stopped 全字段对齐参考 :769-781；事件不改协议状态——"哪个状态允许事件"留到首个改变状态的砖再裁，用户裁）；壳 `_Channel.sink` 回流口（肉身落册时注入）+`forward_event`（翻译→领 seq→写客户端；领号不跨 await=原子；不认识事件丢弃留痕）。**钉序裁决**：stopped 与放行双帧不钉相对序——reader/handle 双路写有真竞争，网改宽容三帧收齐（L1/R8/L2 同款），双帧间字节序（launch 先于 configDone）仍钉死。**收尸边界细分**：ForwardRequest 失败**不收尸**——肉身非本差事产物（砖4 管拆除），对端死自有 reader EOF 报丧；与 FulfillLaunch 的"全捕获无条件收尸"并存，按"差事产物"划界。**网炸**：ForwardRequest 漏导入（NameError 运行时炸，py_compile 不设防）——与砖2 Crashed 同第二块石头；两张网（纯核直测/探针）都没兜住壳的导入错，壳侧直测网挂账（撞结再立）。**网况**：probe_launch RED→全绿（stopped 形状+到达）；R8 网眼依法翻案（宽容三帧）；四网全绿。**桩**：config_done 接单→应答→吐 stopped（params 用执行面词法 thread_id/all_threads_stopped，DAP 词法翻译归父侧）。

**横向首发 threads 落地（2026-10-19，方向裁决：砖4 收场路押后，掉头横向调试命令——用户裁，每层都有命令利于架构/方法论抽象）**：命令成员入册（match_command 宣言清单+Command.THREADS）；`(S3,THREADS)` 携 `ForwardRequest(cmd="threads")` 自回环驻 S3（子面同名空参）；其余状态走 fallback 状态门（1003+相位名，参考靠子进程报错兜门，协议面直接挡）。**落靶重构=按 DAP 命令分业务**：成臂放行=双帧（slot 扣单+现单）、普通代理=单帧 body 映射（threads 取 threads 键重包、杂键滤除，对齐参考 server.py:487）；败臂放行=双帧皆败、普通代理=单帧，码随物理事实、话术随命令。**网炸（横向首块石头）**：步3 败臂混用——ForwardRequest 四路失败全路由 `_release_failed`（放行专用双帧皆败），threads 失败也回双帧且话术张冠李戴（"session refused configurationDone"）；probe exit FAIL 是下游伤（残留败帧未读即 close→内核 RST→服务端 ABRUPT=2）。**教训**：一个动作类承载多业务时，落靶帧形必须按 DAP 命令分，臂不能按动作类一锅端；第二个业务进场的当天就会审判第一个业务写下的形状。**横向复利兑现**：threads 全程壳零改动（ForwardRequest 臂砖3 已铺），新命令成本=核（命令成员+状态行+落靶映射）+桩+网。**网况**：probe_threads RED(2)→全绿（门 1003/透传形状/收场链）；五网全绿。**桩**：threads 假单线程 `[{"id":1,"name":"thread-1"}]`（单线程契约，真 inferior 归引擎线）。

**E3 落地（2026-10-19，实施驱动）**：原语件 `local-cleanroom/server/lldb_session.py`——`load_lldb`（plain import→`lldb -P` 定位）；**单进程单 debugger 升格为构造期契约**（第二个 Session=debugger_exists 大声拒绝；LLDB 22.1.8 宿主复用 Create 静默 no-op+失败后 Continue 永挂，不给复用机会）；`Session.launch`（KTEST_FILE 单变量最小环境+入口停+异步——环境不设变量时 kleeRuntest 运行时卡 stdin，故恒设）；`wait_state` 事件排空（非本 process/非进程/Running 过场全丢，撞 stopped/exited 才信）；`continue_/exit_code/close`。**新坑三枚（转录现形，入规格）**：①`SBFileSpec.GetPath()` 在 lldb 22 要缓冲区参数——argv 直接用 target 路径串（M1 基准同形）②本地 launch 硬依赖 **lldb-server** 且打包目录不进 PATH——`ensure_lldb_server()`：显式 env>lldb 同目录>/usr/lib/llvm-*/bin，设 LLDB_DEBUGSERVER_PATH（对齐 M1 driver:135-157 环境契约）③`WaitForEvent` SWIG 绑定收 uint32，float 直接 TypeError——入口取整。**oracle 网况**：probe_e3 三 ktest diff=0（直跑 0/1/255 vs E3 全程逐一吻合）；拓扑=一 Session 一进程（子进程模式，守门契约即真实部署拓扑）。**基线垫底**：靶子按 tests/conftest.py 配方即建（/tmp/opencode/replay-build/get_sign，与转移表 TG 同址），直跑最小环境三码互异先行钉住。**边界**：E3 只到"入口停→续跑→退出"；预扫描断点（E2 语义）与事件回流进会话编排（E4）未动。
**E3 review 升格（同日，用户两钉）**：①环境变量出层——lldb_session 不做发现不改环境；`find_lldb_server(search_dirs)` 纯函数（目录序=入参）、"显式 env 优先"上移启动层、注入=启动参数 `debugserver_path`（launch 前单点赋值=LLDB 消费唯一通道）②纯核升家法代数——结果/错误当值返回不抛（`Drop(reason,detail)`/`Accept(state)`/`ServerFound`/`ServerNotFound`，家法对齐 FrameBad/BadChildMsg）、状态字母表 ADT（`KnownState` 四态+`UnknownState(raw)` 如实携值不假装）、壳路异常携 **error.py 新码族 SessionErrCode 3xxx**（构造期逼显式无默认）。**网炸二枚（迁移期）**：SessionError 类整只漏写（壳路 raise 无家可归）、probe 忘拆包把 ServerFound 整个塞给 str 参数——代数化后"结果当值"要求调用点显式分流，类型系统在场时这类错该由检查器兜（挂账）。**E4 接口已就位**：会话生命周期可按 (动作,事件)→SessionState 建模。
**E3 review 第二轮（同日，用户钉：异常处理全面 _physical 化）**：`_physical` 升格为**缺省版** `(e, fallback)`——SessionError 已带码透传、TimeoutError→EVENT_TIMEOUT、末臂=调用点业务缺省码；五臂显式冗余归缺省（行为等价）。lldb_session 四非纯面全接：init→BAD_TARGET、launch→LAUNCH_FAILED、continue_→**RESUME_FAILED=3010 新员**、exit_code→NOT_EXITED；wait_state/close 不包（裸崩=bug 按家法炸穿；close 尽力而为）。**init 半路失败也立 _created 旗**——半初始化 debugger 进程内重试会踩复用 no-op 坑。schedule `child_request` 写路窄元组放宽 `except Exception`→ChildGoneError（写路一断=通道死，单一结局翻译一行）。**新盲区记档**：重复定义——新旧两个 _physical 并存、后者静默覆盖前者，六网全绿照不出来（新旧行为恰在 NO_LLDB 路等价），直测断言新签名当场逮住——定义级冲突是探针网盲区，纯核单测从此入常例。

## 横向 stackTrace 契约（2026-10-20，守卫入门；用户两钉定稿，本节为转录稿）

**守卫落位（用户钉）**：相位守卫住**子面**（域分层锚：父薄代理盲发；协议 S 门与子相位机是两根轴，父面不记子相位——不撞挂账「事件状态门」）。代价=桩长**最小相位机**：launch→`entry_stopped`（静默）→config_done→`stopped`（+吐 stopped 事件，既有行为不变），与参考 stopOnEntry=True 路同构。**验收范围（用户钉）**：`not_stopped` 负探针押后随 continue 砖——S3 内桩恒 stopped，本砖不可观测；本砖负路=thread 守卫。

**工程分层裁决（2026-10-20，用户三钉定稿）**：**钉1 位置**——调试命令翻译层=适配器本职（实证三锚：pydevd `on_XXX_request` 派发+`NetCommandFactory` 造响应、官方 SDK `DebugSession.stackTraceRequest` 每命令一方法、codelldb `handle_stack_trace`；debugpy 拓扑与本 cleanroom 父子分层同构）。**钉2 形态（翻案史）**——AI 首案「每命令散函数 `proxy_*`/`settle_*`」废（case 与生命周期行混列，层界不可辨识）；二案「step_debug/settle_debug 每命令一 case」废（用户：同构命令该写一次）；**定案=表驱动收编**：`DebugSpec(child_cmd, params, body)` + `DEBUG_SPECS` 表（键=Command 成员），step_debug/settle_debug=通用执行器（查表造 ForwardRequest/OkReply，永不随命令再改）；S3 表外命令=step_debug 查表 miss→状态门 1003。**分离不住在枚举里**：单枚举保持协议词表宣言角色（match_command 一行一承诺），层界=表成员——枚举维护成本零增加。新调试命令=枚举成员+宣言行+一行表（+桩 handler+网）。反"两样本瞎猜"实证：参考父面 server.py:485-530 四命令（threads/stackTrace/scopes/variables）同构纯代理；唯一预告例外=`replay.branchTrace`（OQ-2：走 output 事件流非简单代理，横向收尾单独处理）。去程/回程两函数不可合一：中间隔子进程 I/O（核零 I/O 不变量）；败臂不经表（码随物理事实、话术随 raw_command，泛化单帧已足）。`req.command` 可空性按 parse_request 先例 assert 窄化给检查器（类型检查器接入仍挂账）。判据不变：表行只许词法翻译，引擎语义（相位守卫/世代票据/断点表）住子面。

**参考线形状（规格来源，零代码继承）**：
- 父面 server.py:491-515：参数**不校验**——映射盲发（`threadId→thread_id` 缺省 0、`levels` 缺省 20）；成→`body={"stackFrames": ←stack_frames, "totalFrames": ←total_frames}`（键映射，同 threads 杂键滤除思路）
- 子面 service.py:370-380：`thread_id` 非 int（**含 bool：isinstance(True, int)=True 的坑，参考专门排除**）→`bad_request`；`levels` 坏值静默回 20（宽容，不拒）
- 守卫 session.py:419-426：stack_trace 要求相位 ∈ {`stopped`, `entry_stopped`} 否则 `not_stopped`（OQ-2 相位守卫按命令分集合的首落地；threads 无守卫、scopes/continue 只收 stopped——后续砖的集合分歧从本砖的机制长出来）；线程不存在→`no_thread`；帧形状 `{id, name, line, column, path}`，id=世代*1000+index（世代票据归 scopes/variables 砖首撞，本桩钉形状不钉值）

**核（adapter.py，预计壳零改动=横向复利第二证）**：
- `Command.STACK_TRACE = "stackTrace"` + match_command 宣言行
- `(S3, STACK_TRACE)` → 自回环驻 S3，携 `ForwardRequest(req, cmd="stack_trace", params={"thread_id": args.get("threadId", 0), "levels": args.get("levels", 20)})`——核定名+参数映射在核（子面词法在此定型，壳盲发）
- resolve 成臂：`case Command.STACK_TRACE` → 单帧 `{"stackFrames": result.get("stack_frames", []), "totalFrames": result.get("total_frames", 0)}`
- 败臂：既有泛化臂零改动（普通代理=单帧，码随物理事实、话术随 raw_command）
- 其余状态→既有 fallback 状态门（1003+相位名）

**桩（session.py）**：相位机两态（`entry_stopped`/`stopped`）；stack_trace handler=守卫三码（`not_stopped`/`bad_request`/`no_thread`）+假帧单线程（id=1000，即世代形状 0*1000+0；line/column/path 钉形状的假值）

**网（probe_stacktrace.py，端口 5687）**：P1 S1 门 1003 / P2 成臂形状（stackFrames[].{id,name,line,column,path}+totalFrames int）/ P3 no_thread 负路（threadId=99→2002 既有 Rejected 链）/ P4 收场链照旧。RED 预期：**P1/P2/P3 FAIL、P4 绿（RED 3）**——命令未入 match_command 时 S1 门钉也过不了（parse 阶段 unknown command 1001 兜走，进不了状态机；threads 网 RED 2 同理含 S 门钉）。

**不变量**：壳零改动；参数校验不在父面（参考对齐：父面缺省盲发，守卫全在子面）；错误码随物理事实（ChildRequestError→Rejected→2002 既有链不动）；桩相位机不得改 config_done 的既有可观测行为（stopped 事件形状原样，砖3 探针照绿）。

**预埋攻击点**：①bool 漏判（isinstance(True,int)=True，参考专门排除，桩照抄该哨）②两套词法键名转录拼错（threadId/thread_id、stackFrames/stack_frames、levels 三对）③成臂 match 漏 case（unhandled AssertionError 兜底已在，忘加 case=当场炸）④桩相位机回归（config_done 路两个既有探针网必须原样绿）。

**落地记录（2026-10-20 当日，AI 小步转录+用户逐步 review）**：步序=契约→RED 探针（**RED 3**：P1/P2/P3，命令未入 match_command 时 S1 门钉也过不了——parse 兜 1001，修正初稿 RED 2 误记）→核转录（P1 转绿，RED 2，`test_core.py` 新持久纯核直测网 5/5=E3 盲区裁决兑现）→分层翻案两轮（见工程分层裁决段）→桩转录（`_phase` 相位机 entry→entry_stopped→stopped，守卫序=类型哨(bool 排除)→相位→线程存在，假帧 id=1000=世代形状 0*1000+0，levels 坏值静默回 20 照参考）。**网炸一枚（真收获）**：P3 断言「'no_thread' 进 detail」实测扑空——detail=`ChildRequestError('no thread with id 99')`，**code 属性结构性在、repr 不显**（`__init__` 只交 message 给 super；默认 repr 只显构造参数），detail 串与 `effect_failed` 日志双场合丢子面错误分类学。修法=`ChildGoneError`/`ChildRequestError` 补 `__repr__`（code+message 都进 repr，单点修复两场合同时受益；同构同病顺手对称）。2002 链不动，probe 7/7 全绿。**网况**：probe_stacktrace 7/7+test_core 5/5+五旧网全绿（转移表/threads/launch/framing/childseam）。**not_stopped 负路**照契约押后（S3 内桩恒 stopped 不可观测），探针随 continue 砖补。**编辑纪律教训**：同形代码块（多个 `case _` 兜底臂）并发编辑 oldString 串位两刀（settle_debug 末臂、resolve 末臂各中一次），LSP+整读全文逮住单刀修复——同形块编辑必须带唯一锚点逐个落。

## E4 停读面契约（2026-10-20，用户两钉：现在接·甲就地演化；AI 转录稿待过目）

**范围裁决**：本刀只接**停-读面**=launch/config_done/threads/stack_trace 四命令真化+收场 close 路；continue/exited/running 相位留横向；（动作,事件）→SessionState 全编排建模不抢跑（continue 砖再上）；世代票据（decision 12）**生产侧在此提前落地**（原定 scopes/variables 首撞，scopes 届时只撞消费侧）。**网策略（用户裁·甲）**：桩假分支逐个替换，协议网换真饵——网变强（钉真形状）代价=变慢+机器绑定（lldb+靶子），桩案风险闸「探针钉死真线形状」的既定兑现。

**lldb_session 增员（原语层，纯/非纯劈分照家法）**：
- `Session._phase`：`"entry"`（构造后）→launch 成功（入口停已在 launch 内等到）=`"entry_stopped"`→config_done 报停=`"stopped"`；running/dead 随 continue 砖入册
- `require_phase(phase, allowed, action) -> SessionError | None` 纯函数：守卫判定下沉当值返回（家法）
- SessionErrCode 新员（撞结即补）：`NOT_STOPPED=3011`、`NOT_LAUNCHED=3012`
- `threads() -> list[dict]`：**无相位守卫**（process 在即可，参考同）；单线程契约取 thread 0：`[{"id": int(GetThreadID), "name": GetName() or f"thread-{id}"}]`
- `stack_trace(thread_id, levels) -> dict`：守卫 stopped/entry_stopped（else NOT_STOPPED）→`GetThreadByID` 无效→no_thread（wire 侧 "no_thread"，见 session 映射）→帧数=`min(max(levels,1), numFrames)`，帧=`{id: generation*1000+index, name: 函数名 or 0x{PC:x}, line, column, path}`；`_generation` 初值 **0**（参考 :102 对齐，帧 id 从 0 起）；返回 `{"stack_frames", "total_frames"}`
- `report_entry_stop() -> dict`：config_done 原语——守卫 entry_stopped（else NOT_LAUNCHED）→相位落 stopped→回 stopped 事件 params `{"reason": "entry", "thread_id": thread0 id, "all_threads_stopped": True}`（thread_id=真 SB id，替桩假 1）

**session.py 就地演化（执行面非纯壳）**：
- `ping`/`unknown_command`/帧循环/`--log` hygiene 不动；`load_lldb` 懒加载不进握手路（ping 无 lldb 依赖）
- launch：params 防御校验（ktest/target 非空串，坏=bad_request）→`find_lldb_server`（显式 env>lldb 同目录>/usr/lib/llvm-*，目录序启动层构造）→`Session(target, debugserver_path=…)`→`launch(ktest)`→回 `{pid: process.GetProcessID()}`（真 inferior pid 替桩自身 pid）
- config_done：`report_entry_stop()`→应答 `{}`+真 params 的 stopped 事件
- threads/stack_trace：类型哨（thread_id 非 int 含 bool→bad_request；levels 坏值静默 20）在 child，守卫在原语
- **错误翻译**：`SessionError`→wire `{"ok": False, "error": {"code": err.code.name.lower(), "message": …}}`（3xxx 名转 snake 串；与桩的字符串码族 "bad_request"/"no_thread" 同形）
- **收场路（硬前置）**：stdin-EOF（msg None）与坏帧（exit3）两路都尽力 `session.close()`（try/except 包裹）——真 inferior 不杀会吃机器；无 Session 即直退（ping-only 握手会话）

**探针换真饵**：
- probe_launch/probe_threads/probe_stacktrace：`"k"/"t"`→真饵（KT=`third_party/klee/ktest/get_sign_test000003.ktest` 相对化、TG=`/tmp/opencode/replay-build/get_sign` 配方即建）；转移表网已是真饵不动
- recv 窗口 5s→15s（真 LLDB launch 1-3s；父侧 child_request timeout=10s 照旧，慢机风险挂账）
- **孤儿 inferior 断言**：收场探针加 ps/pgrep 检查（会话毕无残留 get_sign/lldb-server 进程）

**不变量**：父侧零改动（FulfillLaunch/落靶/事件回流/表驱动全不动）；ping 无会话依赖；桩字符串码族形状不变（子面码经 ChildRequestError.code 进 2002 detail——stackTrace 砖的 `__repr__` 修复兑现侦察能力）。**后端缝纪律（2026-10-20 用户钉：session 不变、调试器可换）**：session.py=调试器无关端口面（wire/派发/类型哨/线格式，只说执行面语汇），lldb_session=可换后端适配器（SB/lldb-server/事件分类/相位机归后端所有）——换引擎=写同原语面的新适配器，session.py 一行不改；本刀**保缝不立制**：LLDB 接触收敛单点 import，handler 只调原语方法；Protocol/注册表等正式抽象等第二个后端出现再立（两样本纪律，PROXY_SPECS 同尺）。

**参数化两钉（2026-10-20 用户裁，1R 落地）**：①**调试器选择=参数**——`schedule.py --backend`（缺省 lldb）spawn 时下传 `session.py --backend`（插拔册键，argparse choices 兜拼错）；②**工具链目录=参数**——父子两级 `--lldb-dir` required（同时装 lldb+lldb-server 的目录），**PATH/系统目录猜测全拆**：make_backend 只在给定目录纯查 find_lldb_server，`_import_lldb` 备援二进制按目录取（`lldb_dir/lldb -P`）；E3 的「显式 env 优先」读通道退役（env 只剩 LLDB 消费侧赋值=launch 内单点）——E3 钉部分翻案带因：env 是隐式注入，参数是显式注入，可插拔架构下发现策略归适配器、输入归操作方。**勘误**：本机工具链=llvm-19（lldb 19.1.7），档案 22.1.8 为异机元数据，进度档环境行收口时改。**攻击点①实证了结**：close()（Destroy+Terminate）确实杀净 stopped-at-entry inferior 与 lldb-server（真饵冒烟孤儿双无），process.Kill() 不需补；另桩时代假设纠正：真线程 id=OS tid（非 1）、线程名=程序名（非 thread-1）、世代帧 id 从 0 起——探针换真饵时 threads→stackTrace 两步走。

**落地记录（2026-10-20 当日，E4 停读面关账）**：llvm-22.1.8 切环境（TUNA 缺 llvm-toolchain-22 谱系 403→换 USTC；lldb-19 连包带默认链接卸、/usr/bin 无版本名指 22；clang 留 19，靶子 clang-19 所编 lldb-22 调试无碍）→步1 原语增员（threads/stack_trace/report_entry_stop/require_phase+码 3011-3014；_generation 漏立户由 LSP 逮）→1R 插拔架构定稿（git mv 保史；SessionError 归 error.py；用户连环翻案：散函数废→每命令 case 废→backend.py 单抽层否——**Protocol 归 session.py 端口、SessionError 归 error.py、SessionState/相位机归适配器私有**，各归其位零新层）→步2 端口面毕业（DebugBackend Protocol 结构性满足零循环/插拔册+工厂懒 import ping 路零后端依赖/_run 统一壳/EOF 坏帧两路 close；桩假分支全退役）→参数化两钉（--backend/--lldb-dir 父子 required；PATH/llvm-glob/env 读通道全拆）→步3 换真饵收网。**网炸两枚**：①dist-packages/lldb 空命名空间包顶 plain import（import 成功≠可用：验签 eStateStopped+踢 sys.modules 槽重导）②桩时代假设被真后端纠正——真线程 id=OS tid 非 1、线程名=程序名非 thread-1、世代帧 id 从 0 起：stackTrace 探针改 **threads→stackTrace 两步走**（DAP 客户端本义）。**网况**：七网全绿——转移表 14 真会话首跑+launch L1/L2+threads/stacktrace（新增孤儿 ps 断言双绿）+framing 8+childseam 8+test_core 5；manual_chain 真链代驾全通（launch 0.3s）。**真化红利**：网钉的从假形状变真形状（真 pid/真 tid/真世代票/真符号名），「桩对真分歧进不来」铁律由真后端兑现；协议网过渡期红转绿的差额=参数缺失非行为伤，收网后无回归。后续勘误：clang 同步升 22.1.8（用户裁全家一致），靶子 clang-22 重编基线不动。

## 横向 scopes/variables 契约（2026-10-20，世代票据消费侧首撞；用户两钉定稿：账本归适配器+守卫单集；转录稿）

**参考线形状（规格来源，零代码继承）**：
- 父面 server.py:517-581：scopes——`frameId→frame_id`（缺省 0）盲发；成臂 `{"scopes": [{"name", "variablesReference"←variables_reference, "expensive": False}]}`（**expensive 父面补**，适配器不识 DAP 词）；variables——`variablesReference→variables_reference`（缺省 0）；成臂 `{"variables": [{"name", "value", "type", "variablesReference"}]}`
- 子会话 session.py:461-561：scopes 守卫**单集 stopped**（用户钉，照参考；OQ-2 分集合首分叉——stackTrace 双集/scopes 单集）；frame id 解码（负值/非 int/bool→bad_request；世代≠当前→stale_reference）；_frame_at 验帧（无→no_frame）；**引用=generation*100000+计数器**（第二 id 空间，与帧 id 的 *1000 两套进制），`_var_refs[ref]=(generation, frame_index)` 登记；variables 守卫单集→解引用（未登记/世代旧→stale_reference）→读帧变量**平铺无子**（variables_reference: 0）
- 引用账本 `_var_refs`/`_ref_counter` 归**适配器私有会话态**（用户钉）：引用生命周期与调试器停相绑定，端口面只见不透明 int；账本生命=Session 生命（close 即亡）

**核（adapter.py，表驱动第二/三行，step/resolve 零改动）**：
- `Command.SCOPES="scopes"`、`Command.VARIABLES="variables"`+宣言两行
- DEBUG_SPECS 两行：SCOPES（params frameId→frame_id 缺省 0；body 键映射+expensive 补齐）；VARIABLES（params variablesReference→variables_reference 缺省 0；body 四键映射含 .get 缺省）

**lldb_backend 增员（原语层）**：
- Session 私有态：`_var_refs: dict`、`_ref_counter: int=0`
- `scopes(frame_id) -> list[dict]`：require_phase(("stopped",)，NOT_STOPPED)→世代解码（≠当前→STALE_REFERENCE 3015）→帧存在（≥numFrames→NO_FRAME 3016）→ref=generation*100000+(计数器+=1)→登记→`[{"name": "Locals", "variables_reference": ref}]`
- `variables(ref) -> list[dict]`：require_phase 单集→解引用（未登记/世代旧→STALE_REFERENCE）→帧取变量 `GetVariables(True, True, False, True)` 平铺 `[{"name", "value", "type", "variables_reference": 0}]`
- error.py：`STALE_REFERENCE=3015`、`NO_FRAME=3016`

**session.py（child 端口面）**：类型哨照家法——scopes 哨 frame_id（int/bool/负→bad_request，参考 decode 的类型域归 child）；variables 哨 ref（int/bool→bad_request）；语义守卫（世代/帧存在/相位）全在适配器。

**网（probe_scopes.py，端口 5689）**：P1 S1 门 1003 / P2 全链→stackTrace 取真帧 id→scopes(frameId)→Locals 引用形状 / P3 variables(ref)→平铺形状（name/value/type str+variablesReference int；内容不钉——get_sign 入口帧 Locals 可空，空列表合法）/ P4 stale 负路（frameId=1000001 世代≠0→2002 detail 携 stale_reference；variablesReference=999999→同）/ P5 收场链+孤儿断言。RED 预期：P1/P2/P3/P4 FAIL（命令未实现），收场绿。test_core 增 resolve 成臂映射两行直测。

**不变量**：壳零改动；新命令成本=成员+宣言行+表行（账本第三次兑现）；引用 int 对端口面不透明（解码归适配器）；scopes/variables 不互使引用失效（失效唯一条件=世代推进——continue 砖的事）。

**预埋攻击点**：①两套进制混抄（帧 id ×1000/引用 ×100000，divmod 抄错）②GetVariables 四布尔参序照抄不换位 ③expensive 补齐必须在父面 body_fn（适配器产 execution-face 词）④空变量列表合法（入口帧 Locals 可空）⑤stale 负路走 ChildRequestError→Rejected→2002 既有链，detail 携子面码（__repr__ 修复兑现）。

**落地记录（2026-10-20 当日）**：RED 5（S1 门/成臂×2/stale×2）→步2 核转录（test_core 9/9，表驱动直测四行新增；S1 门转绿，RED 4=子面未接单）→步3 适配器+child。**网炸一枚**：child 把裸 list 直接当 wire result 上线，父面 body_fn 按键取——`AttributeError: 'list' object has no attribute 'get'` 炸穿父 handle 循环（DAP 连接 EOF）；修=child 侧**命令名键重包** `{"scopes": [...]}`/`{"variables": [...]}`（threads/stack_trace 同款家法），「键重包不裸交」从习惯升格为成文教训。**网况**：probe_scopes 11/11 ALL PASS（RED 5→0）+七网回归全绿。新命令成本账本第三次兑现：成员+宣言两行+表两行+Protocol 两签名+child 两哨两重包+原语两件——**核侧 step/resolve 仍字节未动**。守卫单集（scopes/variables 只收 stopped）与 stackTrace 双集并立=OQ-2 分集合首分叉落地；stale 负路经 ChildRequestError(__repr__)→2002 detail 携码。

## continue 契约（2026-10-20，横向与 E4 续刀合流点；用户两钉：泵架构参考同构+父面翻译扩入本砖；转录稿）

**范围（用户钉）**：①continue 代理+响应（轻件照表驱动旧例）；②**子进程事件泵**（参考同构：reader 线程+owner 轮询循环——参考 spike-verified 形状，owner-thread 契约天然保持：reader 只读 stdin 永不碰 SB，owner 单写者）；③世代推进（决策 12：resume 使引用失效——stale 首次真实可达）；④**exited/terminated 父面翻译扩入本砖**（用户裁扩大：DAP 生命周期全通；output 事件仍留后续）。**有意偏离带因**：参考 `_MAX_RESUMES` 安全阀（M1 一次性回放防病理循环）不随迁——M2 交互式由客户端节奏驱动，病理循环不可达，valve 反成不可观测自杀路。

**参考线形状（规格来源，零代码继承）**：
- 父面 server.py:621-630：continue **参数忽略**空参盲发；成臂 body=常量 `{"allThreadsContinued": True}`——我们改**单源映射**（child 回 all_threads_continued，body_fn r.get 缺省 True；细节自裁入档）
- 子会话 session.py:330-336+377-399：守卫 stopped→_resume：**spike 规则**（非阻塞排空队列残事件，历史不当新闻）→**世代推进**（generation+=1、var_refs.clear()）→`process.Continue()`（异步立即回）→phase="running"；完成以事件到达，response 不等它
- exited/terminated：session poll（:619-629）exited→exit_code 捕获→phase="dead"→`[exited{exit_code}, terminated{}]` 双事件；父面 translate（:769-793）**一变二**：exited→`exited{exitCode}+terminated{}`（DAP 惯例：terminated 随 exited）+terminated→terminated{}；stopped→stopped 既有

**核（adapter.py，表驱动一行）**：`Command.CONTINUE="continue"`+宣言行；DEBUG_SPECS 行：params={}（参考参数忽略）、body=r.get("all_threads_continued", True)→`{"allThreadsContinued": …}`（单源在适配器）

**适配器（lldb_backend 增员）**：
- `continue_() -> dict`：require_phase(("stopped",)，NOT_STOPPED)→非阻塞排空残事件（spike 规则，GetNextEvent 循环）→generation+=1→_var_refs.clear()→process.Continue()→phase="running"→回 `{"all_threads_continued": True}`
- `poll_events() -> list[dict]`：非阻塞 GetNextEvent 循环（classify_event 复用；抽 `_read_event` 助手与 wait_state 共享取数）；STOPPED→phase="stopped"+stopped 事件 params（reason 缺省 "exception"——推断裂归 setBreakpoints 砖）；EXITED→exit_code 捕获→phase="dead"+`[exited{exit_code}, terminated{}]`；CRASHED 同 exited 形（exit_code 取 GetExitStatus 语义值）；DETACHED/Unknown→留痕丢弃
- wait_state 阻塞版保留（launch 入口停专用）

**child（session.py，参考同构）**：reader 线程（read_frame 循环→队列；半帧 buf 活在 reader 内；坏帧=fatal、EOF=eof）+owner 轮询循环（每轮：后端在→`poll_events()` 先发事件帧→`queue.get(timeout)`→handle）；单写者不变（仅 owner send）；退出码语义不变（eof→0/fatal→3）；hygiene 先于 reader 启动

**父面 translate_child_event**：返回类型 `Event | None` → `tuple[Event, ...]`（exited 一变二）；schedule.forward_event 拆包接线随之

**网（probe_continue.py，端口 5691）**：P1 S1 门 1003 / P2 全链→continue→resp success+allThreadsContinued True / P3 continue 后 stackTrace→2002 携 not_stopped（running/dead 相位皆可，不钉相位名——get_sign 秒退）/ P4 再 continue→2002 not_stopped（自身守卫）/ P5 旧帧 id 负路→2002 携 not_stopped（**勘误（RED 后实测）**：参考守卫序=相位先于世代解码，continue 后 inferior 秒退即 dead，not_stopped 先答——stale_reference 待 setBreakpoints 砖产出第二次停相后真实触发，pins 随砖迁移）/ P6 exited/terminated 事件链：exited{exitCode int} 先+terminated{} 随（宽容收齐，exited 先于 terminated 钉死；**child 只发执行面事实 exited，terminated 展开归父面一变二——单源**）/ P7 收场链（disconnect 对 dead inferior 拆除）+孤儿。RED 预期：P1-P6 FAIL（unknown 1001/事件不来），P7 绿。test_core：CONTINUE 出站映射+成臂映射直测两行。

**不变量**：协议状态 S3 全程不因 running/dead 改变（「事件不改协议状态」裁定继续成立）；响应不越其引发的事件（child 单写者顺序天然保）；断路器 disconnect 对 dead inferior 拆除安全（close 尽力而为既有）。

**预埋攻击点**：①reader 启动先于 hygiene=杂音污染 fd1（顺序钉死：hygiene→reader）②半帧 buf 跨线程共享（buf 活在 reader 内，队列只传完整 msg）③exited 一变二——translate 返回类型变更、forward_event 拆包忘改④exited 与 continue 响应竞争（秒退），探针宽容序收帧⑤close 对已 dead inferior 的幂等性（若炸当场见）⑥poll_events 在 phase dead 后持续空转（须终态短路防忙烧）。

**落地记录（2026-10-20 当日）**：RED 6→步2 核转录（表一行；P1 转绿，test_core 11/11）→步3 四件套（适配器 continue_/poll_events+_read_event 取数助手重构、child reader 线程+owner 轮询循环、父面 translate 一变二、直测两行）。**网炸两枚**：①**端口面漏接单**——原语/泵/翻译全建而 handle 无 continue 分支（子面答 "unknown command 'continue'"，全件建成却忘了门牌）；②**terminated 双发**——child 按参考形状发 [exited, terminated] 对，父面一变二再展开=客户端三帧（terminated×2），帧序错位连锁误判三钉；修=**单源裁决**：child 只发执行面事实 exited，DAP 惯例展开（terminated 随 exited）归父面翻译。**契约勘误一枚**：P5 stale_reference 预测被守卫序推翻——参考相位先于世代解码，continue 后 inferior 秒退即 dead，not_stopped 先答；stale 真实触发待 setBreakpoints 砖产出第二次停相（pins 随砖迁移）。**网况**：probe_continue 12/12 ALL PASS+八网回归全绿。**真化里程碑**：DAP 生命周期首次全程可观测（launch→入口停→continue→exited+terminated）；响应不越其引发的事件由 child 单写者顺序天然保证（exited 先于 terminated 同源保序）。

**落地记录（2026-10-20 当日）**：RED 6→步2 核转录（表一行；P1 转绿，test_core 11/11）→步3 四件套（适配器 continue_/poll_events+_read_event 取数助手重构、child reader 线程+owner 轮询循环、父面 translate 一变二、直测两行）。**网炸两枚**：①**端口面漏接单**——原语/泵/翻译全建而 handle 无 continue 分支（子面答 "unknown command 'continue'"，全件建成却忘了门牌）；②**terminated 双发**——child 按参考形状发 [exited, terminated] 对，父面一变二再展开=客户端三帧（terminated×2），帧序错位连锁误判三钉；修=**单源裁决**：child 只发执行面事实 exited，DAP 惯例展开（terminated 随 exited）归父面翻译。**契约勘误一枚**：P5 stale_reference 预测被守卫序推翻——参考相位先于世代解码，continue 后 inferior 秒退即 dead，not_stopped 先答；stale 真实触发待 setBreakpoints 砖产出第二次停相（pins 随砖迁移）。**网况**：probe_continue 12/12 ALL PASS+八网回归全绿。**真化里程碑**：DAP 生命周期首次全程可观测（launch→入口停→continue→exited+terminated）；响应不越其引发的事件由 child 单写者顺序天然保证（exited 先于 terminated 同源保序）。

## setBreakpoints 契约（2026-10-20，第二次停相产出者；非纯代理首例=子面断点表；转录稿）

**范围**：setBreakpoints 写命令（断点表=适配器私有会话态）+**断点命中上报**（stopped reason="breakpoint"+hitBreakpointIds——第二次停相产出者，not_stopped/stale_reference 负路真实触发+reason 推断同砖收口）。output 事件（inferior stdio 转发 decision 13）仍留后续。

**参考线形状（规格来源，零代码继承）**：
- 父面 server.py:583-618：params 提取（source.path+breakpoints[].line，字典项过滤）→`set_breakpoints` 盲发；成臂 `{"breakpoints": [{"verified": bp.get("verified", False), "line": bp.get("line")}]}`（键映射+verified 缺省 False）
- 子会话 service:321-329：path 非 str→bad_request；lines 非 list→bad_request
- 子会话 session.py:492-530（**断点表=适配器私有**）：process 无→not_launched；逐行验证（非 int/bool/<1→bad_request）；**全量替换**语义——先删全部自有断点再逐行 `BreakpointCreateByLocation`；invalid→`{"line": line, "verified": False}`；valid→resolved line（location address 的 line entry）+`{"line": resolved, "verified": True}`；自有 bp id 表 `_own_breakpoint_ids` 记册（**参考简化对齐**：单一全局断点集，非 per-source 分册——DAP per-source 语义在参考即简化，随迁）
- 命中上报：stopped 事件 params 携 `breakpoint_ids`（父面 translate 已有 hitBreakpointIds 映射位）+reason="breakpoint"——**有意偏离 M1**：参考 `_on_stop` 的 record-and-auto-continue（branch site 表）是 M1 回放语义，cleanroom DAP 断点命中一律上报，site 表归 branchTrace 砖另议

**核（adapter.py，表驱动一行）**：`Command.SET_BREAKPOINTS="setBreakpoints"`+宣言行；DEBUG_SPECS 行：params_fn（source.path+lines 提取照参考 :584-592）、body_fn（breakpoints 键映射+verified 缺省）

**适配器（lldb_backend 增员）**：
- Session 私有态：`_own_breakpoint_ids: list[int]`
- `set_breakpoints(path, lines) -> dict`：全量替换（删旧记册→逐行创建）；invalid→`{"line": line, "verified": False}`；valid→address line entry 解析 resolved→`{"line": resolved, "verified": True}`+记册；回 `{"breakpoints": [...]}`
- poll_events STOPPED 臂升级 **reason 推断**：thread.GetStopReason()==eStopReasonBreakpoint→reason="breakpoint"+`breakpoint_ids`（GetStopReasonDataAtIndex(0)）；否则 reason="exception"（既有）；phase→stopped
- 命中即第二次停相：generation 已在 resume 时推进——旧世代帧 id/引用自此真实 stale

**child（session.py）**：类型哨照家法（path 非 str→bad_request；lines 非 list→bad_request；行内逐项 int/bool/<1→bad_request——参考 decode 域归 child）+`_run` 键重包 `{"breakpoints": ...}`；Protocol+`set_breakpoints` 签名

**网（probe_breakpoints.py，端口 5693）**：断点打 `get_sign.c` line 20（main 首句 klee_make_symbolic，必经之路；源码绝对路径=编译期记录）：
- P1 S1 门 1003 / P2 全链→setBreakpoints [20,21]→两行 verified True+line int / P3 continue→**stopped(reason="breakpoint")**+hitBreakpointIds 非空+threadId int（第二停相产出）/ P4 第二停相面：stackTrace→帧 0 落 get_sign.c line 20（path 非空）→scopes(新帧 id)→Locals 新世代引用→variables 平铺（内容不钉）/ P5 **stale 真实触发**：scopes(frameId=0)（旧世代 0）→2002 携 stale_reference / P6 替换语义行为钉：setBreakpoints [21]→continue→停于 line 21（若旧 bp 20 残留则先停 20=替换失败当场见）/ P7 末轮 continue→exited+terminated（生命周期闭环）/ P8 收场+孤儿
- RED 预期：P1-P5 FAIL（命令未知/断点不生效 continue 直通 exit）、P6 错位 FAIL、P7-P8 绿（生命周期事件已通）
- test_core：setBreakpoints 出站映射（params_fn 提取）+成臂键映射直测两行

**不变量**：壳零改动（表驱动照旧）；断点表对端口面不透明；命中上报走既有事件回流（sink→translate→hitBreakpointIds 位）；替换语义=全量删旧（参考简化随迁，per-source 分册不做）。

**预埋攻击点**：①params_fn 嵌套提取（source 或缺/breakpoints 项非 dict 过滤）②resolved line 与请求行可能不同（地址回解析）——探针钉 verified+line int 不钉相等 ③GetStopReasonDataAtIndex(0)=bp id（LLDB 约定），data 计数 0 时不得越界 ④断点命中停相与 continue 响应竞争（同 continue 砖，宽容收帧）⑤reference 简化风险自警：全局单集断点在多源场景丢断点——DAP 客户端逐源调用时后调者清前者（参考即此行为，随迁）。

**落地记录（2026-10-20 当日）**：RED 17 查 10 FAIL（P1-P7 红/P8 绿）→步2 核转录（Command.SET_BREAKPOINTS+宣言行+表一行；P1 转绿，test_core 12/12）→步3 四件套（backend：_own_breakpoint_ids 记册+set_breakpoints 原语+stop_reason_label 纯核推断+_ingest_enums 加 eStopReasonBreakpoint+poll_events STOPPED 臂升级携 breakpoint_ids；child：handle 门牌+_set_breakpoints 类型哨+键重包+Protocol 签名）。**网炸一枚（双键重包）**：契约内部互斥——适配器节写"回 {"breakpoints": [...]}"、child 节写"再重包"，首跑 P2 即炸 AttributeError: 'str' object has no attribute 'get'（父面 body_fn 遍历到内层 dict 的键串）；修=**bare list+child 重包**（家法同构：threads/stack_trace/scopes/variables 全同款），与 f4b1bca 网炸（裸 list 忘重包）成对——键重包家法的两个失败方向（忘包/双包）各实证一回，网各逮一枚。**守卫分布裁决**：类型域（path 非 str/lines 非 list/行内逐项 int·bool·<1）归 child（家法：参考 decode 域迁移）；not_launched（process 无）+空 path 留 backend（参考原位；child 的 _backend_ready 是另一条件——无实例≠无 process，父面 S1/S2 状态门挡不到的防御）。**RED 预测勘误（用户裁照单收）**：契约 RED 预期"P7-P8 绿"不成立——单链下 P3 continue 直通 exit，exited+terminated 在 P3 段已被消费，P7 再 continue 只答 not_stopped；实为 P1-P7 FAIL/P8 绿，GREEN 钉不变。**攻击点了结**：①嵌套提取过滤由 test_core 直测盖（非 dict 项滤除）②resolved line 实测与请求行同（20/21），探针只钉 verified+int 照旧 ③data 计数 0 越界防落 stop_reason_label（count>0 才取 data0）④命中停相与响应竞争照 continue 砖宽容收帧（段定员 3）⑤全局单集简化随迁照旧。**网况**：probe_breakpoints 17 查 ALL PASS+九网回归全绿（test_core 12/12+八探针网）；里程碑：断点命中第二停相全程可观测（launch→入口停→bp 停 L20→替换 L21→bp 停 L21→exit），stale_reference 负路自 continue 砖押后以来首次真实触发（世代票据消费侧闭环）。

## 横向 step 族/terminate（2026-10-21 提议→同日裁决，用户两段式重定性）

**裁决（2026-10-21，用户）**：
- **next/stepIn/stepOut＝押后，非砍死**：回放最终需要——它们是断点停下后的
  **用户调试策略**；当前阶段不实现，因非 M2（回放+报告产出）之急。原 M2-d
  规划地位恢复（排期押后至 M2 收尾后）。
- **pause/terminate**：当前阶段不实现；disconnect 收场已覆盖（pause 救急面
  E3 卡 stdin 坑实证过、terminate 答 unknown 1001 可接受）；需求实证出现
  再议（挂账维持）。

**rationale**：E2 定形后回放核=预扫描插桩+actual 记录+报告——推进靠
bp+continue；交互步进属调试策略层，不承重 M2。DAP 家法"可选才广告"。

**过程存痕**：契约草+推定钉六枚曾落纸，probe_step.py RED 8 FAIL——真饵链
bp L20→next→L21→stepIn→get_sign→stepOut→main L21 已验证可观测——随押后
撤销，链路知识留本档（M2 收尾后按原 M2-d 续）。悬钉"pause 正路真饵（get_sign
秒退无 running 窗口）"随押后挂起（实现时需长跑靶子）。

**连带**：协议线横向待办只剩 branchTrace（横向收尾，引擎集大成）——与引擎线
合流；"协议砖/引擎件交替制"随横向收尾自然终结。roadmap en/zh 需编辑性修订
（M2-d 押后/M2-c 改写/一致性校验归 M4——branchTrace 关账后一并做）。

## step 族（2026-10-23 定案，原 M2-d 兑现；2026-10-21 押后钉解除）

**模糊架构两钉（用户，2026-10-23）**：
- **同步阻塞模型**：SBThread.StepOver/StepInto/StepOut 等步完才返（参考
  session.py:338 同构）；child_request 等待，停相随响应面之后由事件泵到达
  （SB 完成停相进 listener 队列，handle 下轮 poll 消费），无 running 可观测
  窗口。代价=步进耗时期间子进程无响应面（回放工作负载步进皆短步，可接受；
  撞长步再议异步）。
- **三件起步**：next/stepIn/stepOut；pause 维持押后（需长跑靶子，get_sign
  秒退无 running 窗口——2026-10-21 悬钉维持）；restart/stepBack 不做。

**定形（撞到即裁记录）**：
- **核（session_lifecycle）**：ExecTrigger 三成员（step_next/step_in/
  step_out）+_GUARDS 三行（允许相位={STOPPED}，同 CONTINUE，NOT_STOPPED）+
  _TRANSITIONS 三行（照 CONTINUE 形：RESUME_INFERIOR→RUNNING，世代 +1——
  resume 家法：世代推进照 continue 一次，ingest 停相不触碰世代，相位账
  STOPPED→RUNNING→(ingest)→STOPPED）。ingest_exec 零改动（StopObserved
  通用臂天然承接 step 停相；断点 CaptureNode 臂按 reason 分派不误触）。
- **backend（lldb_backend）**：step_next/step_in/step_out 三函数（守卫→
  GetThreadAtIndex(0) 同步步进→resolve+commit 照 continue_ 模式）；stop
  reason 新映射 _PLAN_COMPLETE_REASON→"step"（load 时灌入，同
  _BREAKPOINT_REASON 家法）。
- **child（session）**：handle 三门牌（"next"/"step_in"/"step_out"）+
  DebugBackend Protocol 三签名；响应体 {}。
- **父面（adapter）**：Command 三成员（next/stepIn/stepOut）+match_command
  三 case+DEBUG_SPECS 三行（params={}——threadId 单线程照 continue 家法
  忽略；body={}——DAP step 响应无 body）；S2|S3 整层路由现成转发。
- **网（probe_step.py，5703，真饵）**：存痕链复活——launch→setBreakpoints
  L20→configDone→continue→bp 停 L20→next→停 L21→stepIn→get_sign 函数体
  →stepOut→回 main L21。8 查：三命令响应 success 无 body/stopped
  reason=step/帧0 落点三连/收场照旧。RED 现状=unknown command 全红（命令
  缺位形）。

**落地记录（2026-10-23，三小步转录）**：RED 8 FAIL/2 PASS（缺位形）→
①核（ExecTrigger 三成员+守卫三行+转移三行照 CONTINUE 形）——纯核网先铺钉
（test_session_lifecycle 增 step_resume_family 同形钉+矩阵三行）RED 缺位形→
ALL PASS；②backend+child+父面——probe_step **网炸一枚**：SB 绑定返回面
不统一——StepOver/StepInto/StepOut 成功返 **None**（非 SBError），
err.Fail() 炸 AttributeError→2002 resume_failed，而步进本体已执行（T2/T3
行为面同网绿）——同网双面各逮一格（响应面 T1×行为面 T2）——归一
None=无错形态→**ALL PASS**（含 T6 世代钉：next 后旧 frameId
stale_reference，resume 家法世代语义真线兑现）；③全量 20 网 rc=0+pytest
20/20。**撞到即裁两笔**：threadId 单线程照 continue 家法忽略；Protocol
声明面欠账（continue_/wait_state/exit_code continue 砖已欠+step 三签名
未入——结构性满足照跑）挂账。dap.md 命令清单/roadmap 押后注记/进度档/AGENTS
20 网同步。

## M3 路径缩小（KLEE-fork 子集圈定；2026-10-23 用户定案）

> 正式记录：[ADR-0004](../../en/adr/0004-m3-path-narrowing.md)（M3）/
> [ADR-0005](../../en/adr/0005-m4-path-consistency.md)（M4，en 为权威本）。
> 本节=实施契约与对账记录，随转录滚动。

**阶梯模型（用户）**：M1+M2 实际路径记录 → M3 LLVM IR 侧验证**缩小路径可能性**
（获得更清晰的路径）→ M4 符号执行↔实际路径**完整校验**（.kquery 对账）。前层=
后层必要条件，后层可选：只做 M1+M2 可以结束；做到 M3 路径变清晰；做到 M4 完整
校验。**范围随之落定**：M3=方法1（.ll 静态符号性）+方法3（手工真值验收）；
**方法2（.kquery）整体归 M4**。

**三分类（圈定的本体）**：
- **fork**：条件 ∈ 符号闭包——KLEE 在此分叉，.ktest=其中一支的采样
- **噪声A**：站点所在函数不在 .ll 模块——KLEE 世界根本不存在的代码（回放
  runtime 的文件读取/对象名比对；判据自然涌现：get_sign.ll 不含 intrinsics.c）
- **噪声B**：条件不在符号闭包——具体分支（栈检查/循环边界）

**定形**：
- **launch 参数 `ir`**（optional，string=.ll 路径）——阶梯的运行时体现：未给=
  现状零增帧（M1+M2 层），给了=trace 报告注记（M3 层）；坏 .ll→launch 期
  Rejected（ktest refused 家法 fail-fast）
- **注记层**：trace 报告节点增字段 kind=`fork|runtime` + detail 携判据
  （symbolic-branch / function-not-in-module / concrete-condition）——A/B 在
  detail 里区分，v1 kind 二值；注记=派生层，零处理红线不破；**走向（taken/
  not-taken）M3 v1 不落**（站序隐含），M4 需要时即裁
- **判定机制**：.ll 轻量符号数据流——make_symbolic 调用点→内存标记→load/
  icmp 传播→直接调用实参→形参绑定→br 条件 ∈ 闭包？；新纯件（replay/ 册，
  名暂 pathmark，可议）；.ll=clang -emit-llvm 链路外产物，服务零 KLEE 依赖
- **DWARF 桥接**：.ll !DILocation(line) ↔ native actual line/function **行级
  join**（两次编译的既定边界，不做指令级）
- **验收真值（手工先行，get_sign 三 ktest）**：
  - test1（a=0，exit 0）：fork=[L11]；L13 不出现（早退）
  - test2（a=0x01010101，exit 1）：fork=[L11, L13]（双 not-taken）
  - test3（a=0x80000000，exit 255）：fork=[L11, L13]（L13 taken）
  - 噪声=intrinsics.c 全部（A 类）；入口停非决策
- **M3/M4 分工**：M3 答"这站是不是符号分支"（程序性质，静态）；M4 答
  "KLEE 在这条路径建立了哪些约束"（执行事实，.kquery）——将来两网互证

## branchTrace 预扫描引导回放+报告（2026-10-21 定案，M2 收尾件）

**定案链（用户，2026-10-21 三段演进）**：纯被动汇总（AI 提，废——用户：
"回放的语义就没了"）→ 引导回放推定（AI 评）→ **target 降格+actual 扩容定案
（用户）**：
- **target=预扫描站点集**（E2 is_conditional 首个真消费点）：唯一作用=调试器
  下断点的依据；记进报告但不参与校验——集合对集合（站点集→断点表），
  假比对问题消解，M1/ADR-0002 honesty 对齐。
- **actual=断点命中后据实获取**：pc/function/mnemonic/调用栈/**符号 value**
  等。记录点=断点命中（入口停非命中，不记——定义使然非剔除），零处理红线
  照旧（命中什么记什么）。
- **value 级一致性入报告**：注册符号值 vs ktest 解采样点——局部可验，现在做；
  **路径级一致性=独立校验工具**（路径建模/求解/分析——归 **M4 轨迹分析叠加
  层**，方案 A 用户裁；不进回放——"薄桥"定位，SMT 会破零依赖）。报告=两个
  产品的接口契约。
- **报告=跑末汇总 output 事件**：路径线（actual 线性序列+索引，重复站点自然
  呈现环）+节点详情（pc/function/mnemonic/调用栈/符号值+一致性注记）。
  一致性注记=派生层（记录点仍零处理）；unmatched/ambiguous 如实标注不猜。
- **无命令面**（纯被动，用户裁）；查询需求挂账。E2 match_path/equal 不接
  回放（M4 校验工具消费，代数留册）。

**撞到即裁钉**：
- launch 参数 **scope**（预扫描范围）：现 schema 只有 ktest/target，M2-b1
  冻结 schema 本有 scope 位——补参；缺省语义随转录裁
- 报告格式：人读文本 vs 结构化 vs 双形式（推荐双形式——接口契约形状早定；
  output 文本+body 结构化字段并存）
- 汇总触发：EXITED/CRASHED 收场点单发；disconnect 中断不汇总（当前阶段）
- 符号值读取：backend 停相直读（SBFrame 变量+pc 单条反汇编）；ktest 对象名
  ↔局部变量名匹配=启发式，报告如实标

**网（probe_trace.py，端口 5695，真饵单链）**：launch（携 scope="main,get_sign"）→
configDone→**自动插桩**（客户端零 setBreakpoints——插桩证据）→continue→停站点1
（reason=breakpoint）→continue→停站点2→continue→收场段 [resp, output(汇总),
exited, terminated] 宽容序→文本形状（含 0x 地址行/<get_sign>/节点行）→
disconnect+孤儿照旧。RED 预期（勘误随 breakpoints 砖先例）：第一次 continue
直通 exit，exited/terminated 在 P2 段已被消费——RED 实测 4 FAIL（P2/P3/P4a/
P4b；P4a 红因事件提前消费非生命周期伤），P1/P5 绿。

**落地记录（2026-10-21 当日，AI 小步转录+网逐步验证）**：
- **步1 预扫描+自动插桩**（configDone 时、入口停内——PIE 载入地址此时才解析）：
  lldb_backend 懒挂 replay 册路径（cleanroom 内部复用 E2 is_conditional+E1
  ktest——非 M1 继承）+`_instrument_scope`（M1 规格改写：主模块 code symbols
  反汇编/单表筛选/地址去重排序/BreakpointCreateByAddress/凭册同册/_MAX_SITES=5000
  守阀）+launch 增 scope 参数（Protocol/child 类型哨同步）。**网炸一枚（scope
  在壳被丢）**：launch 单是壳手工造的三行（schedule run_effect），scope 键没上
  单——裸驱后端全好、DAP 链直通 exit，裸驱/线测分责逮住；修=壳造单补 scope
  透传。步1 后 1 FAIL（P4b=步3 的活）。
- **步2 停相记录面**：poll_events STOPPED 臂 reason=breakpoint 即记
  `_capture_node`（pc/GetFunctionName/pc 单条反汇编 GetMnemonic/调用栈全帧/
  ktest 对象名↔FindVariable 匹配+字节对账注记 match/mismatch/ambiguous/
  unmatched——注记=派生层，原始值如实；ktest 解析失败=空集如实不猜）。
  **匹配启发式现形**：get_sign 帧只有 x，ktest 对象 a=unmatched——据实标注
  正确行为（定义帧才有对应局部）。
- **步3 汇总+通道**：`_render_trace`（行式 #序号 0x地址 <函数> 助记符 [符号
  注记]）+EXITED/CRASHED 臂携 trace_report 载荷（**仅 scope 在册会话附报告
  ——未启用会话零增帧**）+父面 translate exited 一变三（output 先发=过程输出
  先于终态告别；body 双形式：output 人读文本+report 结构化节点）。步3 后
  **probe_trace ALL PASS**。
- **RED 探针勘误一枚（AI 钉错自收）**：P4b 钉 `<main>`——test3 路径两命中都在
  get_sign 帧，勘误为 `<get_sign>`（照 breakpoints 砖"RED 勘误照单收"家法）。
- **回归网两笔记**：①probe_e3 陈旧（launch→continue_ 直连，continue 砖收紧
  相位守卫后未随网更新、此后未入回归清单）——补 report_entry_stop 放行步，
  与本刀无关的存量陈旧；②probe_breakpoints 循环语境 flaky 两例（单跑/轻语境
  全绿，深循环红且形状不一、rr3 server 退出码非 0）——**失败取证入网**
  （server stdout 尾巴失败即倾倒），取证版全循环 13 网全绿，flaky 根因未坐实
  挂账盯梢。
- **网况**：probe_trace ALL PASS+**全循环 13 网回归绿**（含真饵九网+纯网四件）。

**不变量**：记录点零处理（据实红线）；比对不入境（M4 工具）；无新命令；单源
（child 事实/父面翻译）；未启用会话零增帧；E2 代数不接线（M4 消费）。

## E4 编排代数化契约（2026-10-22，结构收敛刀：绿网上动结构；用户先验两钉+AI 攻击三枚+转录稿）

**先验（用户两钉，2026-10-22）**：①执行面照协议面 v5 家法**代数化**——(动作，事件)→
新状态的纯转移核、壳只执行动作、迁移不再散装；②加**纯核直测网**（直接发挥 FP
白盒测试优势）。

**AI 攻击记录（照单收，用户无异议）**：
1. **双触发源**——协议面"事件不改协议状态"，执行面事件**就是**转移（STOPPED→
   stopped、EXITED→dead）。核开两入口：命令转移（step/resolve）+事件转移（ingest）。
2. **守卫矩阵实为两轴**——现状 threads 零相位守卫（process 在即可）、set_breakpoints
   只查 process 在场、余者查相位（stack_trace∈{stopped,entry_stopped}、scopes/
   variables∈{stopped}（OQ-2 分叉照旧冻结）、continue∈{stopped}、launch∈{entry}、
   report_entry_stop∈{entry_stopped}）。表把矩阵收成数据，怪相从散装代码变可见表行；
   **行为冻结，逐行照抄现状含错误码**。
3. **唯一声明中的行为差：continue 失败臂世代推进时序**——现状推进+清账在
   process.Continue() 之前（失败也推进，旧帧 id 变 stale 尽管同一次停）；代数化=
   转移提交制，效果成功才提交，失败世代不进。用户照单收（2026-10-22）。

**定形**：
- **核**=`src/server/session_lifecycle.py`（新册，后端无关——编排出端口契约定义；照
  adapter.py 独立成册先例；lldb_backend 只剩壳）。`SessionState` 名已被事件词汇表
  占用（KnownState|UnknownState），编演 ADT 铸名 **ExecPhase/ExecState**（撞随铸）。
- **核状态**=ExecState{phase: ExecPhase（entry/entry_stopped/stopped/running/dead
  五值 ADT，裸字符串退役），generation: int}。世代是守卫输入故入核；var_refs/
  bp 表/轨迹节点/scope/sym_objects 是载荷数据，留壳私。
- **核面**：`guard_exec(state, trigger) -> SessionError | None`（全矩阵，九触发器×
  五相位）；`step_exec(state, trigger) -> ExecStep{state'=提案，effect，error}`
  （三个转移命令：LAUNCH/CONTINUE/REPORT_ENTRY_STOP，守卫内嵌，error 行=提案原值）；
  `resolve_exec(state, step, outcome) -> ExecState`（**提交制**：ExecOk→提案，ExecFail→
  原值——行为差③由此结构兑现）；`ingest_exec(state, fact) -> ExecIngest{state',
  emits}`（StopObserved(reason)→STOPPED+断点命中声明 CaptureNode、ExitObserved→
  DEAD+EmitExit）；`poll_blocked(state) -> bool`（DEAD 短路=冻结真行为）。
  效果词汇=ExecEffect（LAUNCH_INFERIOR/RESUME_INFERIOR/FINISH_CONFIG，铸名随撞随改）。
- **壳**（Session 瘦身）：取数成 plain fact（classify_event/stop_reason_label 纯件
  复用）→guard→效果→resolve 提交；世代差=壳清账信号（new.generation≠old→var_refs
  clear）。wait_state（SB 阻塞 I/O）与 close（砖4 押后件）不入核。
- **防御表行（端口不可达区域，用户批的类）**：threads/set_breakpoints×ENTRY→3012
  （现状=裸崩区，家法裸崩=bug）；poll×ENTRY→短路（现状=潜藏崩溃区：launch 失败后
  _backend 残留、poll 遇事件 classify 对 None 取 pid 即炸）。
- **网策略**：新增 `probes/test_session_lifecycle.py`（无端口无 LLDB，test_core 同款家法）
  钉：矩阵逐行（九触发×五相位）+转移集+resolve 提交制语义+ingest 声明+铸名值与
  旧字符串同形（壳迁移对账用）。15 网回归=行为冻结证据，零改动。
- **小步序列（每步用户 review+网验证）**：①增核册（additive，树零动，RED 全红→
  绿）②壳换守卫+命令转移（launch/report_entry_stop/continue 走核）③壳换事件
  ingest+require_phase 退役+档案。
- **撞到即裁清单（已铺不作废，随转录消费）**：ExecEffect 铸名终形；poll STOPPED 臂
  取数先后结构（取数先于 ingest 声明）；_MAX_SITES 守阀落位（随 FINISH_CONFIG 效果
  留壳）；进程在场谓词是否升核（现为表行近似）。

**落地记录（2026-10-22 当日，E4 编排代数化关账）**：RED 25（模块缺位全红基线，
probe_ktest 家法 MODULE_OK 门+清单即需求文本；首版矩阵查被缺位连坐吞 8 行，改
字符串名惰性取枚举补全）→**步1 增核册** `session_lifecycle.py`（additive 树零动；转录
一次过 25/25 零网炸）→**步2 壳换守卫+命令转移**（`_exec: ExecState` 替换
`_phase`/`_generation` 双散装；三转移命令 step→效果→resolve→`_commit`（世代差=
壳清引用账本信号，清账随提交）；读守卫×5 换 guard_exec；threads/set_breakpoints
防御行接装；**require_phase 退役**——契约写在步3，调用点全走核后成死码提前删，
步3 只剩 ingest+档案）→**步3 壳换事件 ingest**（poll 两臂 StopObserved/ExitObserved
落靶+CaptureNode/EmitExit 意图消费，穷尽 match assert_never；停相事件=转移直接
产物现场组装，收场发帧=核声明意图壳执行）→档案。**LSP 实战**：步2 逮 `_generation`
漏改四处（stack_trace 帧 id/scopes 两处/variables）——挂账「类型检查器」首次兑现。
**转录自收两笔**：_GUARDS 首稿坏句法+把 SessionError 实例存表（改码存表、错误按
相位现造）；assert_never 用而未导。**网况**：pytest 20/20+**16 网 rc=0 全绿**
（新增 test_session_lifecycle=纯核直测第 4 网；靶子易失重建按 conftest 配方、基线
0/1/255 先钉后跑；flaky breakpoints 网本轮绿）。**行为冻结对账**：转移/守卫/
错误码逐行照抄（含 OQ-2 分叉、dead 相位放行 set_breakpoints）；声明行为差一枚
兑现（continue 失败臂世代不进=提交制结构红利）；防御行两枚生效（threads/
set_breakpoints×ENTRY→3012、poll×ENTRY 短路——均端口不可达区，crash→干净错）。

## 砖4+砖5 收场契约（2026-10-22，协议线收场；用户三钉：带 TearDownChild·拆除后发／退出码归类=日志+落靶 detail／混沌三路起步）

**先验（用户三钉，2026-10-22）**：①disconnect 带 **TearDownChild** 动作，**拆除后发**
（双帧随 resolve 落靶，对齐参考"terminated 是拆除副产品"帧序）②退出码归类：
child 拆除结局（干净自杀 vs 强杀）归父侧日志+落靶 detail，父退出码维持传输层
FIN/ABRUPT 不动 ③砖5 混沌三路（SIGKILL 在途/child 自毙 fatal/挂起超时），不够再扩。

**AI 攻击记录（四枚，照单收）**：
1. **S0/S1 无肉身，断路器分两行**——不变量：S2/S3 ⇒ 肉身在册（FulfillLaunch Done
   才落 S2/S3）；S0/S1 disconnect 维持纯转移（现状形状，无可拆）。
2. **自毙 ≡ SIGKILL（父侧不可分辨）**——reader EOF 报丧+decision 8 使两死法在
   父侧同为 Gone；DAP 级一章死亡网覆盖两死法（自毙机制级 childseam T 系已钉），
   注入器用 SIGKILL。
3. **SIGKILL 的孤儿是场景定义**——杀会话员则 inferior/lldb-server 必成孤
   （backend.close 永不运行）：混沌网探针自清孤儿，孤儿断言只在拆除章持有。
4. **行为兼容钉**——拆除后发对客户端帧形状零差（隐式链在已测路径孤儿干净）；
   砖4 RED 落核级（test_core 新查），C1/C2/C3 为收编钉，RED 预期诚实标注部分
   首跑即绿。FIN/RST/坏帧路维持隐式链（对齐参考的 FIN 同为进程退→子 EOF），
   显式化不外溢到客户端告别路。

**定形**：
- **核（adapter.py）**：`TearDownChild` 增 `req: ParsedRequest`（占位员落位定形）；
  step 分行：`(S2|S3, DISCONNECT)`→`StepResult(state=pstate, action=TearDownChild(req))`
  （frames=() 静默，双帧归 resolve）；`(S0|S1, DISCONNECT)`→现状纯转移；
  resolve 增 `(TearDownChild(req), _)` 行——**任何 outcome 同落靶**：DEAD+slot=None
  +双帧（OkReply payload={}+terminated{}；拆除尽力而为、会话照终=立场乙收场，
  码/话术不因拆除结局变）。
- **壳（schedule.py）**：`child_close` 返回归类 **"graceful"**（wait 破 2s 优雅窗）/
  **"killed"**（kill 发射）——退出码归类落点；run_effect 增 TearDownChild 臂：
  child=None=不变量洞大声失败（同 ForwardRequest 家法）、child_close 归类+returncode
  入日志（`child_teardown how=... rc=...`）、回 Done（how 当值）；handle 零改动
  （断路器形状红利：action 路 run_effect→resolve 既有）；stdio 路同 handle 天然覆盖。
- **核级网（test_core 增查，RED 落点）**：step (S3,disconnect)→action TearDownChild
  携 req、frames 空；step (S1,disconnect)→纯转移形状冻结（RED 期即绿）；resolve
  (TearDownChild, Done/Crashed) 同落靶（尽力而为语义钉）。
- **混沌网（probes/probe_chaos.py，端口 5697，真饵）**：
  C1 死亡在途：全链→SIGKILL child→stackTrace→2002 携 upstream_refused（快败不烧
  timeout）→threads→2002（父活陪到底）→disconnect→resp+terminated+FIN rc=0
  （对尸 child 拆除幂等）；孤儿=探针自清（chaos 定义）。
  C2 挂起：全链→SIGSTOP child→threads→2002 携 upstream_timeout（烧 timeout=10s，
  网预算注明）→SIGKILL 收场→disconnect 链同 C1。
  C3 拆除判别（砖4）：全链+setBreakpoints→continue→bp 停（inferior 活着）→
  disconnect→resp+terminated→**孤儿断言持有**（get_sign/lldb-server 零残留=显式
  拆除确定性兑现）+FIN rc=0。
- **小步序列**：①核+壳（test_core RED→绿+全网回归=收场链行为兼容证据）
  ②probe_chaos C1/C2/C3 ③档案（design-notes/进度档协议线收口/ledger）+终版
  sweep+待提交。

**砖4+砖5 落地（2026-10-23，冷启动续冻结点；协议线收口）**：步1 核+壳转录零网炸一次过——
核（adapter.py）：TearDownChild 落位定形携 req；step 断路器分行（`(S2|S3,disconnect)` 携动作
自回环驻原态 frames 静默，双帧归 resolve=拆除后发；`(S0|S1)` 纯转移现状冻结，DEAD 落同行
形状不变）；resolve 增 `(TearDownChild(req), _)` 单行——任何 outcome 同落靶 DEAD+slot 清+
双帧（码/话术不问拆除结局）。壳（schedule.py）：child_close 返回归类 "graceful"|"killed"；
run_effect 增 TearDownChild 臂（child=None 大声失败同 ForwardRequest 家法；child_close 归类
+rc 入日志 `child_teardown how=... rc=...`；回 Done({"how"})；conn.child 拆后清）；handle 零
改动兑现（断路器形状红利：action 路 run_effect→resolve 既有）。**撞到即裁一笔**：conn.child
拆后清（契约未列）——裁"肉身离册守 child 非空⇒在册不变量"，二次 disconnect 在 DEAD 终态
不可达故无幂等冲突（幂等归 child_close 不变量③）。**码勘误一枚自收**（照 breakpoints 家法）：
契约散文 C2"2002 携 upstream_timeout"笔误，UPSTREAM_TIMEOUT=2001，网按枚举真值钉。
**网况**：test_core RED 3 FAIL→16/16 ALL PASS（零网炸，转录一次过）；probe_chaos 26/26
首跑即绿（收编钉如契约诚实标注）——C1 快败实测 0.0s（Gone→2002，reader EOF 置 dead 后
child_request 快败）、C2 烧窗实测 10.0s（TimedOut→2001，双侧钉：≥9.5s+码 2001）、C3 孤儿
断言持有（bp 停 inferior 在场→disconnect→get_sign/lldb-server 零残留）+`child_teardown how=`
日志判别显式拆除臂实跑（SBDebugger.Destroy 对活 inferior 处置的真线兑现）；对尸 child 拆除
幂等=C1/C2 的 disconnect 链 FIN rc=0；17 网 rc=0×17+pytest 20/20 终版 sweep。**环境一笔**：
真靶子易失已按 conftest 配方重建（基线 0/1/255 钉）；pytest 首跑瞬时 14 errors 复跑即绿
（与假绿盯梢钉同类未坐实，记录在案）。**孤儿纪律兑现**：C1/C2 探针自清（pkill）不持断言、
C3 持有——SIGKILL 会话员则 inferior/lldb-server 必成孤是场景定义，网不为脏环境误报。
**协议线收场完毕**：砖4+砖5 关账，DAP 生命周期全程（launch→调试→收场→混沌）真线可观测。

## stdio/VS Code 接线（2026-10-23，lldbDir 参数化——decision 14 保持，E4 argv 裁决修订）

**先验（用户三裁，2026-10-23）**：①方向=stdio/VS Code 补强先于 M3（全链路真客户端段收口，
分支 stdio-vscode）；②注入路径=**A 案**——lldbDir 经 DAP launch 请求参数携带，argv
`--lldb-dir` 降为可选（boot 值作缺省）；扩展保持零码（decision 14 不动），修订的是 E4
"argv required" 的字面——零 PATH/env 猜测原则不破（两处皆缺=launch 期干净错）；
③冲突本质=boot 索取时机 vs F5 直启 argv 恒空（官方文档实证：声明式 adapter 无 argv 通道，
launch 属性只经 DAP launch 请求到达；argv 级定制唯一正路=DescriptorFactory 扩展代码）。

**AI 攻击记录（四枚，自收落约）**：
1. **码语义**：缺失双处（boot 无 argv+launch 无 lldbDir）在壳侧拦截，Outcome 不为此加员
   ——照 **ktest refused 家法**（run_effect 返回 Rejected→2002 "session refused launch"
   +detail 说真相）；lldbDir 形状坏（present⇒非空 str）归核 validate_launch（1001，
   同 ktest/target 家族）。
2. **dap_adapter 缺 --stdio**：注释意图 stdio、代码未传——VS Code spawn 形态必须 stdio，
   照修（`--stdio` 硬编码进启动器；TCP/debugServer 用户直接起 schedule.py，不经此脚本）。
3. **package.json required-ness**：lldbDir 声明 optional（schema required 会误伤
   debugServer 用户——那条路 lldb-dir 由人手动传，运行时壳侧强制兜底）。
4. **boot 缺省与 launch 覆盖的优先级**：`launch args lldbDir or conn.lldb_dir`（launch
   优先）；17 存量网全走 argv 路不携带 launch lldbDir=兼容回归证据。

**定形**：
- **核（adapter.py）**：validate_launch 增 lldbDir 形状行——present 且非（非空 str）→
  "launch 'lldbDir' must be a non-empty path string"（1001）；缺席不拦（boot 缺省可补）。
- **壳（schedule.py）**：argparse --lldb-dir 降 optional（default ""）；run_effect
  FulfillLaunch 臂解析 `lldb_dir = req.arguments.get("lldbDir") or conn.lldb_dir`，
  两处皆缺 → Rejected(detail="no lldb_dir: set 'lldbDir' in launch arguments or pass
  --lldb-dir at boot")；spawn_child 用解析值。ConnState.lldb_dir 语义=boot 缺省。
- **vscode 面**：package.json launch 属性增 lldbDir（optional，string）；dap_adapter
  改 `exec ... schedule.py --stdio "$@"`。
- **网（probes/probe_lldbdir.py，端口 5699，真饵）**：S1 TCP boot 无 dir→alive→launch
  携 lldbDir→全链→threads→disconnect exit0；S2 stdio boot 无 dir→alive→initialize
  caps→stdin EOF exit0；S3 单会话四连发（无 lldbDir→2002 携 no lldb_dir / lldbDir=""
  →1001 / lldbDir=不存在→2002 携 unavailable / lldbDir=真 dir→全链 exit0）。
- **小步序列**：①核+壳转录（probe_lldbdir RED→绿+17 网回归=argv 兼容证据）②vscode 面
  （声明/脚本，描述性改动）③真客户端联调（人在环：F5 直启+debugServer 两形态；
  **terminate 广告钉**撞到即裁）④档案（design-notes/进度档/dap.md en+zh/roadmap en+zh
  同步+AGENTS.md 网数陈旧修正）+sweep+待提交。

**RED 基线（2026-10-23）**：probe_lldbdir 11 FAIL/0 PASS 全红——三场景 boot 即死
（argparse exit 2），连带全红（模块缺位形）。勘误一枚自收：契约初稿预测"12/12"，
S3 末两查落笔时合并，实为 11——预测数与查数对齐归落笔核，非网松。

### 真客户端联调撞钉（2026-10-23，trace 实证两轮）

**第一层：S2 配置期断点被状态门拒**。VS Code 标准时序=setBreakpoints 在 configDone
之前（initialized 后），转移表 S2 无调试命令行→兜底状态门 1003 →断点从未注册。
**用户裁（结构性修法）**：调试器层/适配器层劈分——调试器层命令=自回环转发子进程，
永不触发适配器层状态迁移；状态迁移权只在适配器层命令（initialize/launch/configDone/
disconnect）。落地=整层路由行 (S3,_) 加宽 (S2|S3,_)（resolve 的 ForwardRequest 落靶
按命令分业务，无相位假设，零改动兑现）。

**第二层：setBreakpoints 线形状两钉**（trace 实证：Remote-SSH 客户端 source.path=
`vscode-remote://ssh-remote%2B<host>/home/...` URI 形状）：URI 原样进 LLDB→
BreakpointCreateByLocation 解析不到→pending（零 location）→IsValid() True→verified
谎报 True→VS Code 实心红点永不命中，continue 直通 exit。
- **钉 A（用户裁）**：父面 DEBUG_SPECS params_fn 做 URI 归一——带 scheme 取 URL
  path 分量（vscode-remote://authority/home/... → /home/...；file:// 同款），纯路径
  原样过。客户端线形状归父面，child 不知 VS Code URI。
- **钉 B（用户裁）**：backend verified=IsValid() 且 GetNumLocations()>0——pending
  如实报 False（未验证=空心点，说真话）；断点 id 照记册（全量替换清理不受影响）。
- RED（probe_breakpoints 新场景 realclient_shapes）：V5 URI 路径→continue 停 L20 红、
  V6 不存在路径→verified False 红；修复后两钉齐绿+全量回归。

### 真客户端第三层钉（2026-10-23，用户两裁）

**会话侧性（extensionKind）**：trace 实证 VS Code 把会话跑在 UI 侧——断点以
`vscode-remote://` URI 发来（workspace 侧会话发普通路径）+ 栈 path 远端路径 UI 打不开
显示"未知源"（双向资源映射全拧；连接通是 VS Code 自动端口转发兜住）。步2 的
`["ui","workspace"]` ui 优先是主嫌疑。**用户裁改 `["workspace"]`**（会话归远端，
VS Code 内建路径双向转换生效）。钉 A 的 URI 归一留任=防御纵深（file:// 同款）。

**terminate 广告钉兑现**：UI 停止按钮首发 terminate（capability 广告未实现）→
not implemented 报错；二次 VS Code 降级 disconnect → 正确收场。**用户裁实现
terminate**：收场语义照 disconnect 两门同形（(S2|S3)=TearDownChild 拆除臂，
(_,)=断路器纯转移 DEAD+resp+terminated）；response 无 body；子面零改动（terminate
全程父面核处理，不达 child）。RED=probe_terminate（5701）T1 S0 断路器/T2 S3 带
inferior 在场拆除。

### 第四层钉：stackFrame.source 包裹（2026-10-23，真客户端暴露形状缺位）

会话侧归远端后源码仍"未知源"：child 帧 dict 原样透传 stackFrames——`path` 裸挂
帧级；**DAP StackFrame 规范要求 `source: {name, path}` 包裹**，VS Code 只认
`frame.source.path`，帧级裸 path 被无视。**用户裁钉 C（线格式映射归父面）**：
STACK_TRACE body_fn 补映射（SCOPES 同位先例）——path 非空包 `source{name=basename,
path}`，空路径（_start 入口帧）省略 source。**网钉勘误**：probe_breakpoints 三查+
probe_stacktrace 形状查当时与实现同错（帧级 path），最小客户端跟错形状跑绿——形状
钉两网同错=盲区实证，真客户端是第四张网（协议标准形状网）。
