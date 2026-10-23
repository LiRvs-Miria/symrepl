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
`stackTrace`/`scopes`/`variables`/`continue`/`setBreakpoints`）自回环：
只转发子进程，不迁移适配器状态机——断点可在配置期（标准客户端时序）
与任一停点后设置。能力位已冻结（ADR-0003 决策 4）：
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
  "stopOnEntry": true,
  "scope": "get_sign"
}
```

针对已运行服务器的开发调试，在 launch.json 中改用
`"debugServer": 47113` —— 无需扩展（[`probes/`](../../probes) 里的
探针网正是这样驱动服务器的）；该形态手动起服时带 `--lldb-dir`。

## launch 参数（已冻结，ADR-0003 决策 10）

| 参数           | 类型             | 默认值    | 含义                                             |
| -------------- | ---------------- | --------- | ------------------------------------------------ |
| `ktest`        | string, 必填     | —         | 要回放的 `.ktest`（父进程侧校验）                |
| `target`       | string, 必填     | —         | 被测二进制（以 `-lkleeRuntest` 链接）            |
| `lldbDir`      | string           | boot argv | 含 `lldb`+`lldb-server` 的工具链目录；覆盖       |
|                |                  |           | boot 时给的 `--lldb-dir`                         |
| `stopOnEntry`  | boolean          | `false`   | 在入口点停住                                     |
| `stopOnBranch` | boolean          | `false`   | 在每个被追踪分支停住（否则 M1 平价）             |
| `scope`        | string           | `"module"`| 追踪范围：`"module"` 或函数名列表                |
| `timeout`      | number           | 关闭      | 运行中止秒数（决策 9）                           |

## 分支追踪

`scope` 范围内追踪恒开：被追踪站点记录后自动继续（M1 平价），有序
报告——站点、命中、走向、逐站点摘要——在会话结束时经 `output` 推送
至 Debug Console（纯被动：无请求面）。
`"stopOnBranch": true` 时改为在每个站点拦停（`stopped`，reason
`breakpoint`），实时暴露变量。

## 限制（如实记录）

- 每 server 进程单会话；多会话推迟
- `evaluate`/`completions`（Debug Console REPL）推迟到 M2 之后
- 末次追踪命中的走向不可观测（M1 盲区；M3 解决）
- 追踪是记录路径决策的超集——KLEE fork 约束子集的映射需要
  IR/DWARF（M3）
