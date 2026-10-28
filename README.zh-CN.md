# symrepl

[English](README.md) | 简体中文

在真正的调试器里回放符号执行的反例。

symrepl 接收符号执行器产出的工件（KLEE 测试用例及其记录的输入），在 LLDB
下回放它们：沿记录路径设断点、逐步注入输入、在每个决策点导出程序状态。

符号执行回答的是"**哪个输入**违反了性质"。symrepl 回答每个工程师紧接着要问
的问题："**具体发生了什么？一步一步讲。**"

## 这是不是给你的？

- 你用 KLEE 跑了一个解析器，它递给你 `test000001.ktest`——一个让断言在
  40 个分支之后失败的具体输入。今天的做法：打开 GDB、手工解析二进制
  `.ktest` 格式、手工重建输入文件。用 symrepl：一条命令打开 LLDB，输入
  已注入、断点已沿记录路径设好。
- 你的符号执行/模糊测试管线产出 200 个独立崩溃，你必须判断哪些是真 bug。
  symrepl 把每个反例放到调试器下回放并导出路径状态——分诊（triage）变得
  可脚本化、可比较。
- 你在教或学符号执行，想亲眼看到路径条件如何逐分支"驾驶"程序执行。

对符号执行或上述术语陌生？读一读[五分钟背景](docs/zh-CN/background.md)，
不假设任何 KLEE 使用经验。

## 为什么做这个

KLEE 自带 `klee-replay`——一个基于 GDB 的最小回放器。一旦反例涉及非平凡的
路径条件，"跑一遍看着"就不够了：你需要在约束该路径的分支点上设断点，并让调
试器里的状态与产生失败的执行路径对齐。

symrepl 填补的就是这个空隙。它是符号执行与交互式调试之间的一座**薄桥**——
不是新的符号引擎。

## 范围与架构铁律

symrepl 只消费**公开标准格式**：

- KLEE `.ktest` 文件
- LLVM IR / bitcode
- DWARF 调试信息
- 调试适配协议（DAP）

任何只有了解特定私有代码库或方言才有意义的内容都不属于这里（见
[docs/adr/0001-scope-and-ip-firewall.md](docs/zh-CN/adr/0001-scope-and-ip-firewall.md)）。

## 生态

[ptrfuzz](https://github.com/LiRvs-Miria/ptrfuzz) —— 面向无法承载 sanitizer
runtime 目标的覆盖率引导模糊测试平台，构建在 symrepl 的调试侧基础设施之上。
ptrfuzz 之于 symrepl，如同 Clang 之于 LLVM：从 ptrfuzz M2 起，symrepl 成为
其库依赖。

## 状态

回放能力经 DAP server 交付：ktest 解析、沿追踪范围的断点、按序分支报告
（含逐站点状态）与交互式步进（`next`/`stepIn`/`stepOut`，VS Code 内）。
启用可选 `ir` 启动参数（M3，2026-10-24 交付——
[ADR-0004](docs/zh-CN/adr/0004-m3-path-narrowing.md)）后，报告节点携带静态
计算的 fork/runtime 标注，把轨迹收窄到路径决策。原 M1 CLI 已于
2026-10-22 退役；今日无 CLI——DAP server 即产品面。

下一步：M4 分析叠加层（覆盖率、差分回放的输入依赖、路径一致性校验；
ADR-0005/0006）。路线图见
[docs/roadmap.md](docs/zh-CN/roadmap.md)，M2 架构契约见
[ADR-0003](docs/zh-CN/adr/0003-m2-dap-server-architecture.md)。DAP 用法：
[docs/zh-CN/dap.md](docs/zh-CN/dap.md)。

## 运行要求

- 带脚本支持的 LLDB（Python API）
- Python >= 3.10

## 许可证

Apache-2.0 WITH LLVM-exception——与 LLVM 项目相同的许可证，因为 symrepl 就
处在这个生态里。
