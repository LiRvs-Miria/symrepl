# cleanroom 转正推进状态（立项 2026-10-16：local-cleanroom 替代 src/symrepl/dap/）

> 本文件是会话进度快照，冷启动从此取活。
> 配套：[`local-cleanroom/server/design-notes.md`](local-cleanroom/server/design-notes.md)（设计档案，随码走，含契约/裁决/命名/通道选型全记录）、[`../research-survey/plan/ai-independence.md`](../research-survey/plan/ai-independence.md)（承重协议 v1.3，§2.6 决策节律）、[`../research-survey/plan/ai-ledger.md`](../research-survey/plan/ai-ledger.md)（对账账本，追加式）。
> 前史：phase1（2026-10-12 关账）/ phase2（2026-10-15 收官）的 local-test* 档案已删除（2026-10-16），内容沉淀已蒸馏完毕（设计→design-notes、对账/格式知识→账本、开放问题→design-notes 注记），考古走 git 历史。

## 总纲（已定裁决，勿重议）

- **目标**：local-cleanroom 整体替代 src/symrepl/dap/ 四件（server/framing/service/session）。旧 server.py 已退役（main 分支存档）；M1 replay 包**永驻**（CLI 产品路径不动），但 cleanroom **零代码继承**——M1 引擎=行为 oracle，非代码来源
- **节奏**：**交替制**（2026-10-16 用户裁）——协议砖（轻：接线/事件/收场）与引擎件（重：回放核心重写）轮流推进，节奏不断
- **单条循环**：用户先验（业务事实/线上形状）→ AI 攻 → AI 转录客户端断言+RED 探针 → 实现（承重件用户写/结构件承包 AI 转录，逐件裁）→ 探针绿+旧网无回归 → 账本/复述
- **决策节律**（协议 §2.6）：先验收模糊架构；细节**撞到即裁、一次一钉、代码在前决策在后**；批量问卷=反模式
- **铸名（用户亲铸）**：statemachine→**adapter**（协议面）；桩子进程→**session**（进程即会话）；childproto=父子拓扑的协议词汇表（边界层，不识命令语义）
- **通道**：匿名管道（六路对比 review 维持；EOF=死亡通告地基；解耦四维称重结论入 design-notes）；桩案：不依赖原包，桩形状钉真协议，桩对真分歧由形状断言防
- **引擎规则（翻案定稿）**：cleanroom **原生重写**回放核心（ktest 解析/预扫描断点/走向推断/trace/LLDB 会话原语），多范式+更多 FP+错误前置比旧代码更严格；知识传承走规格（phase1 格式知识+decision 语义+坑清单），行数不入境
- **领域分层锚**：父=协议面（会话状态机），子=执行面（调试操作+事件源）；launch 双面（父验单+slot，子执行）；调试命令父只做薄代理

## 已关账

| 砖 | 内容 | 状态 |
|---|---|---|
| 砖1 ping | childproto 词汇表（VERSION/三 ADT/request_msg/parse_child_msg）+ spawn_child + child_request 机制（_Channel 注册表/reader task 帧分流/Future 表/单写者锁/ChildGoneError+TimeoutError 两路/child_close 优雅窗+强杀+幂等）+ 桩 session.py（探活/父死自杀/坏帧 no-resync） | ✅ 2026-10-16（fd937a7）；probe_childseam 8 项全绿（T4 修假在途改哑 child、T7 增补发前已死快败）+转移表 14+framing 无回归；shutdown 退役连带完成；网炸两转录错（poll≠asyncio API、T4 假在途） |

## 待办（交替队列）

