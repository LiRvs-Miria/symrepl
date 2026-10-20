# cleanroom 转正推进状态（立项 2026-10-16：local-cleanroom 替代 src/symrepl/dap/）

> 本文件是会话进度快照（**冻结 2026-10-19**），冷启动从此取活。
> 配套：[`local-cleanroom/server/design-notes.md`](local-cleanroom/server/design-notes.md)（设计档案，随码走，含契约/裁决/命名/通道选型/网炸史全记录）、[`../research-survey/plan/ai-independence.md`](../research-survey/plan/ai-independence.md)（承重协议 v1.3，§2.6 决策节律）、[`../research-survey/plan/ai-ledger.md`](../research-survey/plan/ai-ledger.md)（对账账本，追加式）。
> 前史：phase1（2026-10-12 关账）/ phase2（2026-10-15 收官）的 local-test* 档案已删除（2026-10-16），考古走 git 历史。

## 总纲（已定裁决，勿重议）

- **目标**：local-cleanroom 整体替代 src/symrepl/dap/ 四件（server/framing/service/session）。旧 server.py 已退役（main 分支存档）；M1 replay 包**永驻**（CLI 产品路径不动），但 cleanroom **零代码继承**——M1 引擎=行为 oracle，非代码来源
- **节奏**：**交替制**（2026-10-16 用户裁）——协议砖（轻：接线/事件/收场）与引擎件（重：回放核心重写）轮流推进，节奏不断；**2026-10-19 方向裁决：砖4 收场路押后，横向调试命令与 E 线先行**（用户：每层都有命令利于架构/方法论抽象）
- **单条循环**：先验/设计 → RED 探针 → 小步转录（每步用户 review）→ 探针绿+旧网无回归 → 档案 → 提交
- **决策节律**（协议 §2.6）：先验收模糊架构；细节**撞到即裁、一次一钉、代码在前决策在后**；批量问卷=反模式
- **建模裁决（2026-10-19，代数 v5）**：转移代数=**action+choice**（五件补全）——step 吐"自回环+携动作"，壳执行动作，核 `resolve` 按结果落靶；机器只停休息态，中途不设顶点；await 顶点+内部事件门=真异步源才修（砖3 stopped 未改状态故未用）
- **码注纪律（用户裁）**：注释只说代码本身；裁决史/形式化出处/作废史一律归 design-notes
- **纯/非纯劈分（用户裁，E3 起）**：判定下沉纯函数（plain 值进出、结果/错误当值返回、家法代数），SB/管道调用只住非纯壳；异常面=`except Exception` 全捕获+`_physical(e, fallback)` 单点分类+末臂默认收编
- **领域分层锚**：父=协议面（会话状态机），子=执行面（调试操作+事件源）；调试命令父只做薄代理（ForwardRequest：cmd/params 核定名，壳盲发）

## 已关账

| 砖 | 内容 | 状态 |
|---|---|---|
| 砖1 ping | childproto 词汇表+spawn_child+child_request 机制（注册表/reader 分流/Future 表/单写者/close 幂等）+桩 session.py（探活/父死自杀/坏帧 no-resync） | ✅ 2026-10-16（fd937a7）；childseam 8 项全绿 |
| 砖2 launch 接线 | 代数 v5：step 好单自回环驻 S1 携 `FulfillLaunch(req)`（"slot 非空=S2"不变量保留）；壳 run_effect（全捕获收尸）；核 resolve 落靶（process+initialized 两事件/pid 形状闸/上游码归核）；Action 占位制+Outcome 六员（含 Crashed） | ✅ 2026-10-19（af38477+a1efcc1）；L1/L2 RED→全绿；R6 依法翻案（静默→两事件）；两案作废史（伪状态/旗子）入 design-notes |
| 砖3 放行+stopped 回流 | configDone 携 `ForwardRequest` 先递 `config_done` 后落双帧（败=双帧皆败留 S2 可重递）；`translate_child_event` 只翻不判+壳 sink 回流领 seq（D3 兑现） | ✅ 2026-10-19（2904c62+8a7d9de+9d9b57c）；L1/L2 全绿+R8 翻案宽容三帧；事件不改协议状态（状态门挂账） |
| 横向 threads | THREADS 成员+(S3,THREADS) 代理自回环；resolve 落靶**按 DAP 命令分业务**（放行双帧/代理单帧，body 映射杂键滤除） | ✅ 2026-10-19（dab49e1）；probe_threads RED(2)→全绿；壳零改动=横向复利首证；网炸败臂混用+RST 下游伤入 design-notes |
| E3 LLDB 会话原语 | lldb_session.py：纯核代数（`classify_event`→Drop/Accept 附因、`SessionState` ADT 未知携值、结果/错误当值返回）+SB 壳（launch 入口停 KTEST_FILE 最小环境/事件排空/单 debugger 构造期守门）；SessionErrCode 3xxx 入 error.py；env 出层=启动参数 debugserver_path+`find_lldb_server` 纯函数；`_physical(e, fallback)` 缺省版接四非纯面 | ✅ 2026-10-19（2f7780a+c517cec）；probe_e3 三 ktest oracle diff=0；新坑三枚+lldb-server 定位+盲区记档入 design-notes |
| 横向 stackTrace | 守卫入门+表驱动收编：DEBUG_SPECS 规格表+step_debug/settle_debug 通用执行器（step (S3,_) 整层路由，单枚举不动，新命令=成员+宣言行+一行表）；桩相位机三态+stack_trace 守卫三码（bool 排除哨照参考）；网炸 ChildRequest*/ChildGone* 补 `__repr__`（子面码进 detail/日志，2002 链不动） | ✅ 2026-10-20（a2b114e）；probe_stacktrace RED 3→7/7+test_core 5/5（新持久纯核直测网）+五旧网全绿；分层翻案史两轮+not_stopped 负路押后随 continue 入 design-notes |

