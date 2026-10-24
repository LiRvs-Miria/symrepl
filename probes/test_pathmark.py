#!/usr/bin/env python3
"""test_pathmark — M3 .ll 静态符号性纯核直测网（RED 2026-10-24）。

需求文本=ADR-0004（裁决 2/4/6）+ design-notes server.md「M3 路径缩小」节：
- 三分类本体：fork（条件 ∈ 符号闭包）/噪声A（函数不在 .ll 模块）/噪声B（条件
  在闭包外）——本网盖 fork 判定与噪声判据原语；A/B 细分在注记层（步2）
- 机制：make_symbolic 调用点标记内存 → load/icmp 传播 → 直接调用实参→形参
  绑定 → br 条件测闭包；行级 join 键=(函数名, !DILocation line)
- Expect 代数：parse 坏当值 ParseBad 不抛（launch Rejected 的底座）
- 验收真值（ADR 裁决 6）：get_sign.ll fork={(get_sign,11),(get_sign,13)}；
  main 无 fork；intrinsics.c 侧函数不在模块

RED 预期：pathmark 模块缺位=导入网炸（rc≠0）；小步转录=已落地查 PASS。
真件查询依赖 clang（缺则 SKIP 不计败，同 conftest 家法）；单元查询 inline
.ll 片段零工具链依赖（封闭网，可进 CI）。
"""
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPLAY_DIR = Path(__file__).resolve().parent.parent / "src" / "replay"   # src/replay（引擎代数册）
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPLAY_DIR))

import pathmark

fails = []

