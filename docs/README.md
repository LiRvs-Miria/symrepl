# Documentation

English is authoritative; every page has a 简体中文 counterpart kept in
sync in the same commit.

## Pages

| Page | English | 简体中文 |
|---|---|---|
| Roadmap (milestones M0–M4) | [en/roadmap.md](en/roadmap.md) | [zh-CN/roadmap.md](zh-CN/roadmap.md) |
| Five-minute background | [en/background.md](en/background.md) | [zh-CN/background.md](zh-CN/background.md) |

## Architecture Decision Records

| ADR | English | 简体中文 |
|---|---|---|
| 0001 — Scope and IP firewall | [en/adr/0001-scope-and-ip-firewall.md](en/adr/0001-scope-and-ip-firewall.md) | [zh-CN/adr/0001-scope-and-ip-firewall.md](zh-CN/adr/0001-scope-and-ip-firewall.md) |
| 0002 — M1 input contract: single `.ktest` + dynamic branch tracing | [en/adr/0002-m1-input-contract.md](en/adr/0002-m1-input-contract.md) | [zh-CN/adr/0002-m1-input-contract.md](zh-CN/adr/0002-m1-input-contract.md) |

## Layout

`en/` and `zh-CN/` are mirror trees. New pages land in `en/` first and
are translated in the same commit; language switchers live at the top
of each page and link across trees (`../zh-CN/…` / `../en/…`).
