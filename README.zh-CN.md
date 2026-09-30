# symrepl

[English](README.md) | 简体中文

在真正的调试器里回放符号执行的反例。

symrepl 接收符号执行器产出的工件（KLEE 测试用例及其记录的输入），在 LLDB
下回放它们：沿记录路径设断点、逐步注入输入、在每个决策点导出程序状态。

符号执行回答的是"**哪个输入**违反了性质"。symrepl 回答每个工程师紧接着要问
的问题："**具体发生了什么？一步一步讲。**"

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
[docs/adr/0001-scope-and-ip-firewall.md](docs/adr/0001-scope-and-ip-firewall.md)）。

## 生态

[ptrfuzz](https://github.com/LiRvs-Miria/ptrfuzz) —— 面向无法承载 sanitizer
runtime 目标的覆盖率引导模糊测试平台，构建在 symrepl 的调试侧基础设施之上。
ptrfuzz 之于 symrepl，如同 Clang 之于 LLVM：从 ptrfuzz M2 起，symrepl 成为
其库依赖。

## 状态

Pre-MVP：仅脚手架。路线图见 [docs/roadmap.md](docs/roadmap.md)——下一步是
M1（在 LLDB 下回放 ktest）。

## 计划中的依赖

- 带脚本支持的 LLDB（Python API）
- Python >= 3.10

## 许可证

Apache-2.0 WITH LLVM-exception——与 LLVM 项目相同的许可证，因为 symrepl 就
处在这个生态里。
