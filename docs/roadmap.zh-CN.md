# 路线图（Roadmap）

[English](roadmap.md) | 简体中文

> 以英文版为准（authoritative）；两版在同一提交内同步更新。

每个里程碑刻意做小：约 2–3 个周末深度块。

## M0 — 脚手架（已完成）

- 仓库、许可证（Apache-2.0 WITH LLVM-exception）、CI、范围与 IP 防火墙
  （见 [ADR-0001](adr/0001-scope-and-ip-firewall.md)）。

## M1 — ktest 回放（MVP）

- `.ktest` 解析器（版本 2/3）：纯 Python、零依赖，字节布局对照 KLEE 源码核实。
- 回放驱动：经 LLDB Python API 启动目标进程，按 harness 契约注入记录的输入。
- 路径条件感知断点：在约束记录路径的分支点设断，命中时导出变量/寄存器。
- 验收：在 KLEE 教程示例（如 `get_sign`）上跑通完整闭环——`klee` 运行 →
  symrepl → 断点按序命中 → 状态报告。

## M2 — DAP server 模式

- 经调试适配协议（DAP）暴露回放会话；回放可在 VS Code 及任何 DAP 客户端中使用。

## M3 — LLVM IR / DWARF 映射

- 经 DWARF 把路径决策映射回 IR 位置与源码行。

## M4 — 分析叠加层

- 回放轨迹上的覆盖率与污点叠加。

## 待决问题

- **路径条件契约（阻塞 M1 实现）**：路径条件有多少能仅从 `.ktest` 对象恢复，
  多少需要 KLEE 额外产出 sidecar 决策日志？调研 KLEE 的输出选项，然后冻结 M1
  输入契约。若需要 sidecar，其格式必须在本仓库公开定义（见 ADR-0001）。
