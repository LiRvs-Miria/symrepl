# 路线图（Roadmap）

[English](../en/roadmap.md) | 简体中文

> 以英文版为准（authoritative）；两版在同一提交内同步更新。

每个里程碑刻意做小：约 2–3 个周末深度块。

## M0 — 脚手架（已完成）

- 仓库、许可证（Apache-2.0 WITH LLVM-exception）、CI、范围与 IP 防火墙
  （见 [ADR-0001](adr/0001-scope-and-ip-firewall.md)）。

## M1 — ktest 回放（MVP）——已完成 2026-10-04

输入契约已冻结：[ADR-0002](adr/0002-m1-input-contract.md)
（2026-10-04 编辑性修订——逐命中记录改为会话侧；决策不变）。

- `.ktest` 解析器（v2/v3——布局完全相同；读取容忍 `BOUT\n`，版本 > 3
  拒绝）：纯 Python、零依赖，字节布局对照 KLEE 源码核实，并由真实 KLEE
  夹具钉死。
- 回放驱动：经 LLDB Python API 启动目标进程（以 `-lkleeRuntest` 链接），
  注入 `KTEST_FILE`，对象级 harness 违规经 `KLEE_RUN_TEST_ERROR` 呈现——
  契约与 KLEE 官方回放路径一致。
- 分支追踪：对追踪范围内的条件跳转逐站点设断，按序记录 PC/结果，命中时
  导出参数/局部变量；与 KLEE fork"约束子集"的精确映射推迟到 M3
  （IR/DWARF）。
- 可选诊断（永不必需）：`.kquery` 的路径条件逻辑视图 pretty-print/校验
  ——尚未提供，留作后续。
- 验收：在 KLEE 教程示例（`get_sign`）上跑通完整闭环——`klee` 运行 →
  symrepl → 断点按序命中 → 状态报告；一键运行 `sh scripts/acceptance.sh`。

## M2 — DAP server 模式（已完成 2026-10-05）

架构契约：[ADR-0003](adr/0003-m2-dap-server-architecture.md)
（parent DAP adapter + 常驻 LLDB service 子进程；stdio/`--port`；对齐
mock-debug 的 DAP 子集；分支追踪经 `stopOnBranch` 与自定义请求
`replay.branchTrace`）。

实施映射（2026-10-05 规划定稿）：

- **原样复用**：`branches.py`、`trace.py`（`TraceReport.render()` 即
  output 事件推送的内容）、`ktest.py`（父进程校验，使用用户原始路径
  —— ADR-0003 决策 11）、`lldb_driver.py` 中的 LLDB 引导辅助与
  `_collect_sites`、inferior stdio 落盘模式（子进程自身 stdout 目前
  空闲，将用作协议通道）、`get_sign_bin` 夹具。
- **拆解改造**：`session_run` 内核改为交互式会话循环——entry stop
  后挂起等待内部命令而非跑完即退，`SBDebugger.Terminate()` 移至子进程
  退出时。每个 DAP 会话一个子进程：即 M1 隔离契约，把生命周期从
  "一次回放"延长为"一个会话"（每进程仍恰好一次 Initialize、一次
  launch）。
- **保留不动**：一次性 CLI 回放路径（`replay.py` +
  `lldb_driver.run()`）；DAP 只增加传输层，不引入新语义。
- **新建**：`dap_framing.py`（约 40 行标准库）、`lldb_service.py`
  （常驻子进程）、父进程 DAP server、仅开发用的 DAP 测试客户端。

切片——严格线性（每片依赖前一片的协议面）；分帧单测、测试客户端、
扩展清单骨架可提前并行：

- **M2-a 骨架** — `SendAsyncInterrupt` spike **已通过**（2026-10-05，
  LLDB 22.1.8，`scripts/spike_send_async_interrupt.py`）：`pause` 路线
  确认可行；spike 同时钉死服务循环的事件原语——`GetNextEvent` 加短暂
  sleep（`WaitForEvent` 绑定只接受整秒数）——以及两条附加规则：每次
  恢复前先排干队列、启动前经 `_ensure_debug_server()` 定位
  `lldb-server`。随后：分帧模块（按 encoded-byte 计数、64 MiB 上限、
  畸形帧杀会话；拆帧/粘包单测）、`lldb_service.py`（版本化 `hello`
  握手、ok/error envelope、stdin-EOF 自杀、单 owner LLDB 线程以
  `GetNextEvent` 短轮询加命令队列；stdio 卫生在最早入口完成——协议
  通道独占 stdout，stderr 重定向到文件，LLDB 日志不进 fd 1）、父进程
  server（stdio + `--port`、单写者输出队列、关机阶梯）、`symrepl dap`
  子命令（裸回放 CLI 及退出码不变）、mock child 测试。**验收**：
  stdio 与 socket 双模式完成握手；畸形帧杀会话；stdin EOF 或父进程
  SIGKILL → 子进程自杀。
