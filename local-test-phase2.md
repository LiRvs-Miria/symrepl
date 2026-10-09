# M2 DAP 链路本地测试指南（阶段二）

目标：通过"手写最小客户端 → 黑盒实测 → 先验对账"厘清 M2（DAP server 模式）的会话逻辑。
方法：承重件流程——**先写下预测，再动手实测，最后才读源码与正式文档**。
本指南只给脚手架，不提前给答案。

> 本文档是本地工作笔记，不属于 `docs/` 正式文档体系（无双语要求）。
> 对账材料（先验写完之前**不要读**）：`docs/en/dap.md`、
> `docs/en/adr/0003-m2-dap-server-architecture.md`、`src/symrepl/dap/` 源码。

## 0. 环境要求

- 同阶段一：Python >= 3.10，`pip install -e .`
- 无需 LLDB / clang / Docker：先做纯协议层黑盒，不涉及目标程序
- 客户端只用标准库（socket / subprocess / json），不装任何东西

## 1. 先验（动手前写下来，每条一句话）

| # | 问题 | 先验（自己填） |
|---|------|---------------|
| P1 | `src/symrepl/dap/` 四个模块 framing / session / service / server：一条消息进来后经过什么顺序？职责边界怎么切？ | |
| P2 | `initialize` 之前 / 之后，server 各允许、拒绝什么？先发 `launch` 再发 `initialize`，协议要求什么？你猜这份实现实际会回什么？ | |
| P3 | `disconnect` 请求 / 对端直接断连 / `shutdown`：进程分别怎么收场？退出码各是什么？ | |

入口观察点（先自己确认）：`symrepl dap --help`；默认走 stdio 还是 `--port`？
（入口分发在 `src/symrepl/cli.py:48`：`dap` 是保留字首词，不动回放路径的 argparse。）

## 2. 启动 server（二选一）

```bash
# A. stdio 直跑（消息走 stdin/stdout）
symrepl dap

# B. 端口模式
symrepl dap --port 4711
# 另一个终端：
nc 127.0.0.1 4711
```

## 3. 手写最小客户端（约 20 行，不要跳过）

DAP 帧格式（公开标准，见 DAP spec）：`Content-Length: <字节数>\r\n\r\n` + JSON body。
请求形如：

```json
{"seq": 1, "type": "request", "command": "initialize",
 "arguments": {"adapterID": "symrepl"}}
```

验收判据：

- [ ] 客户端能发出一条 `initialize` 并**原样打印收到的原始字节**（先看字节，别急着 json 解析）
- [ ] 读回包用与发送对称的分帧逻辑（自己解析 Content-Length）

写不出来再回来要提示；直接找 AI 拿成品 = 白学 framing。

## 4. 黑盒实验（覆盖先验 P1–P3）

每个实验记录三样：发的字节 / 收的字节 / 进程状态（活着？退出码？）。

- [ ] `initialize` 之前直接发 `launch` → 回什么？
- [ ] `initialize` 发两次 → 第二次什么反应？
- [ ] 正常 `disconnect` 后 → 进程退出吗？码？
- [ ] 不发 `disconnect` 直接杀客户端 → server 进程还在吗？
- [ ] `shutdown` 与 `disconnect` 顺序对调 → 差异？

跑完逐条对照 §1 先验，标注：命中 / 偏差 / 未预测到。

## 5. 对账（此时才读）

1. `docs/en/dap.md` → 修正 P1–P3
2. `docs/en/adr/0003-m2-dap-server-architecture.md` → 架构意图（为什么这么切模块）
3. `src/symrepl/dap/` 源码 → 四个模块各用一句话复述职责；§4 的每个观察在源码里找到对应行
4. `tests/` 下 DAP 相关测试（自己找）→ 实现者视角的协议边界

## 6. 验收清单（复述式，全部闭卷）

- [ ] 画出消息流：stdin/socket → 四个模块 → stdout，标出会话状态存放在哪个对象
- [ ] 说出状态机的状态集合与转移条件
- [ ] 说出三种收场方式的差异与各自退出码
- [ ] 5 分钟向同事讲清：为什么需要 Content-Length 分帧（流式字节上怎么界定一条消息）

通过标准：P1–P3 中 ≥2 条命中或偏差能自行解释。
健康信号参考：全对 ≈ 答案泄漏嫌疑，全错 ≈ 回炉重讲；40–80% 命中最理想。
