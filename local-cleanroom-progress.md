# cleanroom 转正推进状态（立项 2026-10-16：local-cleanroom 替代 src/symrepl/dap/）

> 本文件是会话进度快照（**冻结 2026-10-22**），冷启动从此取活。
> 配套：[`docs/design-notes/server.md`](docs/design-notes/server.md)（设计档案，2026-10-22 迁 docs/design-notes/，含契约/裁决/命名/通道选型/网炸史全记录）、[`../research-survey/plan/ai-independence.md`](../research-survey/plan/ai-independence.md)（承重协议，§2.6 决策节律）、[`../research-survey/plan/ai-ledger.md`](../research-survey/plan/ai-ledger.md)（对账账本，追加式）。
> 转正注记（2026-10-22）：树已由 `local-cleanroom/` 更名 `src/`（本文件名留用）；M1 CLI 回放链路同日退役（取代裁决走到底，git 历史存档）。
> 前史：phase1（2026-10-12 关账）/ phase2（2026-10-15 收官）的 local-test* 档案已删除（2026-10-16），考古走 git 历史。

## 总纲（已定裁决，勿重议）

- **目标（2026-10-22 改判）**：cleanroom 即产品树 `src/`（server 7 件+replay 2 件+probes+client）；原「M1 replay 包永驻/CLI 产品路径不动」裁决**同日废止**——DAP 面已全覆盖回放能力（branchTrace M1 平价+dap_client replay_chain），cli/M1 五件/M1 面测试/acceptance 两孤儿随葬，probe_ktest 收割 M1 oracle 真值一次后改纯形状钉（E2 无-oracle 先例）。零代码继承纪律不变（历史语义，RIP）
- **转正定形（2026-10-22 用户裁，B 案）**：cleanroom **原地即产品**（server 7 件+replay 2 件不搬进包，网/探针/client 零改动）；src/symrepl/dap/ 整册退役，src 只剩 cli+M1 五件；`symrepl dap`=cli 子进程 spawn 本册 schedule.py（退出码透传）。原 A 案（五件包化/replay-algebra 子包/13 网搬家/`-m`/pytest 41 项重切+stdio 冒烟）**整体作废**——packaging 摩擦两日未克，用户止损改道；DAP 面入库（ptrfuzz import）需求实证出现时再包化（挂账）
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
| E4 停读面第一刀 | 真 session 替桩：lldb_backend 适配器（原语面五件+相位机+世代票据 gen0）+session.py 端口面毕业（DebugBackend Protocol/插拔册/工厂，"session 不变、调试器可插拔"）+参数化两钉（父子 --backend/--lldb-dir required，PATH/env 猜测全拆）+llvm-22.1.8 切环境；client 手动工作台（Manual/一键代驾/-i）；网炸空命名空间包（验签踢槽）+桩假设被真 tid 纠正（两步走） | ✅ 2026-10-20（d8575a9+步3 收网笔）；七网全绿（转移表真会话首跑+孤儿断言双绿）+probe_e3 diff=0×3+manual_chain 真链通；not_stopped 负探针仍随 continue |
| 横向 scopes/variables | 世代票据消费侧首撞：表驱动第 4/5 命令（step/resolve 零改动）；适配器 scopes/variables 原语+引用账本（gen*100000+计数器，适配器私有）+码 3015/3016+守卫单集（OQ-2 分集合首分叉）；网炸 child 裸 list 上线——修=命令名键重包成文家法 | ✅ 2026-10-20（f4b1bca）；probe_scopes RED 5→11/11+test_core 9/9+七网回归全绿；新命令成本账本第三次兑现 |
| continue | 横向与 E4 合流点：子进程事件泵（参考同构 reader 线程+owner 轮询，事件先发+dead 短路）+世代推进（决策 12 resume 失效）+exited/terminated 父面一变二（单源：child 只发执行面 exited）+_MAX_RESUMES 不随迁（偏离带因）；网炸端口面漏接单+terminated 双发（单源裁决）；守卫序勘误（相位先于世代解码，stale 触发随 setBreakpoints 迁移） | ✅ 2026-10-20（336b127）；probe_continue RED 6→12/12+八网回归全绿；DAP 生命周期首次全程可观测（launch→入口停→continue→exited+terminated） |
| 横向 setBreakpoints | 第二次停相产出者：核表第 6 命令（params_fn 嵌套提取/非 dict 过滤+body verified 缺省）+backend 断点表全量替换（记册删旧/location 地址回解析 resolved+记册）+stop_reason_label 纯核推断（bp id 携 breakpoint_ids，count 0 越界防）+child 类型哨/键重包/Protocol 签名；守卫分布=类型域归 child、not_launched+空 path 留 backend；网炸**双键重包**（契约互斥表述，修=bare list+child 重包家法同构，与 scopes 砖忘重包成对）；RED 预测勘误 P7（单链直通 exit 先消费生命周期对，用户裁照单收） | ✅ 2026-10-20（6d5d1bb）；probe_breakpoints RED 10→17 查 ALL PASS+九网回归全绿；stale_reference 负路首次真实触发（世代票据消费侧闭环）+not_stopped/stale 两负路兑现 |
| 引擎线 E1 ktest 解析重写 | 引擎线首件（新册 replay/，零代码继承首落地）：四答定稿（纯读/Expect 代数 KTest\|KtestBad 当值不抛/KtestBadReason 封闭三枚照 FrameBad 家法/新册）；值 ADT 字段同款 M1（v1→sym 0/0）；撞裁=私有 _Truncated fail-fast+parse 边界单点转值（逐点 None 检查漏检面大不取）；**语料册自检前置**（17 合成例+6 真件先对 M1 oracle 合预期，挡掉语料册两处 layout 错） | ✅ 2026-10-20（待提交笔）；probe_ktest RED 10→10/10 零网炸+纯网三件回归绿（server/ 零 diff，LLDB 六网无风险） |
| 引擎线 E2 BranchSite 值代数与路径对比（纯件） | **取代裁决首落地（无 oracle——M1 代码不入境，E1 oracle 模式对本刀不适用）**，三轮撞裁定形：①x86 专用识别单表（aarch64/kind 权威路/parse_branch_target 全出局）②`+` 到达代数与混杂 step 两钉打回 ③**函数式定形**（用户三钉）：BranchSite 纯值无方法（代数住模块函数，"做不成代数该直说"）/== 自下而上（字段→`equal` 逐域合取无地址捷径→`match_path` map 高阶逐位）/实际执行只记录实际 list，对比独立代数；元素结果=Bool，长度不齐=PathBad(LENGTH_MISMATCH) 当值；可得性模型=target 侧三域全知、replay 侧 address/function 调用栈+mnemonic 反汇编（SBInstruction.GetMnemonic，E4 壳侧取） | ✅ 2026-10-21（待提交笔）；probe_branches RED 12→12/12（两轮打回新钉 RED 7/8 精准红）+纯网四件回归绿；网钉自愈一枚（hasattr 撞 tuple 继承面→__dict__+行为双钉）；网搬运进化=模块属性访问（小步部分绿可见） |
| branchTrace 预扫描引导回放+报告（M2 收尾件，横向收尾） | **target=预扫描站点集仅作断点依据**（E2 is_conditional 首消费点；`_instrument_scope`：主模块 code symbols 反汇编/单表筛选/地址设断/5000 守阀；scope=launch 可选参数，None=不插桩）/**actual=断点命中据实记录**（pc/function/mnemonic/调用栈/符号 value+ktest 字节对账注记 match·mismatch·ambiguous·unmatched——注记派生层，零处理红线）/报告=跑末汇总 output 事件（**仅 scope 在册会话附**，未启用零增帧；双形式：人读文本+结构化节点；父面 exited 一变三 output 先发）/无命令面纯被动；路径级一致性=独立校验工具归 **M4**（方案 A 用户裁，薄桥定位）；E2 match_path/equal 不接回放（M4 消费） | ✅ 2026-10-21（待提交笔）；probe_trace RED 4→ALL PASS（三小步：插桩/记录/汇总通道，每步网验证）；**网炸一枚 scope 在壳被丢**（裸驱后端全好、DAP 链直通 exit——裸驱/线测分责逮住）；探针勘误一枚（`<main>`→`<get_sign>`，AI 钉错自收）；**全循环 13 网回归绿**；匹配启发式现形=非定义帧 unmatched 据实标注；probe_e3 存量陈旧修（continue 砖守卫收紧后未随网更新）；breakpoints 网 flaky 两例取证入网（flaky 未坐实挂账盯梢） |
| 归一 B 案（cleanroom 原地转正） | A 案（包化+搬家+pytest 重切）执行半程两度受阻（stdio 冒烟挂死诊断未克+工作树被外部重置回冻结点），用户止损改道 B 案：**src/symrepl/dap/ 整册退役**（git rm 旧四件）+旧 pytest DAP 面 43 项随葬（test_dap_server/session/chaos 删）+**framing 20 项纯件单测补位**（直指 local-cleanroom/server/framing.py，sys.path 注入，不涉进程/LLDB）+cli `_dap_main` 改子进程 spawn（`symrepl dap` 命令面不变，退出码透传：stdio EOF→0/缺参 argparse→2 实测）+cleanroom 9 件原地零动 | ✅ 2026-10-22（待提交笔）；pytest 164 全绿（M1 面 144+framing 20）+15 网全量回归 rc=0×15+`symrepl dap` 两路冒烟绿；押后两钉：DAP 面入库（ptrfuzz import 需求实证再包化）/local-cleanroom 改名 |
| 砍到底+转正定形（B2） | 用户两问驱动的终点裁决：①cli 意义审查——DAP 面已全覆盖回放（branchTrace 平价+replay_chain），cli/M1 全退（`git rm -r src/symrepl`+M1 面 5 测试文件+acceptance 两孤儿+spike 死件）；②「local-cleanroom 替代 src」——`git mv local-cleanroom src`，产品树=src/{server,replay,probes,client}，网/探针相对路径零改动；probe_ktest **收割 M1 oracle 真值一次后改纯形状钉**（17 语料+6 真件值钉+reason 名直钉，vscode/dap_adapter 启动器改指 checkout 树）；pytest DAP 面=framing 20 项（sys.path 指 src/server）；pyproject 降 dev-only（[project.scripts] 删，无可安装包）+CI install 改直装 pytest/hypothesis；venv 卸载 editable symrepl；README/AGENTS/roadmap en/zh/dap.md en/zh 同步 | ✅ 2026-10-22（待提交笔）；pytest 20/20+15 网 rc=0×15（含无 oracle probe_ktest 首跑即绿）；押后钉更新：VS Code lldb-dir 接线随应用场景押后（启动器已指对树） |
| 测试侧分离+client 退役（B3） | 用户三问定形：①probes 迁出产品树（`git mv src/probes probes`，15 网路径常量改线：server→parent.parent/"src"/"server"、3-up 资源引用降级、ktest/branches 的 replay 册指 src/replay）②client 裁退役——但 8 网拿 dap_client 当驱动库（dc.send×77/recv×32/connect×10 实证），刀法=**库随网走**：probes/dap_client.py 精简版（仅 connect/send/recv 四函数 verbatim），手工作面板 Manual/manual_chain/replay_chain/show* 随 src/client 整册退役；③单元测试判据=纯件要害在 pytest（framing 20）+行为在网，不缺单测缺 CI——封闭五网（framing/childseam/test_core/ktest/branches，零 LLDB）进 CI 与 pytest 同批，真饵九网留本地 | ✅ 2026-10-22（待提交笔）；pytest 20/20+15 网两轮 sweep 全绿（sweep1 breakpoints 网红一次=已知 flaky 挂账再现一例：单跑即复绿、根因未坐实，盯梢累计三例）；scripts/ 目录随 acceptance/spike 退役后已空 |
| 引擎线 E4 会话编排代数化收敛（E4 关账件，结构收敛刀） | 用户两钉先验：①执行面照协议面 v5 家法代数化 ②加纯核直测网。AI 攻击三枚照单收：双触发源（事件即转移，核开 step/resolve+ingest 双入口）/守卫矩阵实为两轴（threads 零守卫裸崩区+set_breakpoints 只查 process 在场，怪相入表逐行冻结含 OQ-2 分叉）/唯一声明行为差=continue 失败臂世代推进时序（提交制修掉：效果败世代不进，用户裁照收）。新册 `src/server/session_lifecycle.py`（ExecPhase 五值 ADT/ExecState{phase,generation}/_GUARDS 唯一权威表+_TRANSITIONS 转移行/guard_exec/step_exec/resolve_exec 提交制/ingest_exec/poll_blocked）；壳瘦身：`_exec: ExecState` 替 `_phase`+`_generation` 双散装，三转移命令 step→效果→resolve→_commit（世代差=清账信号），读守卫×5 换 guard_exec，poll 两臂 ingest 落靶+CaptureNode/EmitExit 意图消费；require_phase 退役；防御行两枚生效（threads/set_breakpoints×ENTRY→3012、poll×ENTRY 短路，端口不可达区 crash→干净错） | ✅ 2026-10-22（待提交笔）；RED 25 全红→三小步转录（每步用户 review+网验证）→test_session_lifecycle 25/25+pytest 20/20+**16 网 rc=0 全绿**；LSP 逮 _generation 漏改四处（类型检查器挂账首兑现）；靶子易失重建基线 0/1/255 先钉 |

## 待办

### 协议线
- [ ] 砖4 收场路（**押后中**）：disconnect 带 child 拆除（`TearDownChild` 占位员落位）、退出码归类
- [ ] 砖5 child 死路混沌探针（立场乙：父活快败陪到底）
- [x] stdio 模式（✅ 2026-10-21：schedule `--stdio` flag+`amain_stdio`——handle 传输无关直接接管 stdin/stdout，单"连接"=进程即会话，信号=读侧 feed_eof；probe_stdio 6/6 绿（握手/状态门/真链/收场/stdin-EOF/卫生）+四网回归绿）
- [x] roadmap en/zh 编辑性修订（✅ 2026-10-21 已随 7ba89f2：branchTrace 重定形/stepping 押后非砍死/一致性校验归 M4）
- [x] ~~归一一步到位（原 A 案：五件包化/replay-algebra 子包/13 网搬家/pytest 41 项重切）~~（✅ 2026-10-22 以 **B 案**关账：cleanroom 原地转正+src dap 退役+framing 20 项补位+cli 子进程派发——见已关账表；A 案细则整体作废）

### 引擎线（对账网随件裁：E1 oracle 对账 / E2 起取代无 oracle）
- [x] ~~E4 会话编排续刀~~（✅ 2026-10-22 关账：编演建模收敛以代数化兑现——session_lifecycle 纯核册+壳瘦身+test_session_lifecycle 25 查；trace_report 输出事件流早已随 branchTrace 落地——见已关账表；**引擎线清零，交替制终结**）

## 挂账（未决小钉，撞结再裁）

- **stepping 押后钉**（2026-10-21 用户重定性：非砍死——断点停下后的用户调试策略，原 M2-d 地位恢复，M2 收尾后实现，需长跑靶子）；**pause/terminate 复活钉**（当前不实现，需求实证出现再议）——链路知识存 server/design-notes step 族节

- **stdio/VS Code 押后钉**（2026-10-22 用户裁：应用场景问题，打通链路后再搞——`--stdio`/`amain_stdio` 实现已关账 6/6 绿，后续演进与真客户端联调押后；vscode/dap_adapter 启动器已指 src/server/schedule.py，扩展 lldbDir 属性接线随本钉）
- **DAP 面入库钉**：ptrfuzz 或他者要 import 树时再包化（pyproject 现为 dev-only，无可安装包；M1 已退役，库面届时从 cleanroom 定形）
- **flaky 盯梢钉**（breakpoints 网，累计三例：2026-10-21 两例+2026-10-22 一例——深循环偶发单跑复绿，取证已入网，根因未坐实）；**假绿盯梢钉**（2026-10-22）：probe_ktest 转正首轮回跑 rc=0 但 ROOT 层级差一（ROOT/"replay" 应为 parent.parent/"replay"）——同路径现在确定性红，当时假绿机制未坐实（怀疑与 git mv 时序的中间态有关）；已修死+终版全绿，若再现按 flaky 取证入网家法办
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

- 树结构（2026-10-22 转正后）：`src/server`（DAP 七件+session_lifecycle 编排核）/`src/replay`（引擎两件）/`probes`（16 网+共用驱动库 dap_client 精简版）/`tests`（pytest：framing 纯件面）；pyproject dev-only（pytest 经 `pip install pytest hypothesis`，无 editable 包）；本机：**llvm-22.1.8 全家**（lldb/clang/clang++ 22.1.8，Debian 官方源装；apt 源已由 TUNA 换 USTC——TUNA 缺 llvm-toolchain-22 谱系且 403；无版本名 /usr/bin/{lldb,clang,clang++} 均手指 22；llvm-19 库残留无害）；工具链目录经 `--lldb-dir` 参数显式给（=lldb+lldb-server 同册，**现为 /usr/lib/llvm-22/bin**），服务侧零 PATH 猜测；绑定定位 plain import→`lldb_dir/lldb -P` 备援（dist-packages 空命名空间包坑已修，design-notes E4 节）；`~/venvs/symrepl` 已建（pytest+hypothesis 直装，无可安装包——editable symrepl 已随 M1 退役卸载）；真靶子：`/tmp/opencode/replay-build/get_sign`（按 tests/conftest.py 配方即建，易失；直跑最小环境基线 test1→0 / test2→1 / test3→255，2026-10-22 重建验证）；探针真饵化（2026-10-20 起：launch/threads/stacktrace/转移表走真 LLDB 会话，依赖本机 lldb 22+靶子；封闭网=framing/childseam/test_core/ktest/branches/test_session_lifecycle）；端口：转移表 5681 / launch 5683 / threads 5685 / stacktrace 5687 / continue 5691 / breakpoints 5693 / trace 5695 / stdio 无端口；framing 起服自洁 fuser（psmisc 已装）；手动测试入口=src/client/dap_client.py（**replay_chain=实际 ktest 回放模拟**/一键代驾/-p 人在环/-i 交互/-m 旧 demo）
