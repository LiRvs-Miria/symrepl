# Documentation

English is authoritative; every page has a 简体中文 counterpart kept in
sync in the same commit.

## Pages

| Page | English | 简体中文 |
|---|---|---|
| Roadmap (milestones M0–M4) | [en/roadmap.md](en/roadmap.md) | [zh-CN/roadmap.md](zh-CN/roadmap.md) |
| Five-minute background | [en/background.md](en/background.md) | [zh-CN/background.md](zh-CN/background.md) |
| Project management guide | [en/project-management.md](en/project-management.md) | [zh-CN/project-management.md](zh-CN/project-management.md) |

## Architecture Decision Records

| ADR | English | 简体中文 |
|---|---|---|
| 0001 — Scope and IP firewall | [en/adr/0001-scope-and-ip-firewall.md](en/adr/0001-scope-and-ip-firewall.md) | [zh-CN/adr/0001-scope-and-ip-firewall.md](zh-CN/adr/0001-scope-and-ip-firewall.md) |
| 0002 — M1 input contract: single `.ktest` + dynamic branch tracing | [en/adr/0002-m1-input-contract.md](en/adr/0002-m1-input-contract.md) | [zh-CN/adr/0002-m1-input-contract.md](zh-CN/adr/0002-m1-input-contract.md) |
| 0003 — M2 architecture: DAP server on a parent/child topology | [en/adr/0003-m2-dap-server-architecture.md](en/adr/0003-m2-dap-server-architecture.md) | [zh-CN/adr/0003-m2-dap-server-architecture.md](zh-CN/adr/0003-m2-dap-server-architecture.md) |
| 0004 — M3 path narrowing: the KLEE-fork subset via LLVM IR symbolic analysis | [en/adr/0004-m3-path-narrowing.md](en/adr/0004-m3-path-narrowing.md) | [zh-CN/adr/0004-m3-path-narrowing.md](zh-CN/adr/0004-m3-path-narrowing.md) |
| 0005 — M4 path consistency verification: the symbolic path condition vs the replayed decisions | [en/adr/0005-m4-path-consistency.md](en/adr/0005-m4-path-consistency.md) | [zh-CN/adr/0005-m4-path-consistency.md](zh-CN/adr/0005-m4-path-consistency.md) |
| 0006 — M4-a differential replay matrix: foundation of the taint overlay | [en/adr/0006-m4a-differential-replay-matrix.md](en/adr/0006-m4a-differential-replay-matrix.md) | [zh-CN/adr/0006-m4a-differential-replay-matrix.md](zh-CN/adr/0006-m4a-differential-replay-matrix.md) |

## Layout

`en/` and `zh-CN/` are mirror trees. New pages land in `en/` first and
are translated in the same commit; language switchers live at the top
of each page and link across trees (`../zh-CN/…` / `../en/…`).
