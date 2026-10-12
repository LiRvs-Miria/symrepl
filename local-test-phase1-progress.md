# local-test-phase1 推进状态（**已完成** 2026-10-12：§7 验收全过，phase1 关账入本；下一步 local-test-phase2.md，M2 DAP 链路）

> 本文件是**会话进度快照**，供在新环境（VM opencode）恢复阶段一学习流程。
> 配套：[`local-test-phase1.md`](local-test-phase1.md)（任务书）、`../research-survey/plan/ai-independence.md`（承重协议）、`../research-survey/plan/ai-ledger.md`（对账账本，已有 6 条记录：P1–P3、补遗、P4、P5）。
> **恢复方法：先读本文件，再按"下一步"节继续。承重协议必须维持：预测先行，AI 不得代写预测。**

## 已完成

| 步骤 | 状态 | 结果要点 |
|---|---|---|
| §0 环境 | ✅（VM 内已就绪） | Python 3.13 / clang 19.1.7 / lldb 19.1.7；symrepl 装在 venv：`source ~/venvs/symrepl/bin/activate`。10-10 补：lldb Python 绑定因缺无版本 dev 链接导不进，已修 `sudo ln -s liblldb.so.1 /usr/lib/llvm-19/lib/liblldb.so` |
| §1 P1 ktest 布局 | ✅ 已对账 | 先验全错（8 字节对齐 magic、小端），实际：magic 恰 5 字节无对齐、全 u32 **大端**、串为"u32 长度+裸字节"、v≥2 有 symArgvs/symArgvLen 字段。详见账本 2026-10-10 P1 行 |
| §1 P2 符号性在哪层 | ✅ 已对账 | 层选对（运行时层），机制论证错。实锤：`.ll` 里只是普通 `declare`+`call`，语义随链接的运行时实现走（KLEE=符号执行；intrinsics.c=读 ktest 填具体值） |
| §1 P3 喂值通道 | ✅ 已对账 | 先验"调试器注入"**错**。实际：env `KTEST_FILE` 指向 .ktest → 目标进程运行时库自读自填（intrinsics.c:86 起，按名字 strcmp 找对象后 memcpy）；LLDB 只是传感器（断点/采状态/报告），不参与喂值 |
| §2 编译目标二进制 | ✅（10-10 VM 内代做，机械件） | 运行库 + get_sign/two_objs 在 `/tmp/opencode/replay-build/` |
| §3 最小回放 | ✅ 已对账 | get_sign 退出码 **0/1/255**（先验 0/1/1：第三个错在按大端读数据段字节，实为 x86 小端内存映像 → 0x80000000=INT_MIN → -1 → shell 255，详见账本 P4 行）；two_objs exit=1（arr+b 双对象注入正常） |
| §4 完整回放（trace） | ✅ 已对账 | 先验"断点在 make_symbolic/memcpy、状态=符号变量内存值"，实际：**启动前预扫全模块条件分支逐址下断**（86 处）、状态=命中帧 args+locals（GetVariables）、taken/fallthrough 由下停点 PC 推断；trace 为动态超集，KLEE-fork 子集圈定留给 M3。详见账本 P5 行 |
| §5 失败路径抽查 | ✅ | 坏 ktest（截断）与缺失 target 均退 2，符合验收判据 |

对账中沉淀的关键事实（恢复时不需要重推）：

- ktest 对象名 = `klee_make_symbolic` 第三参的 strcmp 查找键（get_sign.c 里是 `"a"`）
- 运行时旁路：`KLEE_RANDOM` 随机填充；`KTEST_FILE` 未设时退化成 stdin 交互
- symrepl 只消费公开 .ktest 格式（ADR 0001 红线），回放阶段 KLEE 本体不参与
- **ktest 大端只管头部 u32 字段；对象数据段 = 产生方内存原始映像，端序归目标机解释**（P4 教训）
- trace 采样点 = 条件分支指令（决策点粒度），状态 = 命中帧变量快照；动态 trace ⊇ KLEE 路径决策（含运行时噪声分支）（P5 教训）

## §7 验收复述（2026-10-12 全过，终判）

| 项 | 状态 | 详情 |
|---|---|---|
| a. ktest 布局 | ✅ | 端序关键句补齐：大端只管头部 u32 字段（每字段 **4 字节**，u32=32位），对象数据段=产生方内存原始映像，端序归目标机解释（P4 教训）；magic 5 字节 4b 54 45 53 54；串编码=u32 长度前缀+裸字节（ktest.py:110-112，"a"=`00 00 00 01 61`，Pascal 制无 \0）——字段细节按"可查不必记"降级，但 u32 单位错已当场纠正 |
| b. make_symbolic 回放期 | ✅ | 改口完成："KTEST=从解集采的具体特解"=具体样本值；因果补齐：特解以路径约束为条件**求出**，非碰巧满足——回放=兑现 KLEE 执行期算出的答案 |
| c. 分支报告+driver.py | ✅ | 骨架三问+事件循环三轮对账两轮复述全过（过程详账本 2026-10-12 行）：守卫双 __name__/spawn 隔离契约/入口停双理由/条件跳转采样/PC 序列即走向载体/收尾六步（GetState 定案→exit_code→销毁→infer_outcomes→Hit→JSON，render 在父进程 cli.py:146） |

**phase1 关账。下一步：`local-test-phase2.md`（M2 DAP 链路），承重协议照旧：预测先行，AI 不代写先验。**

**§7 对账过程存档（复习用，无待办）：**

1. 骨架三问首轮 0/3（父进程守卫方向反/答非所问/size 语义错）→ 两轮复述过：守卫双 `__name__`、spawn 隔离契约（LLDB host 复用二次 launch 静默 no-op + 超时可强杀）、PIE→入口停唯一解析窗口、条件跳转采样（无条件 jmp 在路径上但不分叉）
2. 事件循环三题：问2 过（PC 序列即走向载体）；问1 四处修正（只认进程事件/terminal 四种/GetState 二次确认防陈旧事件/拿不到栈帧静默跳过）；问3 首轮编造"trace.hit 函数"→ 复述过收尾六步
3. 欠账清偿：magic 5 字节 ✅、b 术语"具体特解"✅、端序后半句"数据段归目标机"✅、串编码答疑钉死（u32 长度前缀+裸字节）；u32 单位错（32字节→32位=4字节）当场纠正
4. 本轮最重教训（详账本 2026-10-12 行）：编造 API 名须以实码为准；对账后的"显然"属后见之明不计入理解

## 下一步（原冻结点，已被 §7 状态取代，留档）

1. **§7 验收清单逐项打勾**：前两项（ktest 布局、make_symbolic 回放期行为）与"能看懂分支报告并定位 driver.py 事件循环"由用户口头复述确认 → 通过后进入 `local-test-phase2.md`（M2 DAP 链路）

## 协议约束（对新会话的 AI）

- 每个预测点必须由**用户本人**先写先验，AI 只在对账阶段提供材料与证据，不得提前给答案
- 每完成一个对账点，向 `../research-survey/plan/ai-ledger.md` 追加一行（格式照既有条目）
- 机械件（编译、跑命令）可直接代做；承重件解释权留给用户
