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
