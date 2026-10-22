# ADR-0003 — M2 架构：父子拓扑之上的 DAP server

日期：2026-10-04　　状态：已接受（2026-10-04 架构评审；评审发现已并入；2026-10-05 编辑性修订——决策 7 的轮询原语按 M2-a spike 结论修正，决策不变）

英文版为准（authoritative）。English version:
[en/adr/0003-m2-dap-server-architecture.md](../en/adr/0003-m2-dap-server-architecture.md)。

## 背景

M1 交付了一次性回放：`symrepl <ktest> --target <bin>` 拉起一个用完即弃的
子进程（`python -m symrepl.replay.driver`），持有 SBDebugger 与 inferior，
跑至退出并返回 JSON 结果。M1 的三个事实约束 M2：

1. LLDB 内嵌全局状态在常驻宿主中是脆弱的（`SBDebugger.Terminate()` 之后
   重新初始化会导致启动失败；同步 `Continue()` 曾永久挂起；脚本回调静默
   从不执行）——因此有了用完即弃的子进程隔离，M2 必须保留它。
2. harness 契约（kleeRuntest）与分支追踪（逐站点断点、会话侧记录）是
   会话语义，任何交互模式都必须**复现**而非取代它们。
3. 仓库运行时零依赖，且 M1 子进程已把 inferior 的 stdio 重定向到文件。

M2 的目标（见 roadmap）：回放会话可被 VS Code 与任何 DAP 客户端使用。
对照 DAP 规范、`lldb-dap`、`microsoft/debugpy` 与 `vscode-mock-debug`
的调研（2026-10-04）确认：

- 线协议是 `Content-Length` 帧化的 UTF-8 JSON，stdio 与 TCP 两种传输
  完全一致；信封为 request/response/event，每 actor 自 `seq=1` 起。
- 生命周期：`initialize` → 能力响应 → `initialized` 事件 →
  `setBreakpoints`/… → `configurationDone`；`launch` 的响应必须扣留到
  `configurationDone` 应答之后。
- `threads` 即使单线程目标也必须实现。
- 小型 adapter（`vscode-mock-debug`）实现约 25 个请求；stepping
  （`next/stepIn/stepOut/pause`）属基线，且结果通过
  `stopped(reason, …)` 事件表达，而非请求响应。
- VS Code 需要一个 `debuggers` 贡献点（扩展）才能提供调试类型，但
  **零代码的声明式 `package.json` 即可**；`"debugServer": N` 可连接
  已运行的 adapter，无需任何扩展。
- stdio 卫生是强制项：debugpy 在 stdio 模式启动即把 `sys.stderr` 置空；
  lldb-dap 有专门的输出重定向器——协议通道必须独占。
- 先例的进程模型：debugpy 把 adapter / launcher / debug server 拆成
  多进程、每组件一条 JSON 通道；lldb-dap 每会话独立 runloop +
  专用 LLDB 事件线程，并在 continue 时清空变量引用表。

## 决策

1. **拓扑**：DAP server 是 `symrepl` 父进程；每个调试会话对应一个
   **常驻子进程**（`python -m symrepl.dap.service`），在会话生命周期内
   持有 SBDebugger 与 inferior，随会话销毁。父进程对客户端说 DAP；
   父子之间说内部"请求/响应 + 事件"协议。
2. **内部协议**：`Content-Length` 帧化的 UTF-8 JSON——与 DAP 共用同一个
   framing 模块——走子进程的 stdin/stdout。规则：`hello` 消息携带协议
   **版本**（廉价保险；roadmap 中的 ptrfuzz 集成未来可能驱动这条通道）；
   每个响应带统一的 **ok/error 信封**；`Content-Length` 设上限
   （64 MiB）且按**编码后字节数**计；**畸形帧直接杀会话**（不做重同步）。
3. **传输**：默认 stdio 单会话；`symrepl dap --port N` 监听单个客户端
   连接。单进程多会话推迟。
