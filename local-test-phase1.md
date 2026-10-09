# M1 回放链路本地测试指南（阶段一）

目标：通过"看懂输入 → 编译目标 → 亲手回放"厘清 M1 的完整逻辑。
阅读本指南时请配合源码路径，所有路径均相对仓库根目录。
方法：承重件流程——**预测先行**：每一步先写下自己的猜测，再读材料/跑实验，最后对账。

> 本文档是本地工作笔记，不属于 `docs/` 正式文档体系（无双语要求）。

## 0. 环境要求

- Python >= 3.10（项目无运行时第三方依赖）
- `clang`、`clang++`、`lldb`（本机已确认可用）
- **不需要安装 KLEE**——回放阶段 KLEE 本身不参与
- 可选：Docker（仅重新生成 .ktest 时需要）

安装 symrepl：

```bash
pip install -e .
# 之后可用 symrepl 命令；或 python -m symrepl.cli 等价
```

## 1. 预测先行：每样先猜后看（理解"格式 → 契约 → 运行时"）

预测任务（动手前逐条写下，之后逐条对账）：

- **P1 字节布局**：先看原始字节——
  `xxd third_party/klee/ktest/get_sign_test000001.ktest | head -40`，
  猜 .ktest 的布局：magic？版本号？argc/argv 在哪？符号对象怎么排？
- **P2 符号性在哪一层**：源码？LLVM IR？运行时库？一句话理由。
  验证办法：`clang -S -emit-llvm -g tests/fixtures/src/get_sign.c -o /tmp/get_sign.ll`，
  看 IR 里 `klee_make_symbolic` 长什么样；再对照运行时实现，确认语义真正在哪层。
- **P3 喂值通道**：回放时字节怎么被喂进目标进程？环境变量？命令行参数？别的？
  （名字自己去 runtime 实现里找。）

对账材料（预测写完后再读）：

| 顺序 | 材料 | 对账你的哪条预测 |
|------|------|-----------------|
| 1 | `src/symrepl/replay/ktest.py` | P1：布局猜对了吗？ |
| 2 | `tests/fixtures/src/get_sign.c` | `klee_make_symbolic` 契约：哪个变量被声明为符号？ |
| 3 | `third_party/klee/runtime/intrinsics.c`（对照 `KTest.cpp`） | P3：喂值机制 |
| 4 | `/tmp/get_sign.ll`（自己生成） | P2：符号性到底在哪一层？ |

注：symrepl 只消费公开格式 `.ktest`（ADR 0001 范围红线），KLEE 本身不参与回放。

用 Python 亲手解析一个真实 fixture（对账 P1）：

```bash
python -c "
from symrepl.replay.ktest import load
k = load('third_party/klee/ktest/get_sign_test000001.ktest')
print('args:', list(k.args))
for o in k.objects:
    print(f'{o.name}: {len(o.data)} bytes = {o.data.hex()}')
"
```

## 2. 编译目标二进制（唯一的外部依赖准备）

配方照抄 `tests/conftest.py:38-59`（get_sign fixture 的构建方式）：

```bash
BUILD=/tmp/opencode/replay-build
ROOT=$PWD
INC=$ROOT/third_party/klee/runtime/include
mkdir -p "$BUILD" && cd "$BUILD"

# 1) 编译 vendored kleeRuntest 运行库
clang   -O0 -g -c "$ROOT/third_party/klee/runtime/intrinsics.c" -I"$INC" -o intrinsics.o
clang++ -O0 -g -std=c++17 -c "$ROOT/third_party/klee/runtime/KTest.cpp" -I"$INC" -o KTest.o

# 2) 编译目标程序并链接运行库（get_sign 与 two_objs 各一个）
clang -O0 -g "$ROOT/tests/fixtures/src/get_sign.c"  -I"$INC" intrinsics.o KTest.o -o get_sign
clang -O0 -g "$ROOT/tests/fixtures/src/two_objs.c" -I"$INC" intrinsics.o KTest.o -o two_objs
```

