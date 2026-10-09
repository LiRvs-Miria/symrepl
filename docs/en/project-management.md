# Project Management Guide

English (this page) ｜ 简体中文: [zh-CN/project-management.md](../zh-CN/project-management.md)

> This page records symrepl's project-management structure and operating
> procedures: the whole setup can be reproduced from scratch in any
> environment with the `gh` CLI and a suitable token, and run day to
> day. The English version is authoritative; both are synced in the
> same commit.

## The four layers

| Layer | Vehicle | Granularity | Current state |
|---|---|---|---|
| Milestones | GitHub Milestones | Release-level (M0–M4, mirrors the roadmap) | M1/M2 closed (100%), M3/M4 open |
| Domain labels | GitHub Labels | Per-issue/PR area classification | `replay` `dap` `ktest` `vscode` `tests` + default type labels |
| Board | Project V2 “symrepl 路线图” | Cross-milestone item view (Todo/In Progress/Done) | Repo linked; M3 slices listed |
| Releases | Annotated tag + Release | Version deliverables and notes | `v0.1.0`, `v0.2.0` both released |

Division of labor: **milestones** own "what does done mean",
**the board** owns "where are we now", **labels** own "which area is
this", **releases** own "what shipped".

## Conventions (consistent with existing practice)

- **Slice naming**: `Mx-a`, `Mx-b`, … — each slice is an independently
  completable block (about one deep weekend), advanced in
  implementation → wiring → tests commits.
- **ADR first**: slices with real design forks (e.g. M3's
  input-dependence method) freeze an ADR before implementation; the
  ADR issue explicitly blocks the slices that depend on it.
- **Parent issue + sub-issues**: one parent issue per milestone
  (numbering mirrors the roadmap section); slices hang below it as
  sub-issues in dependency order.