4. **DAP 子集**（能力位诚实声明，对齐 mock-debug）：`initialize`、
   `launch`、`setBreakpoints`、`setExceptionBreakpoints`、
   `configurationDone`、`threads`、`stackTrace`、`scopes`、`variables`、
   `continue`、`disconnect`、`terminate`，以及 stepping（`next`、
   `stepIn`、`stepOut`、`pause`）。`evaluate` 与 `completions` 推迟到
   M2 之后。`threads` 返回单线程。`launch` 响应扣留至
   `configurationDone`（永不发它的坏客户端由决策 10 的超时规则兜底）。
   **事件词汇**：`initialized`、`process`、`thread`、`stopped`、
   `exited`、`terminated`、`output`。**能力位现在冻结**：
   `supportsConfigurationDoneRequest: true`（扣留 launch 的设计依赖
   它）、`supportsTerminateRequest: true`；`supportsTerminateDebuggee`
   **不声明**（决策 8）。**顺序不变量**：响应与事件共用同一条输出
   队列、经单写者写出，`continue` 的响应永远先于其引发的
   `stopped` 事件。
5. **分支追踪 UX**：默认与 M1 对齐——分支站点断点自动继续，有序报告经
   自定义请求 `replay.branchTrace` 获取（自定义命令用 `replay.*` 前缀；
   未知命令按规范优雅失败）。`launch` 参数 `"stopOnBranch": true` 时
   改为在每个站点拦停（`stopped`，reason `breakpoint`），实时暴露变量。
   由于零代码扩展无法发起自定义请求，会话结束时会把
   `TraceReport.render()` 经 **`output` 事件推入 Debug Console**——
   默认的 VS Code 体验不是黑洞。
6. **Stepping**：`next`/`stepIn`/`stepOut`/`pause` 映射到 SBThread 步进
   操作（`pause` 用 `SendAsyncInterrupt`）；完成以
   `stopped(reason: "step")` 表达。
7. **并发与线程归属**：线程 + 队列 + 每通道单写者（读线程永不写）；
   不上 asyncio（SB API 是阻塞式 C 形态接口）。**子进程内由唯一的
   owner 线程执行全部 SB API 调用**：LLDB 事件线程以 `GetNextEvent`
   加短暂 sleep 短轮询，轮询间隙让出处理经队列注入的内部命令
   （2026-10-05 编辑性修订：Python 的 `WaitForEvent` 绑定只接受整秒数，
   亚秒级 `WaitForEvent` 轮询无法表达）；`pause` 用
   `process.SendAsyncInterrupt()`。父进程跑客户端读线程与子进程事件
   读线程，由主循环多路复用。**M2-a spike 已完成**（2026-10-05，
   LLDB 22.1.8）：service 形态子进程内 `SendAsyncInterrupt` 验证通过
   ——两次中断都产生真实停止（停止事件送达会话 listener、PC 落在
   target 内、可恢复、可重复），并确立两条服务循环附加规则：每次恢复
   运行前必须先排干队列中的陈旧事件（如 entry stop）；启动前须经 M1
   的 `_ensure_debug_server()` 辅助定位 `lldb-server`。
8. **生命周期与故障语义**（关机阶梯）：
   - 子进程监视自身 **stdin**：EOF 即杀死 inferior 并退出——这是对
     父进程被 SIGKILL 也可靠的唯一信号。
   - 子进程会话中途死亡（stdout EOF / 非零退出）：父进程将其翻译为
     DAP `terminated` 事件、fail 全部在途内部请求并清理会话。
   - `disconnect`/`terminate`（及客户端传输 EOF）走升级阶梯：
     SIGTERM → 2–5 秒宽限 → SIGKILL → `waitpid`。优雅退出从不假设
     可依赖（M1 证据）。
   - `disconnect`/`terminate` **一律杀死** inferior 与子进程（回放
     场景没有值得 detach 留活的东西）；`supportsTerminateDebuggee`
     保持不声明。
9. **交互超时**：交互式会话**没有 DAP 级全局超时**——用户可以对着
   停住的进程看任意久。事件等待仅用短轮询让出 CPU 并检查关停标志。
   M1 的硬超时默认只保留在一次性 CLI 路径。
10. **launch 参数 schema 现在冻结**（名字会泄漏进用户 `launch.json`
    与扩展 manifest）：`ktest`（必填）、`target`（必填）、
    `stopOnBranch`（bool，默认 `false`）、`stopOnEntry`（bool，默认
    `false`）、`scope`（字符串，默认 `"module"`）、`timeout`（数值，
    可选——按决策 9 默认关闭）。
