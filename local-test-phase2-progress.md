# local-test-phase2 推进状态（阶段二开工 2026-10-13：M2 DAP 链路；§3/§4/§5 完成，§6 改为动手验收；两线合流为四阶段。**2026-10-14 续：阶段 1 v6 改道为代码骨架直接交付（用户写 AI 攻协议不变），①主循环+③收场矩阵+④退出码表已关账（五探针全过，RST→exit2 落地）；余② slot，明日续**）

> 本文件是**会话进度快照**，供在新环境（VM opencode）恢复阶段二学习流程。
> 配套：[`local-test-phase2.md`](local-test-phase2.md)（任务书）、`../research-survey/plan/ai-independence.md`（承重协议）、`../research-survey/plan/ai-ledger.md`（对账账本，截至 2026-10-13 共 8 条：phase1 六条 + phase2 §4 对账 + §5 归因更正）。
> **承重协议必须维持：预测先行，AI 不得代写先验。**（P1–P3 已于 2026-10-13 锁定并对账，"先验前不读源码"约束已解除；动手验收的新先验规则：语义/设计先验先写，再读对应源码——见待办阶段 1/3）

## 环境（✅ 2026-10-13 已确认就绪，机械件）

- Python 3.13.5（`~/venvs/symrepl`）；symrepl 0.2.0 editable 安装正常，`symrepl --help` 可用
- 阶段二无需 LLDB / clang / Docker（纯协议层黑盒，不涉及目标程序）

## §1 先验（待用户本人填写，每条一句话；AI 不得代答）

| # | 问题 | 先验（自己填） |
|---|------|---------------|
| P1 | `src/symrepl/dap/` 四个模块 framing / session / service / server：一条消息进来后经过什么顺序？职责边界怎么切？ | 自顶向下，我觉得应该是 framing-session-service-server，framing 主管消息处理流的调度，也就是 dap 协议解析，然后启用 lldb-dap 处理，然后使用 lldb-server 进行处理。我觉得应该调度层（主要管理各模块协同和先后调度处理）、顶层协议处理层（处理 dap 协议的承载协议的处理，比如 dap 用 tcp 协议、hppts 协议、wss 协议来运输，那么需要先解析这些协议）、dap 协议处理（主要调用 lldb-dap 来处理 dap 协议，不是不能开发而是工程上尽量考虑经过完备测试稳定的基础设施）、调试器层（lldb 或者 gdb，主要用于实际断点调试）、远程调试层（lldb-server 或者 gdb-server，主要用于远程调试）。 |
| P2 | `initialize` 之前 / 之后，server 各允许、拒绝什么？先发 `launch` 再发 `initialize`，协议要求什么？你猜这份实现实际会回什么？ | initialize 之前允许设置变量值。之后不允许修改断点，不然会干扰实际路径回放，因为断点是打在条件分支上用来作路径回放的。 |
| P3 | `disconnect` 请求 / 对端直接断连 / `shutdown`：进程分别怎么收场？退出码各是什么？ | 对于可以收到进程事件的，可以通过父进程的事件来处理。对于对端断链接等通过超时机制来处理，超时杀死不能阻塞。对于别的异常事件 try...catch |

- [x] 入口观察点自确认（2026-10-13）：`symrepl dap --help` → 默认 stdio，`--port N` 走 127.0.0.1:N；usage 自述 "one client, one service child per session"（ADR-0003）

## 待办（2026-10-13 两线合流：深读/实现线（并行会话起草）+ clean-room 线（会话内选定）。**顺序不可换**——阶段 1 必须在参考实现解封前完成，否则重写退化为抄写）