def check(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        fails.append(name)
    #end if
#end def

def skip(name, note):
    print(f"SKIP {name} ({note})")
#end def


# ---- 真件锚：clang -emit-llvm 产物（同 conftest 配方 -O0 -g；ADR-0001 公开产物）----

get_sign_ll = None
note = "clang not available"
clang = shutil.which("clang")
if clang is None:
    note = "clang not available"
#end if
if clang is not None:
    inc = ROOT / "third_party" / "klee" / "runtime" / "include"
    src = ROOT / "tests" / "fixtures" / "src" / "get_sign.c"
    work = Path(tempfile.mkdtemp(prefix="pathmark-"))
    ll_path = work / "get_sign.ll"
    proc = subprocess.run(
        [clang, "-O0", "-g", "-S", "-emit-llvm", f"-I{inc}", str(src), "-o", str(ll_path)],
        capture_output=True, text=True)
    if proc.returncode != 0:
        note = f"clang failed: {proc.stderr.strip()[:80]}"
    else:
        get_sign_ll = ll_path.read_text()
    #end if
#end if

if get_sign_ll is not None:
    m = pathmark.parse_module(get_sign_ll)

    # A1 parse ok：真件非 ParseBad
    check("A1: get_sign.ll parse 非 ParseBad", not isinstance(m, pathmark.ParseBad))

    # A2 验收真值：fork={(get_sign,11),(get_sign,13)}（排序稳定输出）
    forks = pathmark.fork_sites(m)
    check("A2: 真值 fork_sites=[(get_sign,11),(get_sign,13)]",
          forks == [pathmark.ForkSite("get_sign", 11),
                    pathmark.ForkSite("get_sign", 13)])

    # A3 main 无 fork（入口停非决策的静态面）
    check("A3: main 无符号 br",
          all(f.function != "main" for f in forks))

    # A4 噪声A 判据原语：模块函数集成员
    check("A4: has_function get_sign/main=True；kTest_fromFile/kTest_compare=False",
          pathmark.has_function(m, "get_sign")
          and pathmark.has_function(m, "main")
          and not pathmark.has_function(m, "kTest_fromFile")
          and not pathmark.has_function(m, "kTest_compare"))
else:
    for q in ("A1", "A2", "A3", "A4"):
        skip(q, note)
    #end for
#end if


# ---- 数据流单元（inline .ll 片段，形状照 clang -O0 输出）----

SEED_CHAIN = """\
define dso_local i32 @f() #0 !dbg !17 {
  %1 = alloca i32, align 4
  call void @klee_make_symbolic(ptr noundef %1, i64 noundef 4, ptr noundef @.str), !dbg !20
  %2 = load i32, ptr %1, align 4, !dbg !24
  %3 = icmp eq i32 %2, 0, !dbg !26
  br i1 %3, label %4, label %5, !dbg !26

4:
  ret i32 0, !dbg !27

5:
  ret i32 1, !dbg !28
}

declare void @klee_make_symbolic(ptr noundef, i64 noundef, ptr noundef) #1

!17 = !DISubprogram(name: "f", scope: null, file: null, line: 1, spFlags: DISPFlagDefinition)
!20 = !DILocation(line: 2, column: 3, scope: !17)
!24 = !DILocation(line: 3, column: 7, scope: !17)
!26 = !DILocation(line: 4, column: 9, scope: !17)
!27 = !DILocation(line: 5, column: 3, scope: !17)
!28 = !DILocation(line: 6, column: 3, scope: !17)
"""

CONCRETE_CHAIN = """\
define dso_local i32 @g() #0 !dbg !17 {
  %1 = alloca i32, align 4
  store i32 5, ptr %1, align 4
  %2 = load i32, ptr %1, align 4, !dbg !24
  %3 = icmp slt i32 %2, 0, !dbg !26
  br i1 %3, label %4, label %5, !dbg !26

4:
  ret i32 0, !dbg !27

5:
  ret i32 1, !dbg !28
}

!17 = !DISubprogram(name: "g", scope: null, file: null, line: 1, spFlags: DISPFlagDefinition)
!24 = !DILocation(line: 3, column: 7, scope: !17)
!26 = !DILocation(line: 4, column: 9, scope: !17)
!27 = !DILocation(line: 5, column: 3, scope: !17)
!28 = !DILocation(line: 6, column: 3, scope: !17)
"""

CALL_BINDING = """\
define dso_local i32 @caller() #0 !dbg !17 {
  %1 = alloca i32, align 4
  call void @klee_make_symbolic(ptr noundef %1, i64 noundef 4, ptr noundef @.str), !dbg !20
  %2 = load i32, ptr %1, align 4, !dbg !24
  %3 = call i32 @callee(i32 noundef %2), !dbg !26
  ret i32 %3, !dbg !28
}

define dso_local i32 @callee(i32 noundef %0) #0 !dbg !30 {
  %2 = alloca i32, align 4
  store i32 %0, ptr %2, align 4
  %3 = load i32, ptr %2, align 4, !dbg !34
  %4 = icmp slt i32 %3, 0, !dbg !36
  br i1 %4, label %5, label %6, !dbg !36

5:
  ret i32 -1, !dbg !37

6:
  ret i32 1, !dbg !38
}

declare void @klee_make_symbolic(ptr noundef, i64 noundef, ptr noundef) #1

!17 = !DISubprogram(name: "caller", scope: null, file: null, line: 1, spFlags: DISPFlagDefinition)
!20 = !DILocation(line: 2, column: 3, scope: !17)
!24 = !DILocation(line: 3, column: 7, scope: !17)
!26 = !DILocation(line: 4, column: 10, scope: !17)
!28 = !DILocation(line: 5, column: 3, scope: !17)
!30 = !DISubprogram(name: "callee", scope: null, file: null, line: 7, spFlags: DISPFlagDefinition)
!34 = !DILocation(line: 9, column: 7, scope: !30)
!36 = !DILocation(line: 10, column: 9, scope: !30)
!37 = !DILocation(line: 11, column: 3, scope: !30)
!38 = !DILocation(line: 13, column: 3, scope: !30)
"""

CALL_CONCRETE = """\
define dso_local i32 @caller2() #0 !dbg !17 {
  %1 = call i32 @callee2(i32 noundef 7), !dbg !20
  ret i32 %1, !dbg !22
}

define dso_local i32 @callee2(i32 noundef %0) #0 !dbg !30 {
  %2 = alloca i32, align 4
  store i32 %0, ptr %2, align 4
  %3 = load i32, ptr %2, align 4, !dbg !34
  %4 = icmp eq i32 %3, 7, !dbg !36
  br i1 %4, label %5, label %6, !dbg !36

5:
  ret i32 0, !dbg !37

6:
  ret i32 1, !dbg !38
}

!17 = !DISubprogram(name: "caller2", scope: null, file: null, line: 1, spFlags: DISPFlagDefinition)
!20 = !DILocation(line: 2, column: 10, scope: !17)
!22 = !DILocation(line: 3, column: 3, scope: !17)
!30 = !DISubprogram(name: "callee2", scope: null, file: null, line: 5, spFlags: DISPFlagDefinition)
!34 = !DILocation(line: 7, column: 7, scope: !30)
!36 = !DILocation(line: 8, column: 9, scope: !30)
!37 = !DILocation(line: 9, column: 3, scope: !30)
!38 = !DILocation(line: 11, column: 3, scope: !30)
"""

ICMP_MIXED = """\
define dso_local i32 @h() #0 !dbg !17 {
  %1 = alloca i32, align 4
  call void @klee_make_symbolic(ptr noundef %1, i64 noundef 4, ptr noundef @.str), !dbg !20
  %2 = load i32, ptr %1, align 4, !dbg !24
  %3 = icmp eq i32 42, %2, !dbg !26
  br i1 %3, label %4, label %5, !dbg !26

4:
  ret i32 0, !dbg !27

5:
  ret i32 1, !dbg !28
}

declare void @klee_make_symbolic(ptr noundef, i64 noundef, ptr noundef) #1

!17 = !DISubprogram(name: "h", scope: null, file: null, line: 1, spFlags: DISPFlagDefinition)
!20 = !DILocation(line: 2, column: 3, scope: !17)
!24 = !DILocation(line: 3, column: 7, scope: !17)
!26 = !DILocation(line: 4, column: 9, scope: !17)
!27 = !DILocation(line: 5, column: 3, scope: !17)
!28 = !DILocation(line: 6, column: 3, scope: !17)
"""

NO_DBG = """\
define dso_local i32 @k() #0 !dbg !17 {
  %1 = alloca i32, align 4
  call void @klee_make_symbolic(ptr noundef %1, i64 noundef 4, ptr noundef @.str)
  %2 = load i32, ptr %1, align 4
  %3 = icmp eq i32 %2, 0
  br i1 %3, label %4, label %5

4:
  ret i32 0

5:
  ret i32 1
}

declare void @klee_make_symbolic(ptr noundef, i64 noundef, ptr noundef) #1

!17 = !DISubprogram(name: "k", scope: null, file: null, line: 1, spFlags: DISPFlagDefinition)
"""

# B1 seed→load→icmp→br 基本链：fork 站点 (f,4)
m = pathmark.parse_module(SEED_CHAIN)
check("B1: seed 链 fork=(f,4)",
      pathmark.fork_sites(m) == [pathmark.ForkSite("f", 4)])

# B2 噪声B 判据：具体条件不 fork
m = pathmark.parse_module(CONCRETE_CHAIN)
check("B2: 具体条件 br 不入 fork 集",
      pathmark.fork_sites(m) == [])

# B3 跨函数绑定：符号实参→callee 形参→callee br fork（get_sign 链的单元形）
m = pathmark.parse_module(CALL_BINDING)
check("B3: 实参→形参绑定 callee fork=(callee,10)",
      pathmark.fork_sites(m) == [pathmark.ForkSite("callee", 10)])

# B4 对照：常量实参跨 call → callee br 不 fork
m = pathmark.parse_module(CALL_CONCRETE)
check("B4: 常量实参跨 call 不 fork",
      pathmark.fork_sites(m) == [])

# B5 icmp 任一操作数符号即符号（常量在前）
m = pathmark.parse_module(ICMP_MIXED)
check("B5: icmp 任一操作数符号即符号",
      pathmark.fork_sites(m) == [pathmark.ForkSite("h", 4)])

# B6 join 不了的站点不注记：br 无 !dbg → 无站点（honest 缺行不出假站点）
m = pathmark.parse_module(NO_DBG)
check("B6: br 无 !dbg 不产站点",
      pathmark.fork_sites(m) == [])


# ---- 解析面（Expect 代数：坏 .ll 当值，不抛）----

# C1 空文本 → ParseBad
r = pathmark.parse_module("")
ok = isinstance(r, pathmark.ParseBad) and r.reason == pathmark.ParseBadReason.NO_MODULE
check("C1: 空文本 ParseBad(NO_MODULE) 当值不抛", ok)

# C2 无 define 的文本 → ParseBad
r = pathmark.parse_module("not llvm at all\njust text\n")
ok = isinstance(r, pathmark.ParseBad) and r.reason == pathmark.ParseBadReason.NO_MODULE
check("C2: 无 define 文本 ParseBad", ok)

# C3 ParseBad 携人读 detail
r = pathmark.parse_module("")
check("C3: ParseBad.detail 非空", isinstance(r, pathmark.ParseBad) and bool(r.detail))


# ---- 三分类与结构化下发（M3 步2：注记层纯面） ----

# D1 wire 往返：to_wire→from_wire 值相等（ktest sym_objects 家法：解析一次对象过线）
m = pathmark.parse_module(CALL_BINDING)
check("D1: to_wire→from_wire 往返值相等",
      pathmark.from_wire(pathmark.to_wire(m)) == m)

# D2 fork 判据：(f,4) → ("fork","symbolic-branch")
m = pathmark.parse_module(SEED_CHAIN)
forks = frozenset(pathmark.fork_sites(m))
check("D2: classify fork=(fork,symbolic-branch)",
      pathmark.classify(forks, m, "f", 4) == ("fork", "symbolic-branch"))

# D3 噪声A 判据：函数不在模块 → ("runtime","function-not-in-module")
check("D3: classify 噪声A=(runtime,function-not-in-module)",
      pathmark.classify(forks, m, "kTest_fromFile", 42)
      == ("runtime", "function-not-in-module"))

# D4 噪声B 判据：函数在模块、站点非 fork → ("runtime","concrete-condition")
check("D4: classify 噪声B=(runtime,concrete-condition)",
      pathmark.classify(forks, m, "f", 5) == ("runtime", "concrete-condition"))

# D5 wire 坏形状 → ParseBad(WIRE_BAD) 当值不抛
w1 = pathmark.from_wire("not a dict")
w2 = pathmark.from_wire({"functions": []})
w3 = pathmark.from_wire({"functions": {"f": {}}, "dbg_lines": {}})
ok = all(isinstance(w, pathmark.ParseBad)
         and w.reason == pathmark.ParseBadReason.WIRE_BAD for w in (w1, w2, w3))
check("D5: from_wire 坏形状 ParseBad(WIRE_BAD)", ok)

if get_sign_ll is not None:
    # A5 真件三分类：fork 行/模块内非 fork 行/模块外函数
    m = pathmark.parse_module(get_sign_ll)
    forks = frozenset(pathmark.fork_sites(m))
    check("A5: 真件 classify 三判据各就位",
          pathmark.classify(forks, m, "get_sign", 11) == ("fork", "symbolic-branch")
          and pathmark.classify(forks, m, "get_sign", 14) == ("runtime", "concrete-condition")
          and pathmark.classify(forks, m, "kTest_fromFile", 0)
          == ("runtime", "function-not-in-module"))
else:
    skip("A5", note)
#end if


print()
print(f"{len(fails)} fail(s)")
sys.exit(1 if fails else 0)