## 待办

### 协议线
- [ ] 砖4 收场路（**押后中**）：disconnect 带 child 拆除（`TearDownChild` 占位员落位）、退出码归类
- [ ] 砖5 child 死路混沌探针（立场乙：父活快败陪到底）
- [ ] 横向剩余 6 条（每条=完整循环）：scopes/variables（世代票据首撞）→ continue（运行态+事件流；not_stopped 负探针在此补）→ setBreakpoints → next/stepIn/stepOut/pause → terminate → replay.branchTrace（引擎集大成，横向收尾；OQ-2 预告走 output 事件流非简单代理）
- [ ] 收尾清单：stdio 模式、cli.py:151 接线改挂、pytest DAP 面 41 项改挂、ADR 注、原包 dap 四件退役、5c 真逻辑填实

### 引擎线（每件配 oracle 对账网）
- [ ] E1 ktest 解析重写（纯件：真 ktest 喂，对账 M1 parse；值 ADT/错误前置首发）
- [ ] E2 BranchSite/预扫描语义+走向推断（纯件：合成 PC 序列网；语义源=branches.py，代码不入境）
- [ ] E4 会话编排（一次性循环→请求驱动；真 session 替桩——lldb_session 已就位，(动作,事件)→SessionState 建模接入口在 design-notes「E3 review 升格」）

## 挂账（未决小钉，撞结再裁）

- **类型检查器**：代数化后"忘拆包/漏成员"类错该由检查器兜（E3 网炸实证），接入待裁
- **壳侧直测网**：漏导入两连（Crashed/ForwardRequest，py_compile 不设防 NameError）
- **重复定义盲区**：新旧同名函数并存后者静默覆盖，六网全绿照不出——纯核单测入常例（design-notes「E3 review 第二轮」）
- **事件状态门**：留首个"事件改变协议状态"的砖再裁；await 顶点+内部事件门同理（真异步源）
- **铸名**：随撞随铸（LAUNCHING 案已废；Action/Outcome 已铸）

## 红线

- evaluate/completions 是 ADR 明确推迟项，不顺手实现
- 承重协议：先验用户写，AI 不代答；对账后账本追加（不改旧行）
- 引擎重写**零代码继承**：不因图省事 import 原 replay；规格继承+行为 oracle 是唯一合法传承
- capability 冻结语义照旧（可选才广告）；版本校验行为、请求级失败线形状两钉未裁

## 环境

- 本机（lxg）：clang/clang++/lldb 22.1.8 系统级；lldb python 绑定经 `lldb -P` 定位（plain import 不通）；无 ~/venvs/symrepl（旧机路径失效，M1 CLI 若跑需重建）
- 真靶子：`/tmp/opencode/replay-build/get_sign`（按 tests/conftest.py 配方即建；直跑基线 test1→0 / test2→1 / test3→255）
- 探针全封闭跨机可跑（odot 硬编码已相对化；KT 相对化，TG 仍不透明载荷）；端口：转移表 5681 / launch 5683 / threads 5685；framing 起服自洁 fuser（psmisc 已装）