- [~] **阶段 1｜§7 Clean-room 重写**：设计稿 v5 已交（2026-10-13：A 轮询/事件驱动两案✓待 commit；B 心跳/超时回收**打回**——①53ms 实测=recv 被内核叫醒非心跳 ②断点停留 5 分钟合法沉默会被超时误杀=设计 bug，decision 9 明文交互会话无全局超时 ③心跳只适用于死讯传不回的场景 ④"守护进程不需检测"的区分是真洞察，参考实现用结构表达（单 accept、会话结束即退）；C 偏函数=扣响应机制✓，slot 提示已给（pending_launch dict slot：放=launch / 取=configurationDone / 非空=拒双发 / 清=收尾清理题））。**AI 攻击第五轮已发**（定稿标准四项：主循环伪代码≤10行、launch/configurationDone 伪代码各≤5行、收场矩阵=伪代码每条 return 路径、退出码表）→ **⏸ 冻结点（2026-10-13）**：v6 待交四项 = ①主循环伪代码≤10行（并 commit 轮询 vs 事件驱动选型）②launch/configurationDone 伪代码各≤5行（pending_launch slot 的放/取/拒/清四操作）③收场矩阵（伪代码每条 return 路径=一行；含 slot 清理题答案）④退出码表。已定共识：自研适配器（decision 16 已推导关账）、Functional Core/Imperative Shell 方向、偏函数=扣响应机制、客户端死亡检测靠连接事件非心跳（勿回退到已打回的超时方案）。**恢复方法**：新会话先读本文件待办节 + 向用户索要 v6；封印与"用户写 AI 攻"协议继续有效。v6 定稿后：实现（`~/github/test_code/`，范式自定）→ 八条黑盒验收 → diff 对照参考实现 → 账本一行。**封印 `src/symrepl/dap/` 不读**（docs/ADR 已解封）；协议：用户写 AI 攻，AI 不代写
  - **▶ 进展（2026-10-14）**：v6 四项经用户决定**改道**——介质从纸面伪代码升级为真代码骨架（`~/github/test_code/server/` schedule.py+error.py），"用户写 AI 攻"协议不变。**①主循环**：事件驱动(asyncio)选型已 commit；单会话拓扑自建=active_writer 单槽+client_done 单事件（独立收敛到参考实现"单 accept、会话结束即退"结构）；signal handler 全部收场路收拢到 client_done。**③收场矩阵六行全探针实测**：拒连（RST 副作用，进程不受影响）/会话结束 exit0/RST exit**2**/SIGTERM 带活会话掐客 exit0/空闲 exit0/未捕获炸穿=implicit 行 exit1+traceback（不建代码）。**④退出码**：两层设计——细粒度 reason 只进日志（eof/reset/epipe/slot_busy，全走 stderr 因 stdout 将来说协议），TransExitCode 粗类 FIN=0/ABRUPT=2（8 位约束：sys.exit(9002) 实测=42 掩码；daemon policy=SIGTERM 优雅退 0）→ State.exit_code 默认 FIN 仅 ABRUPT 改写 → main 唯一 sys.exit。error.py 两类型：InternalErrCode（协议层 1xxx/上游 2xxx，会话不死）与传输终态分轴；ProtoError=FC 错误返回载体（NamedTuple，暂未接线）。**转录件复述 1/3**（InternalErrCode 二轴说塌已纠；账本 2026-10-14 两行）。**余：② launch/configurationDone + pending_launch slot 放/取/拒/清——纯承重，草稿必须用户写**。注：test_code 尚非 git 仓库
  - **▶ 进展（2026-10-14 续）**：**②完成（转录模式）**——用户疲劳，S0/S1/S2 状态机经逐状态攻防定稿后由 AI 转录为转移表+伪代码（`test_code/server/design-notes.md`），复述判据**待过**（用户需一口气讲出：事件≠状态、slot 即 S2、断路器、两层错误、偏离清单）。状态机定稿要点：事件=转移条件非状态；S0 initialize 无守卫（adapterId 守卫裁掉：实证不敏感+自家客户端即用 adapterId）；断路器 disconnect（逃生门不上锁）；S1 launch 过参数校验→放 slot 扣响应；initialize 重入拒（决策=纯洁性，偏离 §4-2 已记录）。**帧序实验（先验→实测，账本 2026-10-14 行）**：先验"configDone 响应先"题面作废——参考扣响应比共识深，四跑均不见 launch/configDone 响应（OQ-1）。**新增开放问题 OQ-1~4 + 黑盒锚点已入 design-notes.md**（子会话 phase 机 'dead'/'stopped'/'entry_stopped'、configDone=spawn、branch trace 走 output 事件、launch 必需 ktest+target 双参数、ktest 现货+target 构建配方）。**下一步**：用户复述过判据 → ②正式关账 → 阶段 1 完成 → 解封进阶段 2（OQ-1~4 即深读路线图）