- **M2-b1 DAP 生命周期** — initialize/launch/configurationDone
  （launch 响应扣留）、threads/stackTrace/continue、terminate/
  disconnect（含运行中 disconnect）；launch 参数 schema 冻结（ktest、
  target、stopOnBranch、stopOnEntry、scope、timeout），ktest 由父进程
  校验。**验收**：手写测试客户端在 socket 模式下跑通完整
  initialize→launch→运行→退出会话；运行中 disconnect 不留孤儿
  inferior 或子进程。
- **M2-b2 状态面** — scopes/variables 按停止点引用代际（resume 即
  失效；stale 引用回错误）、源码断点。**验收**：停止点可读变量；
  continue 后 stale 引用报错；断点命中并上报。
- **M2-c 分支追踪上 DAP** — 分支站点断点、`stopOnBranch`（`stopped`，
  reason `breakpoint`）、`replay.branchTrace`、会话末尾经 `output`
  推送 `TraceReport.render()`、inferior 输出转发（可丢弃有界队列加
  截断标记；控制事件永不丢弃）。**验收**：默认 M1 平价——同一夹具上
  DAP 报告与 CLI 轨迹一致；`stopOnBranch` 在每个站点暴露活变量。
- **M2-d stepping** — next/stepIn/stepOut/pause 映射到 SBThread step
  操作（`pause` 用 `SendAsyncInterrupt`），完成以
  `stopped(reason: "step")` 报告；交互态无全局超时。**验收**：每种
  单步都在夹具的预期函数内停住。
- **M2-e 收尾** — 声明式 VS Code 扩展（零代码）、en/zh 文档、DAP 验收
  场景（acceptance.sh 风格）、混沌测试（子进程中途崩溃、畸形帧、
  客户端中途断连）、LLDB smoke path。**验收**：完整闭环可从 VS Code
  经 `"debugServer"` 跑通；混沌测试以 `terminated` 干净失败，绝不
  挂死。

已知风险（2026-10-05 规划定稿）：

- **stdio 卫生**是最锋利的边界：任何库或 LLDB 内部对 fd 1 的杂散写入
  都会破坏分帧；重定向必须在最早入口完成，LLDB 日志路由需要显式
  测试。
- **输出顺序不变量**（`continue` 响应先于其引发的 `stopped` 事件）由
  单写者队列结构性保证，但需要专门的字节序测试钉死。
- **背压分裂**（可丢弃的 `output` vs 永不丢弃的控制事件）必须是两个
  显式队列类别，不能事后过滤。
- **一次性 CLI 路径保持冻结**；把它迁移到 service 子进程明确不在 M2
  范围内（M3 再评估）。

## M3 — LLVM IR / DWARF 映射

- 经 DWARF 把路径决策映射回 IR 位置与源码行。

## M4 — 分析叠加层

- 回放轨迹上的覆盖率与污点叠加。

## 生态

[ptrfuzz](https://github.com/LiRvs-Miria/ptrfuzz) 在 symrepl 的调试侧基础设
施之上构建覆盖率引导模糊测试平台——ptrfuzz 之于 symrepl，如同 Clang 之于
LLVM。从 ptrfuzz M2 起，symrepl 成为 ptrfuzz 的库依赖；标准格式防火墙
（ADR-0001）对两仓库同时生效。ptrfuzz 的开发在 symrepl M2 完成之后启动。

## 待决问题

- ~~**路径条件契约（阻塞 M1 实现）**~~——已于 2026-10-03 解决并冻结为
  [ADR-0002](adr/0002-m1-input-contract.md)：M1 必选输入仅为一个 `.ktest`
  （v2/v3）文件；路径决策经 LLDB 下的动态分支追踪恢复。不需要任何 KLEE
  sidecar；`.kquery` 仅作为可选诊断消费。
