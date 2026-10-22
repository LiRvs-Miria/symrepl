# src/ — 产品树（cleanroom 转正，2026-10-22）

- `server/` — DAP 面七件（schedule/adapter/session/framing/childproto/error/lldb_backend），
  按 checkout 脚本运行（`python3 src/server/schedule.py --help`）；
  设计档案=[`../docs/design-notes/server.md`](../docs/design-notes/server.md)（契约/裁决/命名/通道选型/网炸史全记录）
- `replay/` — 回放引擎代数两件（branches/ktest，纯件零依赖）；
  设计档案=[`../docs/design-notes/replay.md`](../docs/design-notes/replay.md)

测试侧在树外：`probes/`（15 网+共用驱动库 dap_client 精简版）、
`tests/`（pytest：framing 纯件面）。历史（M1/旧 dap/client/phase1-2）走 git 考古。