- **Progress semantics**: GitHub milestone progress bars count only
  linked issues/PRs. Historical milestones (work landed as direct
  commits, no issues) are closed out with **retroactive issues** —
  see M1 (#1–#4) and M2 (#5–#10), each noting its delivering commits
  and version.
- **Bilingual docs**: `docs/en/` is authoritative, `docs/zh-CN/` is
  the mirror, synced in the same commit.
- **Commit message prefixes**: `实现:` (implement), `接线:` (wire),
  `测试:` (test), `文档:` (docs), `重构:` (refactor), `清理:` (chore),
  `版本:` (version) — one line saying what was done.

## One-time environment setup

1. Install the GitHub CLI (`gh`).
2. Create a classic PAT (<https://github.com/settings/tokens>) with:

   | Scope | Purpose |
   |---|---|
   | `repo` | issue/PR/milestone/label/release read-write |
   | `read:org` | `gh` login minimum |
   | `admin:public_key` | SSH key upload during login (optional) |
   | `gist` | `gh` login minimum |
   | `project` / `read:project` | Project V2 read-write (GraphQL) |

3. Log in (the interactive device flow needs `github.com`; the token
   paste flow only needs `api.github.com`):

   ```sh
   gh auth login                    # or:
   gh auth login --with-token       # reads the token from stdin
   gh auth status                   # verify
   ```

## Reproduction commands

`$R` below is the repo full name (`LiRvs-Miria/symrepl`). Commands are
idempotency-oriented — re-running them 422s on existing resources;
skip those.

### Labels

```sh
R=LiRvs-Miria/symrepl
gh api repos/$R/labels -f name=replay -f color=1d76db -f description="M1 replay path: ktest parsing, LLDB driver, branch tracing"
gh api repos/$R/labels -f name=dap    -f color=5319e7 -f description="M2 DAP server: framing/server/service/session and the VS Code extension"
gh api repos/$R/labels -f name=ktest  -f color=c5def5 -f description=".ktest format and parser"
gh api repos/$R/labels -f name=vscode -f color=0e8a16 -f description="VS Code integration (zero-code extension, launch schema)"
gh api repos/$R/labels -f name=tests  -f color=fbca04 -f description="Tests and acceptance scripts"
```

### Milestones

```sh
gh api repos/$R/milestones -f title="M1 — ktest replay (MVP)" -f state=closed -f description="…"
gh api repos/$R/milestones -f title="M2 — DAP server"         -f state=closed -f description="…"
gh api repos/$R/milestones -f title="M3 — LLVM IR / DWARF mapping"          -f description="…"
gh api repos/$R/milestones -f title="M4 — analysis overlays"                -f description="…"
```

### Project V2 (GraphQL; requires the `project` scope)

```sh
OWNER_ID=$(gh api user --jq .node_id)
REPO_ID=$(gh api repos/$R --jq .node_id)
PROJ=$(gh api graphql -f query='mutation($o:ID!){createProjectV2(input:{ownerId:$o,title:"symrepl 路线图"}){projectV2{id}}}' -f o=$OWNER_ID --jq '.data.createProjectV2.projectV2.id')
gh api graphql -f query='mutation($p:ID!,$r:ID!){linkProjectV2ToRepository(input:{projectId:$p,repositoryId:$r}){repository{id}}}' -f p=$PROJ -f r=$REPO_ID
```

### Issues: create → attach as sub-issue → add to board

```sh
NUM=$(gh api repos/$R/issues --input - <<'EOF' --jq .number
{"title":"…","body":"…","milestone":3,"labels":["replay"]}
EOF
)
ID=$(gh api repos/$R/issues/$NUM --jq .id)
gh api repos/$R/issues/11/sub_issues -f sub_issue_id=$ID      # attach to parent
NODE=$(gh api repos/$R/issues/$NUM --jq .node_id)
gh api graphql -f query='mutation($p:ID!,$c:ID!){addProjectV2ItemById(input:{projectId:$p,contentId:$c}){item{id}}}' -f p=$PROJ -f c=$NODE
```

Retroactive close-out (historical milestones): after creating, run
`gh api -X PATCH repos/$R/issues/$NUM --input - <<< '{"state":"closed"}'`.

## Operating procedures

### Starting a milestone (M3 as the example)

1. Design fork → file the ADR issue first (`documentation` label,
   stating explicitly what it blocks).
2. Create the parent issue (assigned to the milestone); slice issues
   hang below it as sub-issues in dependency order.
3. Add all slices to the board; note which slices can start
   immediately without being blocked by the ADR.
4. Detail the slices in roadmap.md (en/zh; may share the commit).

### Day to day

- Drag board items to In Progress when starting; close the issue when
  done (the progress bar follows).
- Slices advance in implementation/wiring/tests commits; push promptly
  after a slice lands.
- CI/acceptance: `pytest` green plus the relevant acceptance script
  before closing a slice.

### Completing a milestone (release flow)

```sh
# 1. Retroactive close-out: create + close issues for commit-delivered
#    work (noting delivering commits and the version)
# 2. Version commit (mirrors M1/M2: pyproject.toml only)
sed -i 's/^version = "0\.2\.0"/version = "0.3.0"/' pyproject.toml
git commit -am "版本: 0.3.0——M3（DWARF 映射）完成"
# 3. Annotated tag (format per v0.1.0/v0.2.0: 'Mx 完成：<content>（<branch>）')
git tag -a v0.3.0 -m "M3 完成：LLVM IR / DWARF 映射（feat/m3-dwarf-mapping）"
git push origin main --follow-tags
# 4. Release (notes carry highlights/quality/docs/limitations + Full Changelog)
gh release create v0.3.0 --title "v0.3.0 — M3（DWARF 映射）完成" --notes-file notes.md
# 5. Mark roadmap.md (en/zh) complete; close the milestone
gh api -X PATCH repos/$R/milestones/3 -f state=closed
```

## Appendix: current configuration (2026-10-09)

- Milestones: #1 M1 (closed), #2 M2 (closed), #3 M3 (open), #4 M4 (open)
- M3 structure: parent #11; slices #13 (ADR-0004), #14–#18 (M3-a…e)
- Board: <https://github.com/users/LiRvs-Miria/projects/3>
- Releases: v0.1.0, v0.2.0 (Latest)