- [ ] **阶段 2｜深读一：断点调试式深读 server.py**（此时解封）：关键路径下断点，用户客户端驱动，预测先行（每断点先写"预期看到什么"）——带着自己刚写的实现对照两种写法
- [ ] **阶段 3｜深读二：实现 DAP `shutdown` 请求**（真代码库；实验⑤发现的缺口，分发表无此 key）：①动手前先写语义先验（回什么？进程何时退？退出码？未 initialize 时收到怎么办？）②读 server.py 四段：分发表 :105-122 / 事件循环 _closing 路径 / 内部 shutdown 收尾 :722 / 退出码流 cli.py:151-156 ③实现 ④自己 dap_client 的 testShutdownAfterDisconn 实测验收 ⑤pytest 全量回归（187+新增）⑥账本记实现型对账一行
- [ ] **阶段 4｜深读三（可削减项）：framing.py 函数式重构**：独立分支上进行，187 测试保绿，补 ADR 备注
- 红线（贯穿全程）：evaluate/completions 是 ADR 明确推迟项，不顺手实现；capability 冻结不受影响（shutdown 无需 capability 声明，但阶段 3 要想清楚为什么）；任务 B 混沌调试（半帧/坏 Content-Length/未读完就关，先写预测再实测，对账 framing.py 的 no-resync 决策 decision 2）为选做插入项，任意阶段可做
- 补充锚点（阶段 2/3 用）：launch 仅防重入+参数校验 :363-446；disconnect body={} 硬编码 :719；客户端泵阻塞读 :279-290；退出阶梯 :893-923；单连接拓扑 :982-983；子进程 stdin-EOF 自杀 service.py:38-39；子会话状态机 session.py:24-27

- [x] §3 手写最小客户端（2026-10-13 完成，`~/github/test_code/dap_client.py`，用户手写；socket 模式 `--port 5678`）
  - 实跑通过：initialize 往返 `success: True`（body: supportsConfigurationDoneRequest / supportsTerminateRequest），客户端 exit=0
  - **附带观察**：客户端不发 disconnect 直接退出 → server 随之退出，退出码 0（正式实验留 §4-4）
  - 与判据偏差（用户自定是否修）：①打印的是解析后 dict，未按判据先打印原始字节；②发的是 `adapterId`（任务书为 `adapterID`），实测 server 不敏感
- [x] §4 黑盒实验（2026-10-13 完成，三轮迭代；客户端用户手写并逐轮修正，驱动 /tmp/opencode/run_dap_tests2.py，每测试全新 server+连接）
  1. launch **先于** initialize → `success: False`，`"launch requires a 'ktest' path string"`——参数校验错，无状态门｜exit=0
  2. initialize ×2 → 第二次照常 `success: True`（同 body）｜exit=0
  3. disconnect 请求 → `success: True, body: {}`，之后 2s 无任何后续帧｜exit=0
  4. kill -9 客户端 → server ≈53ms 察觉并退出，exit=0，无日志
  5. shutdown/disconnect 顺序对调 → **shutdown 未实现**（`"not implemented … ADR-0003 decision 4"`），两种顺序无差别；disconnect 任意位置均 `success: True`｜exit=0
  - 计划外观察：**未知命令**（拼写 `shudown`）→ server **静默不回包**（阻塞 recv 即卡死来源）；**已知未实现命令** → 回规范错误。差异原因入 §5 疑问清单
  - 差分存档：客户端带未读数据 close → RST → server exit=**2** + `malformed client frame`（唯一非 0 收场）；客户端侧机制已定位（close 时内核缓冲有未读数据则发 RST），server 侧解释留 §5
  - **收尾 ✅（2026-10-13 用户标注）**：P1 未覆盖（黑盒测不到内部结构）；P2 **偏差**（"之前只许设变量"——实测 launch 先于 init 被受理至参数校验层）；P3 **偏差**（"靠超时发现断连"——实测 ≈53ms，用户判断是信号不是超时）。两条偏差均能自行解释，已录入账本 2026-10-13 行
- [x] §5 对账（2026-10-13 完成：源码映射全部落点 + **完整会话实录**（/tmp/opencode/demo_session2.py，真 ktest 真目标端到端跑通 initialize→launch→configurationDone→entry 停→threads/stack/scopes/variables→continue→branchTrace→disconnect）；演示驱动 v1 现场踩响两个机制（扣住的 launch 响应、stale_reference 世代票据），教学效果最佳；目标二进制已按 conftest.py:23-61 配方重建于 /tmp/opencode/replay-build/get_sign）
- [~] §6 验收（2026-10-13 应用户要求由"闭卷复述"改为**动手验收**；细则已并入上方四阶段——复述职能由阶段 1 设计稿承担，shutdown 实现即阶段 3，混沌调试即任务 B 选做插入项）
  - （旧任务 A/B 明细与锚点已并入待办四阶段，2026-10-13 合流清理）

## 已完成

| 步骤 | 状态 | 结果要点 |
|---|---|---|
| §0 环境确认 | ✅（2026-10-13，机械件） | venv/安装/CLI 就绪，工作树干净（c30b7f2） |

## 通过标准（留档）

P1–P3 中 ≥2 条命中或偏差能自行解释；全对 ≈ 答案泄漏嫌疑，全错 ≈ 回炉重讲（40–80% 理想）。