11. **ktest 注入**：父进程用 `symrepl.ktest` 校验用户的 ktest，子进程
    把 `KTEST_FILE` 设为**用户原始路径**——M1 的临时文件重序列化及其
    跨进程生命周期管理废除。
12. **状态引用**：变量引用与栈帧 id 采用按停止点的**代际 id**；任何
    恢复运行都使当前代失效，陈旧引用返回错误响应（DAP adapter 最经典
    的坑， upfront 立约）。
13. **背压**：`output` 事件**可丢弃**（有界队列 + 截断标记）；
    控制事件（`stopped`/`terminated`/`exited`）**绝不丢弃**。两类
    失败语义相反，这条区分是显式立约的。
14. **VS Code 集成**：M2 末期交付零代码声明式扩展
    （`contributes.debuggers`，type `symrepl`，经 `program` 指向
    adapter 脚本）。开发与测试走 `"debugServer"` / 裸 socket 客户端，
    因此任何非 VS Code 的 DAP 客户端都无需扩展。Linux 优先：Windows
    的 adapter 启动器方案延后文档化。
15. **无 LLDB 测试**：parent 的 DAP 逻辑对着一个说内部协议的 **mock
    child** 测试；framing 层有纯单元测试（拆帧/粘包、编码字节长度、
    一次读取两帧）。手写约 40 行的 Python DAP 测试客户端（仅 dev
    依赖）以 socket 模式驱动集成测试。M2-e 追加混沌测试（子进程中途
    崩溃、畸形帧）与 acceptance 脚本风格的 LLDB 冒烟路径。
16. **否决的备选**：基于 `lldb-dap` 二次开发。它是通用 C++ adapter：
    ktest 注入、harness 校验、分支站点语义都无法内嵌，且其进程模型与
    子进程隔离契约冲突。仅作为协议映射的参考实现。

## 后果

- VS Code 与任何 DAP 客户端都能驱动回放会话；symrepl 获得交互式调试
  界面而**零新增依赖**（framing 约 40 行标准库代码）。
- M1 的隔离契约逐字保留：每会话一个用完即弃的子进程；LLDB 永不在
  常驻父进程内运行。子进程的生命周期语义（stdin-EOF 自杀、关机阶梯）
  补上了"父进程死亡"与"会话中途崩溃"这两个一次性模型不存在的缺口。
- M1 的语义——kleeRuntest 契约下的 ktest 注入、harness 违约呈现、
  带 `not observed` 诚实标注的有序分支追踪——原样沿用。DAP 只是新增
  传输层，不新增语义。
- 父子内部协议是私有的，但现在带**版本化握手**：roadmap 中的 ptrfuzz
  集成可以有意识地绑定它，而不是碰巧绑上。
- 局限（如实记录）：每 server 进程单会话；`evaluate`/completions
  （调试控制台 REPL）推迟；分支站点追踪对最后一击的走向依旧不可观测
  （M1 盲区，M3 解决）；多会话与 `runInTerminal` 推迟；Windows 的
  adapter 启动器延后（Linux 优先）。
- M2 切片见 roadmap：M2-a（骨架 + 生命周期语义 + interrupt spike）、
  M2-b1（DAP 生命周期 + threads/stackTrace/continue + 测试客户端）、
  M2-b2（scopes/variables + 源码断点）、M2-c（分支追踪上 DAP）、
  M2-d（stepping）、M2-e（扩展、文档、验收场景、混沌测试）。

## 修订（2026-10-22）：cleanroom 实现转正

原 M2-a 实现（`symrepl.dap.server`/`service`/`session`/`framing`）由
cleanroom 重写整体替代，按归一 B 案**原地转正**于 `local-cleanroom/server/`：
DAP 面以 checkout 内脚本形态存在，不是安装包。上文的裁决（拓扑、内部协议、
传输、DAP 子集）原样延续；文中模块路径指原始实现。现接线：`symrepl dap`
以子进程方式派发 `local-cleanroom/server/schedule.py`（退出码透传）；
父进程=`schedule.py`，每会话子进程=`session.py`。DAP 面的包化押后，
待真实消费方（如 ptrfuzz）需要 import 时再做。
