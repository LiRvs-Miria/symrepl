# local-cleanroom — 阶段 1 Clean-room 产物（学习档案）

> **状态：学习/对照用，不是 `src/symrepl/dap/` 的替代品。**
> 替代/演进决策押后至阶段 2（解封深读）+ 阶段 3（真库实现 shutdown + 187 测试回归）完成之后。

- `server/` — 会话骨架：单会话拓扑（active_writer 单槽 + client_done 单出口）、收场矩阵六行、退出码两层（FIN=0 / ABRUPT=2）、错误分类两轴。设计稿见 `server/design-notes.md`（转移表 + slot 伪代码 + 偏离清单 + OQ-1~4 开放问题）
- `client/` — 黑盒驱动客户端（dap_client.py，帧序实验/验收工具）

复述判据待过（承重协议）；工作副本在 `~/github/test_code/`（易失区外），本目录为快照。
