# 项目管理指南

English: [en/project-management.md](../en/project-management.md) ｜ 简体中文（本页）

> 本页记录 symrepl 的项目管理结构与操作流程：在任何有 `gh` CLI 与相应
> token 的环境里都能从零复现整套配置，或继续日常运作。英文版为准；
> 两版同提交同步。

## 四层结构

| 层 | 载体 | 粒度 | 现状 |
|---|---|---|---|
| 里程碑 | GitHub Milestones | 版本级（M0–M4，对应 roadmap） | M1/M2 closed（100%），M3/M4 open |
| 领域标签 | GitHub Labels | 单个 issue/PR 的领域分类 | `replay` `dap` `ktest` `vscode` `tests` + 默认类型标签 |
| 看板 | Project V2「symrepl 路线图」 | 跨里程碑的条目视图（Todo/In Progress/Done） | 已关联仓库，M3 各切片在列 |
| 发布 | Tag（注解）+ Release | 版本交付物与发布说明 | `v0.1.0`、`v0.2.0` 双 Release |

分工原则：**里程碑**管"什么时候算完成"，**看板**管"现在做到哪了"，
**标签**管"这是哪一块的事"，**Release** 管"交付了什么"。

## 约定（与仓库既有实践一致）

- **里程碑切片命名**：`Mx-a`、`Mx-b`……每个切片是一个可独立完成的块
  （约一个周末深度块），提交按 实现 → 接线 → 测试 三段式推进。
- **ADR 先行**：有方案分歧的切片（如 M3 的输入依赖识别方法）先冻结
  ADR，再动实现；ADR issue 在里程碑里显式阻塞依赖它的切片。
- **父 issue + sub-issues**：每个里程碑一个父 issue（编号对应 roadmap
  章节），切片作为 sub-issues 挂在其下、按依赖顺序排列。
- **进度语义**：GitHub 里程碑进度条只统计关联的 issue/PR。历史里程碑
  （工作以直接提交完成、无 issue）用**追溯 issue** 闭环留档——见 M1
  （#1–#4）与 M2（#5–#10）先例，每条注明交付提交与完成版本。
- **文档双语**：`docs/en/` 为权威版，`docs/zh-CN/` 镜像，同提交同步。
- **提交信息前缀**：`实现:` `接线:` `测试:` `文档:` `重构:` `清理:`
  `版本:`，一句话说清做了什么。

## 一次性环境准备

1. 安装 GitHub CLI（`gh`）。
2. 创建 classic PAT（<https://github.com/settings/tokens>），作用域：

   | 作用域 | 用途 |
   |---|---|
   | `repo` | issue/PR/milestone/label/release 读写 |
   | `read:org` | `gh` 登录最低要求 |
   | `admin:public_key` | `gh` 登录时上传 SSH 公钥（可省） |
   | `gist` | `gh` 登录最低要求 |
   | `project` / `read:project` | Project V2 读写（GraphQL） |

3. 登录（交互式设备码流程需要能访问 `github.com`；token 粘贴流程只
   需要 `api.github.com`）：

   ```sh
   gh auth login                    # 或：
   gh auth login --with-token       # 从 stdin 读 token
   gh auth status                   # 验证
   ```

## 可复现命令

以下 `$R` 为仓库全名（`LiRvs-Miria/symrepl`）。命令均为幂等导向——
重复执行时已存在的资源会报 422，跳过即可。

### 标签

```sh
R=LiRvs-Miria/symrepl
gh api repos/$R/labels -f name=replay -f color=1d76db -f description="M1 回放路径：ktest 解析、LLDB 驱动、分支追踪"
gh api repos/$R/labels -f name=dap    -f color=5319e7 -f description="M2 DAP server：framing/server/service/session 与 VS Code 扩展"
gh api repos/$R/labels -f name=ktest  -f color=c5def5 -f description=".ktest 格式与解析器"
gh api repos/$R/labels -f name=vscode -f color=0e8a16 -f description="VS Code 集成（零代码扩展、launch schema）"
gh api repos/$R/labels -f name=tests  -f color=fbca04 -f description="测试与验收脚本"
```

### 里程碑

