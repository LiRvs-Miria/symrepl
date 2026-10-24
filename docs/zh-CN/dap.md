# DAP server 模式（M2）

symrepl 内置调试适配协议（DAP）服务器，回放会话可由 VS Code 与任意
DAP 客户端驱动（ADR-0003）。运行时零依赖：分帧用标准库，传输为
stdio 或单条 TCP 连接。

## 运行

    python3 src/server/schedule.py --stdio            # stdio —— 单客户端、单会话
    python3 src/server/schedule.py --port 47113 --lldb-dir /usr/lib/llvm-22/bin
    # （见 `python3 src/server/schedule.py --help`；--lldb-dir = 工具链目录，
    #  boot 可选——launch 参数 `lldbDir` 可覆盖；两处皆缺=launch 期干净拒绝）

生命周期：`initialize` → `launch`（响应扣留）→ `initialized` 事件 →
`setBreakpoints`/… → `configurationDone` → launch 响应 →
`stopped`/`exited`/`terminated` 事件。调试器层命令（`threads`/
`stackTrace`/`scopes`/`variables`/`continue`/`setBreakpoints`/`next`/
`stepIn`/`stepOut`）自回环：只转发子进程，不迁移适配器状态机——断点
可在配置期（标准客户端时序）与任一停点后设置；步进为同步阻塞（停相
在步进响应之后经事件泵到达，reason `step`）。能力位已冻结（ADR-0003 决策 4）：
`supportsConfigurationDoneRequest` 与 `supportsTerminateRequest`；
`evaluate`/`completions` 推迟到 M2 之后。

## VS Code

安装 [`vscode/`](../../vscode) 中的零代码扩展（声明式
`contributes.debuggers`，Linux 优先——ADR-0003 决策 14），然后：

```json
{
  "type": "symrepl",
  "request": "launch",
  "name": "replay get_sign",
  "ktest": "third_party/klee/ktest/get_sign_test000002.ktest",
  "target": "${workspaceFolder}/get_sign",
  "stopOnEntry": true,
  "scope": "get_sign"
}
```

```json
{
  "type": "symrepl",
  "request": "launch",
  "name": "replay get_sign",
  "ktest": "third_party/klee/ktest/get_sign_test000002.ktest",
  "target": "${workspaceFolder}/get_sign",
  "lldbDir": "/usr/lib/llvm-22/bin",
  "scope": "get_sign"
}
```

针对已运行服务器的开发调试，在 launch.json 中改用
`"debugServer": 47113` —— 无需扩展（[`probes/`](../../probes) 里的
探针网正是这样驱动服务器的）；该形态手动起服时带 `--lldb-dir`。

## launch 参数

核心（`ktest`/`target`）按 ADR-0003 决策 10 冻结；扩展（`lldbDir`、
`scope`、`ir`）走同一校验家法，各自随探针网落地。

| 参数           | 类型             | 默认值    | 含义                                             |
| -------------- | ---------------- | --------- | ------------------------------------------------ |
| `ktest`        | string, 必填     | —         | 要回放的 `.ktest`（父进程侧解析，结构化下发）    |
| `target`       | string, 必填     | —         | 被测二进制（链 kleeRuntest 运行时）              |
| `lldbDir`      | string           | boot argv | 含 `lldb`+`lldb-server` 的工具链目录；覆盖       |
|                |                  |           | boot 时给的 `--lldb-dir`                         |
| `scope`        | string           | —         | branch-trace 插桩范围：`"module"` 或逗号分隔     |
|                |                  |           | 函数名；缺席=不插桩、无报告                      |
| `ir`           | string           | —         | 靶子的 `.ll` 路径（`clang -O0 -g -S -emit-llvm`， |
|                |                  |           | M3）；在场=报告节点携 fork/runtime 注记；坏件    |
|                |                  |           | =launch 拒（fail-fast，spawn 前）                |

## 分支追踪

追踪经 `scope` 显式开启：`configurationDone` 时会话自动插桩范围内全部
条件站点（按载入地址设断）；每次命中发 `stopped`（reason `breakpoint`），
有序报告——pc、函数、`file:line:column`、助记符、调用栈、注册符号值与
ktest 字节对账注记——在会话结束时经 `output` 事件推送（纯被动：无请求面）。
入口停不是站点。

带 `ir`（M3，[ADR-0004](adr/0004-m3-path-narrowing.md)）：报告节点额外携
`kind: "fork" | "runtime"` 加 `detail`（`symbolic-branch` /
`function-not-in-module` / `concrete-condition`）——由 `.ll` 文本静态算出
的 fork 子集。无 `ir` 则零注记字段（M1+M2 形状不变）。

## 限制（如实记录）

- 每 server 进程单会话；多会话推迟
- `evaluate`/`completions`（Debug Console REPL）按 ADR 推迟
- 末次追踪命中的走向不可观测（站序隐含；ADR-0004 决策 5 定为 M3 non-goal）
- 无 `ir` 时追踪是路径决策的超集；fork/runtime 注记做静态缩小（M3）；
  动态输入依赖（差分回放）归 M4
