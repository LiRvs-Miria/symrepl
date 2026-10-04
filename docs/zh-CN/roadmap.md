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

## M2 — DAP server 模式

架构契约：[ADR-0003](adr/0003-m2-dap-server-architecture.md)
（parent DAP adapter + 常驻 LLDB service 子进程；stdio/`--port`；对齐
mock-debug 的 DAP 子集；分支追踪经 `stopOnBranch` 与自定义请求
`replay.branchTrace`）。

切片：

- **M2-a 骨架** — DAP framing（标准库，拆帧/粘包单测）、说内部协议的
  service 化子进程（版本化握手）、`symrepl dap` 入口（stdio +
  `--port`）、stdio 卫生、生命周期/故障语义（stdin-EOF 自杀、关机
  阶梯）、mock child 测试、`SendAsyncInterrupt` spike。
- **M2-b1 DAP 生命周期** — initialize/launch/configurationDone、
  threads/stackTrace/continue、terminate/disconnect（含运行中
  disconnect）；手写 Python 测试客户端。
- **M2-b2 状态面** — scopes/variables（按停止点的引用代际）、源码断点。
- **M2-c 分支追踪上 DAP** — 分支站点断点、`stopOnBranch`、
  `replay.branchTrace`、经 `output` 的会话末尾跟踪报告、inferior 输出
  转发。
- **M2-d stepping** — next/stepIn/stepOut/pause。
- **M2-e 收尾** — 声明式 VS Code 扩展（零代码）、en/zh 文档、DAP 验收
  场景、混沌测试（子进程崩溃、畸形帧）。

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