## 3. 最小回放（--no-trace，先跑通）

```bash
cd "$ROOT"
for i in 1 2 3; do
  symrepl "third_party/klee/ktest/get_sign_test00000$i.ktest" \
          --target "$BUILD/get_sign" --no-trace
  echo "test00000$i exit=$?"
done
```

**验收判据**（见 `src/symrepl/cli.py` 文件头）：

- 退出码 0–255 = 目标程序自身退出码（回放即运行）；
- 128 = 回放未正常退出（崩溃/信号）；
- 2 = symrepl 层失败（参数错、ktest 不可读、target 缺失、LLDB 不可用等）。

先预测再跑：三个 get_sign ktest 的退出码各是多少？为什么？
跑完对账：`tests/conftest.py:27` 注明了预期值——与你猜的一致吗？
第三个为什么不是 -1？get_sign 的 main 返回了什么、shell 把它表达成了什么？

多符号对象验证（两个对象 `arr`[4] + `b`[2]）：

```bash
symrepl third_party/klee/ktest/two_objs_test000001.ktest --target "$BUILD/two_objs" --no-trace
echo "exit=$?"
```

## 4. 完整回放（开 trace，边跑边读代码）

```bash
symrepl third_party/klee/ktest/get_sign_test000001.ktest --target "$BUILD/get_sign"
```

观察输出末尾的分支报告（决策点 ↔ 机器状态对齐）。先写下预测：断点打在哪、
机器状态从哪取——下表是对账材料，预测写完再读：

| 代码位置 | 看什么 |
|----------|--------|
| `src/symrepl/replay/replay.py:21` | replay() 入口：写临时 ktest、spawn 子进程、JSON 回传 |
| `src/symrepl/replay/driver.py:486` | run()：LLDB 启动、断点循环（命中→记录→continue） |
| `src/symrepl/replay/driver.py:277` | `KTEST_FILE` 环境变量注入点（输入注入的实现） |
| `src/symrepl/replay/driver.py:178` | _collect_sites()：预扫条件分支、逐址下断 |
| `src/symrepl/replay/branches.py:103` | infer_outcomes()：推断 taken/fallthrough |
| `src/symrepl/replay/trace.py:34` | TraceReport.render()：报告文本生成 |

## 5. 失败路径抽查（可选）

```bash
# 坏 ktest → 退出码 2
symrepl /dev/null --target "$BUILD/get_sign" --no-trace; echo "exit=$?"
# 缺失 target → 退出码 2
symrepl third_party/klee/ktest/get_sign_test000001.ktest --target /no/such/bin; echo "exit=$?"
```

## 6. 可选：重新生成 .ktest（需 Docker）

```bash
cd tests/fixtures
docker run --rm -v "$PWD":/work -w /work klee/klee:3.1 sh gen.sh
```

生成流程（gen.sh 内可见）：`clang -emit-llvm -g` 编译为 .ll → `klee file.ll`
符号执行 → `klee-out-*/test*.ktest` 即反例。产物应与
`third_party/klee/ktest/` 中已提交的一致（该目录 README 有 sha256 表）。

## 7. 验收清单

- [ ] 能说出 ktest 文件的字节布局（magic/版本/argv/对象）
- [ ] 能说出 `klee_make_symbolic` 在回放期的行为（intrinsics.c：读 KTEST_FILE 填字节）
- [ ] 目标二进制编译成功（运行库静态链入）
- [ ] 三个 get_sign ktest 回放退出码 == 0 / 1 / 255
- [ ] 开 trace 后能看懂分支报告，并在 driver.py 里找到对应的事件循环
- [ ] （可选）Docker 重生成 ktest 与已提交版本一致

完成以上即阶段一通过，可进入阶段二：`local-test-phase2.md`（M2 DAP 链路，承重流程同款）。
