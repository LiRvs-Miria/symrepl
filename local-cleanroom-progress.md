# cleanroom 转正推进状态（立项 2026-10-16：local-cleanroom 替代 src/symrepl/dap/）

> 本文件是会话进度快照，冷启动从此取活。
> 配套：[`local-cleanroom/server/design-notes.md`](local-cleanroom/server/design-notes.md)（设计档案，随码走，含契约/裁决/命名/通道选型全记录）、[`../research-survey/plan/ai-independence.md`](../research-survey/plan/ai-independence.md)（承重协议 v1.3，§2.6 决策节律）、[`../research-survey/plan/ai-ledger.md`](../research-survey/plan/ai-ledger.md)（对账账本，追加式）。
> 前史：`local-test-phase1-progress.md`（2026-10-12 关账）/ `local-test-phase2-progress.md`（2026-10-15 收官）**已完成使命，只读存档不再更新**。

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
- [ ] **砖2 launch 接线**（当前口令）：spawn 接线到 launch 门 + 专属状态成型（D2 纯核×I/O 接缝撞真章）。业务事实=派工链路：验单（ktest 可解析/target 存在，废单不惊动会话员）→叫人→交单→接单确认即广播 **process+initialized**（对齐参考 server.py:390-399）→正式答复仍扣到 configurationDone。悬钉（撞到即裁）：①专属状态名/形状，与"slot 即 S2"不变量关系 ②筹备期失败→第三扇门帧序（ErrReply 后 terminated 与否）③process 事件桩 pid 给不给 ④ktest 第一跳（参数真实流到桩子）
- [ ] 砖3 configurationDone 放行 + stopped 事件回流（D3：无请求事件进 seq 账本）
- [ ] 砖4 收场路（disconnect 带 child 拆除、退出码归类）
- [ ] 砖5 child 死路混沌探针（退场哲学兑现）
- [ ] 横向 11+1 条（每条=完整循环，先验=用户出题表）：threads（最简首发）→ stackTrace（守卫入门）→ scopes/variables（世代票据首撞）→ continue（运行态+事件流）→ setBreakpoints → next/stepIn/stepOut/pause → terminate → replay.branchTrace（引擎集大成，横向收尾）
- [ ] 收尾清单：stdio 模式、cli.py:151 接线改挂、pytest DAP 面 41 项改挂、ADR 注（shutdown 退役注+新架构）、原包 dap 四件退役、5c 真逻辑填实

### 引擎线（每件配 oracle 对账网：M1 同 ktest/target 输出 diff）
- [ ] E1 ktest 解析重写（纯件：真 ktest 文件喂，对账 M1 parse；值 ADT/错误前置风格首发——格式知识=phase1 账本逐字节）
- [ ] E2 BranchSite/预扫描语义 + 走向推断（纯件：合成 PC 序列网；语义源=branches.py is_conditional/infer_outcomes，代码不入境）
- [ ] E3 LLDB 会话原语（launch/entry stop/resume/事件排空；真靶子=`~/replay-build/get_sign` + `third_party/klee/ktest/get_sign_test000003.ktest`，配方 tests/conftest.py；坑清单入规格：read1/复用 no-op/事件排空规则/owner-thread）
- [ ] E4 会话编排（一次性循环→请求驱动；trace 按需报告，exit 后经 output 事件推——OQ-2 锚点 :803-810）

## 红线

- evaluate/completions 是 ADR 明确推迟项，不顺手实现
- 承重协议：先验用户写，AI 不代答；对账后账本追加（不改旧行）
- 引擎重写**零代码继承**：不因图省事 import 原 replay；规格继承+行为 oracle 是唯一合法传承
- capability 冻结语义照旧（可选才广告）；版本校验行为、请求级失败线形状两钉未裁

## 环境

- venv `~/venvs/symrepl`（M1 CLI 与 pytest 用）；协议探针自砖1 起全封闭（`sys.executable`+本地桩，零 venv/lldb 依赖）
- 真靶子 fixture：`~/replay-build/get_sign` + `third_party/klee/ktest/get_sign_test000003.ktest`