```sh
gh api repos/$R/milestones -f title="M1 — ktest 回放（MVP）" -f state=closed -f description="…"
gh api repos/$R/milestones -f title="M2 — DAP server"       -f state=closed -f description="…"
gh api repos/$R/milestones -f title="M3 — LLVM IR / DWARF 映射"              -f description="…"
gh api repos/$R/milestones -f title="M4 — 分析叠加层"                        -f description="…"
```

### Project V2（GraphQL；需要 `project` 作用域）

```sh
OWNER_ID=$(gh api user --jq .node_id)
REPO_ID=$(gh api repos/$R --jq .node_id)
PROJ=$(gh api graphql -f query='mutation($o:ID!){createProjectV2(input:{ownerId:$o,title:"symrepl 路线图"}){projectV2{id}}}' -f o=$OWNER_ID --jq '.data.createProjectV2.projectV2.id')
gh api graphql -f query='mutation($p:ID!,$r:ID!){linkProjectV2ToRepository(input:{projectId:$p,repositoryId:$r}){repository{id}}}' -f p=$PROJ -f r=$REPO_ID
```

### Issue：创建 → 挂 sub-issue → 进看板

```sh
NUM=$(gh api repos/$R/issues --input - <<'EOF' --jq .number
{"title":"…","body":"…","milestone":3,"labels":["replay"]}
EOF
)
ID=$(gh api repos/$R/issues/$NUM --jq .id)
gh api repos/$R/issues/11/sub_issues -f sub_issue_id=$ID      # 挂到父 issue
NODE=$(gh api repos/$R/issues/$NUM --jq .node_id)
gh api graphql -f query='mutation($p:ID!,$c:ID!){addProjectV2ItemById(input:{projectId:$p,contentId:$c}){item{id}}}' -f p=$PROJ -f c=$NODE
```

追溯闭环（历史里程碑）：创建后 `gh api -X PATCH repos/$R/issues/$NUM
--input - <<< '{"state":"closed"}'`。

## 日常流程

### 开新里程碑（以 M3 为例）

1. 有方案分歧 → 先立 ADR issue（`documentation` 标签，显式声明阻塞谁）。
2. 建父 issue（关联里程碑），切片 issue 作为 sub-issues 按依赖序挂入。
3. 切片全部加入看板；可立即开工、不被 ADR 阻塞的切片注明。
4. roadmap.md（en/zh）细化切片（可与上一步同提交）。

### 日常运作

- 开工把看板条目拖到 In Progress；完成即关 issue（进度条自动走）。
- 切片按 实现/接线/测试 分提交；A 主要功能切片合并后及时 push。
- CI/验收：`pytest` 全绿 + 对应 acceptance 脚本通过才关切片。

### 完成里程碑（发布流程）

```sh
# 1. 追溯闭环：为无 issue 的交付补建 issue 并关闭（注明交付提交与版本）
# 2. 版本提交（照 M1/M2 先例，只动 pyproject.toml）
sed -i 's/^version = "0\.2\.0"/version = "0.3.0"/' pyproject.toml
git commit -am "版本: 0.3.0——M3（DWARF 映射）完成"
# 3. 注解 tag（格式见 v0.1.0/v0.2.0：'Mx 完成：<内容>（<分支>）'）
git tag -a v0.3.0 -m "M3 完成：LLVM IR / DWARF 映射（feat/m3-dwarf-mapping）"
git push origin main --follow-tags
# 4. Release（notes 含亮点/质量/文档/已知限制 + Full Changelog）
gh release create v0.3.0 --title "v0.3.0 — M3（DWARF 映射）完成" --notes-file notes.md
# 5. roadmap.md（en/zh）标记完成；里程碑置 closed
gh api -X PATCH repos/$R/milestones/3 -f state=closed
```

## 附录：当前配置清单（2026-10-09）

- 里程碑：#1 M1（closed）、#2 M2（closed）、#3 M3（open）、#4 M4（open）
- M3 结构：父 issue #11，切片 #13（ADR-0004）、#14–#18（M3-a…e）
- 看板：<https://github.com/users/LiRvs-Miria/projects/3>
- Release：v0.1.0、v0.2.0（Latest）
