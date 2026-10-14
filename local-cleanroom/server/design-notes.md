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

## 已记录的对参考实现偏离（决策清单）

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
- slot 不可变延伸：`PendingLaunch(NamedTuple)`，dict 直接入 slot 会别名漏水（浅拷贝反例已演示）
- ProtoError.code 默认 OK：用户暂用默认搭框架；保守意见=去默认逼显式（静默成功是地雷），未裁决

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
