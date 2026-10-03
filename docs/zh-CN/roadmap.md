# 路线图（Roadmap）

[English](../en/roadmap.md) | 简体中文

> 以英文版为准（authoritative）；两版在同一提交内同步更新。

每个里程碑刻意做小：约 2–3 个周末深度块。

## M0 — 脚手架（已完成）

- 仓库、许可证（Apache-2.0 WITH LLVM-exception）、CI、范围与 IP 防火墙
  （见 [ADR-0001](adr/0001-scope-and-ip-firewall.md)）。

## M1 — ktest 回放（MVP）

输入契约已冻结：[ADR-0002](adr/0002-m1-input-contract.md)。

- `.ktest` 解析器（v2/v3——布局完全相同；读取容忍 `BOUT\n`，版本 > 3
  拒绝）：纯 Python、零依赖，字节布局对照 KLEE 源码核实。
- 回放驱动：经 LLDB Python API 启动目标进程（以 `-lkleeRuntest` 链接），
  经 `SBLaunchInfo` 注入 `KTEST_FILE`，并预先校验对象名字/大小——harness
  契约与 KLEE 官方回放路径一致。
- 分支追踪：对执行范围内的条件跳转逐地址设断，脚本回调按序记录 PC/结果，
  命中时导出状态；与 KLEE fork"约束子集"的精确映射推迟到 M3（IR/DWARF）。
- 可选诊断（永不必需）：`.kquery` 的路径条件逻辑视图 pretty-print/校验。
- 验收：在 KLEE 教程示例（如 `get_sign`）上跑通完整闭环——`klee` 运行 →
  symrepl → 断点按序命中 → 状态报告。

## M2 — DAP server 模式

- 经调试适配协议（DAP）暴露回放会话；回放可在 VS Code 及任何 DAP 客户端中使用。

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