### 协议线
- [ ] **砖2 launch 接线**（**接线完成 2026-10-19：四网全绿，已提交 af38477，待验收关账**）。**已裁**：①launch 履约成功即广播 process+initialized 两事件（caps 只在 initialize 应答）②立场乙：child 死/无应答=父活陪到底快败（失败=ErrReply+留 S1 可重递；退场门收缩为客户端侧 FIN/RST/坏帧）③履约两步=先行诊断（败不惊动会话员）+spawn 握手（败=上游类 ErrReply）④桩 pid=自身真 pid。**落地=代数 v5（action+choice，用户裁根解，两案作废史见 design-notes）**：step 好单自回环驻 S1+携 `FulfillLaunch(req)`（"slot 非空=S2"不变量原样保留），壳 `run_effect` 执行（`except Exception` 全捕获无条件收尸+`_physical` 单点分类学末臂默认收编），核 `resolve` 落靶（两事件帧序/pid 形状闸/上游码归核，壳无语义）；Action 三员（占位 ForwardRequest/TearDownChild 防忘位）、Outcome 六员（含 Crashed）。**网况**：L1/L2 全绿；R6 网眼依法翻案（静默→两事件）；四网全绿；探针 odot 硬编码已相对化可跨机。**悬钉**：ktest 第一跳实现形状（E 线首撞）；砖3 才修 await 顶点+内部事件门（真异步源）
- [ ] 砖3 configurationDone 放行 + stopped 事件回流（**完成 2026-10-19：四网全绿，待验收关账**）——放行携 `ForwardRequest` 先递 `config_done` 后落双帧（败=双帧皆败留 S2 可重递，立场乙同类）；stopped 经 `translate_child_event` 只翻不判+壳 sink 回流领 seq（D3 兑现）；钉序=stopped 与双帧不钉相对序（宽容三帧网），事件不改协议状态（状态门留首个改状态砖再裁）；D3 留位注记退役
- [ ] 砖4 收场路（disconnect 带 child 拆除、退出码归类）
- [ ] 砖5 child 死路混沌探针（**立场乙**：父活快败陪到底，对齐参考 decision 8——原"退场哲学兑现"表述作废）
- [ ] 横向 11+1 条（每条=完整循环，先验=用户出题表；**方向裁决 2026-10-19：砖4 收场路押后，横向先行**）：~~threads（最简首发）~~✅ 2026-10-19 五网全绿（落靶按 DAP 命令分业务；网炸败臂混用详见 design-notes；壳零改动=横向复利首证）→ stackTrace（守卫入门）→ scopes/variables（世代票据首撞）→ continue（运行态+事件流）→ setBreakpoints → next/stepIn/stepOut/pause → terminate → replay.branchTrace（引擎集大成，横向收尾）
- [ ] 收尾清单：stdio 模式、cli.py:151 接线改挂、pytest DAP 面 41 项改挂、ADR 注（shutdown 退役注+新架构）、原包 dap 四件退役、5c 真逻辑填实

### 引擎线（每件配 oracle 对账网：M1 同 ktest/target 输出 diff）
- [ ] E1 ktest 解析重写（纯件：真 ktest 文件喂，对账 M1 parse；值 ADT/错误前置风格首发——格式知识=phase1 账本逐字节）
- [ ] E2 BranchSite/预扫描语义 + 走向推断（纯件：合成 PC 序列网；语义源=branches.py is_conditional/infer_outcomes，代码不入境）
- [ ] ~~E3 LLDB 会话原语~~✅ 2026-10-19（lldb_session.py：launch 入口停/resume/事件排空/单进程单 debugger 守门；新坑三枚+lldb-server 定位入规格；probe_e3 三 ktest oracle diff=0；靶子按 conftest 配方即建 /tmp/opencode/replay-build/get_sign）
- [ ] E4 会话编排（一次性循环→请求驱动；trace 按需报告，exit 后经 output 事件推——OQ-2 锚点 :803-810）

## 红线

- evaluate/completions 是 ADR 明确推迟项，不顺手实现
- 承重协议：先验用户写，AI 不代答；对账后账本追加（不改旧行）
- 引擎重写**零代码继承**：不因图省事 import 原 replay；规格继承+行为 oracle 是唯一合法传承
- capability 冻结语义照旧（可选才广告）；版本校验行为、请求级失败线形状两钉未裁

## 环境

- venv `~/venvs/symrepl`（M1 CLI 与 pytest 用）；协议探针自砖1 起全封闭（`sys.executable`+本地桩，零 venv/lldb 依赖）
- 真靶子 fixture：`~/replay-build/get_sign` + `third_party/klee/ktest/get_sign_test000003.ktest`
