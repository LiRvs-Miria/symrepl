# ADR-0004 — M3：路径缩小——以 LLVM IR 符号性分析圈定 KLEE-fork 子集

日期：2026-10-23　　状态：已接受（用户裁决 2026-10-23；取代 M2 限制清单里
"KLEE-fork 子集映射押后至 M3"的注记）

English version is authoritative. English see
[en/adr/0004-m3-path-narrowing.md](../../en/adr/0004-m3-path-narrowing.md)。

## 背景

`branchTrace` 记录 inferior 在 scope 内执行的**每一条**条件分支——动态
**超集**。探针观察（phase1，P5，2026-10-10）表明噪声是结构性的：

- **回放运行时独有代码**：`kleeRuntest` 的 `kTest_fromFile` 读取、对象名
  `strcmp` 循环——这些代码在 KLEE 的世界里**根本不存在**（KLEE 的
  `klee_make_symbolic` 只标记内存，什么都不读）。
- **具体条件分支**：栈检查、循环边界——两个世界都执行，但 KLEE 不在此分叉。

而回放语义——*"沿 KLEE 采样到的路径重放"*——在产品里没有任何位置可观测：
报告把路径决策和运行时噪声混在一起。

两个事实限定了解空间：

1. **KLEE 的 fork 定义就在 LLVM bitcode 层**：条件（传递地）依赖
   `klee_make_symbolic` 所写内存的 `br i1` 会分叉——两个后继都执行，`.ktest`
   是其中一支的具体采样。"分支是否符号"是**程序性质**，可从 `.ll` 静态
   恢复——公开的 `clang -emit-llvm` 产物，不需要 KLEE 就能得到。
2. **ADR-0001 防火墙**：KLEE 本体永不进回放链路，进链路的只有公开产物。
   `.ll` 满足此条；本层不需要安装 KLEE。

分层能力模型（用户裁决，2026-10-23）划定范围：

| 层 | 能力 | 用户得到 | 状态 |
|---|---|---|---|
| M1+M2 | 实际路径记录+回放 | trace 超集 | 已发布 |
| **M3** | **LLVM IR 侧缩小路径可能性** | **清晰的路径** | 本 ADR |
| M4 | 符号↔实际完整校验（`.kquery`） | 保真度结论 | ADR-0005 |

前层是后层的必要条件；后层可选。

## 裁决

1. **范围**：M3 = `.ll` 静态符号性分析（方法 1）+ 手工验收真值（方法 3）。
   `.kquery` 路线（方法 2）整体归 M4（ADR-0005）。
2. **三分类**（缩小的本体）：
   - **fork**——分支条件 ∈ 符号闭包；KLEE 在此分叉（`.ktest` 采样了其中一个
     方向）。
   - **噪声A（运行时独有）**——站点所在函数不在 `.ll` 模块：KLEE 世界不存在的
     代码。判据自然涌现：`get_sign.ll` 不含 `intrinsics.c`。
   - **噪声B（具体条件）**——条件在符号闭包之外。
3. **运行时面**：可选 launch 参数 `ir`（.ll 路径）。缺席=现状，零增帧
   （M1+M2 层）；在场=trace 报告获得注记层：节点携 `kind: "fork" | "runtime"`
   加 `detail` 携判据（`symbolic-branch` / `function-not-in-module` /
   `concrete-condition`——A/B 细分住 `detail`，`kind` 保持二值）。坏 `.ll` →
   launch 期 `Rejected`（ktest refused 家法 fail-fast）。
4. **机制**：自写轻量 `.ll` 解析器+数据流——`klee_make_symbolic` 调用点标记
   内存；经 `load`/`icmp` 与直接调用实参→形参绑定传播；`br` 条件测试闭包
   成员。replay/ 册内新纯件（暂名 `pathmark`），纯核可直测（家法）。行级 join
   经 `.ll` `!DILocation` ↔ native DWARF 行/函数——**两次编译，指令级 join
   由构造排除**。
5. **决策走向（taken/not-taken）M3 v1 不落**——站序隐含；M4 需要时显式落
   （撞到即裁）。
6. **验收真值**（手工先行）：get_sign——test1（a=0，exit 0）：fork=[L11]；
   test2（a=0x01010101，exit 1）与 test3（a=0x80000000，exit 255）：fork=
   [L11, L13]；`intrinsics.c` 全部为噪声A；入口停不是决策。

## 后果

- 报告读者看到塌缩后的路径；M4 拿到输入形状。
- 零运行时依赖保持——运行时无 KLEE、无 LLVM 工具链；`.ll` 以用户提供的公开
  产物进入。
- 轻量数据流有已知边界（指针别名、间接调用、数组传播）——真撞到再扩，不预铺。
- 注记是派生层：零处理红线不碰（记录照旧据实；注记从 `.ll` 算出）。
- M3 回答程序性质（静态）；M4 将回答执行事实（动态）——两层互证（多网交叉
  家法同构）。
