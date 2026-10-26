#!/usr/bin/env python3
"""test_diffmat — M4-a 差分矩阵纯核直测网（RED 2026-10-26，B/V 面起步）。

需求文本=ADR-0006 + design-notes replay.md「M4-a 差分回放矩阵」节（三答
定稿 2026-10-26）。本回合钉两面：
- diffmat.flip(k, obj, byte, bit) -> KTest | FlipBad：单位翻转，三向越界当值不抛
- diffmat.variants(k, cap) -> VariantPlan：位翻转全列（n 字节=8n 位）；降采样
  **确定性**（同输入同计划，无随机）；cap 超限如实标未分析档（不静默截断）
- VariantPlan 字段面（variants/unanalyzed）为本网钉出的需求形状，铸名
  撞到随铸可复议
矩阵/判定/互证（M/X 面）与真饵网 probe_m4a 随其转录步各自 RED 钉入
（小步节律，一次一钉）。

RED 预期：diffmat 模块缺位=全查 FAIL（清单即需求文本）。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src" / "replay"))

MODULE_OK = True
try:
    import diffmat                          # 模块属性访问：缺名不连坐（E1 家法微进化）
    from ktest import KTest, KTestObject
except ImportError as e:
    MODULE_OK = False
    _IMPORT_DETAIL = f"{type(e).__name__}: {e}"
#end try

fails = []

def check(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        fails.append(name)
    #end if
#end def


# ---- 语料（值面照 probe_ktest 家法；单对象/多对象/零尺寸各一；缺位不构造） ----
if MODULE_OK:
    K1 = KTest(3, ("prog",), 2, 4, (KTestObject("a", b"\x01\x02\x03"),))
    K2 = KTest(3, ("two_objs.ll",), 0, 0, (KTestObject("arr", b"\x41\xff\xff\xff"),
                                           KTestObject("b", b"\x00\x01")))
    KZ = KTest(3, (), 0, 0, (KTestObject("z", b""),))
#end if

def total_xor_diff(a, b) -> int:
    """两份 KTest 逐对象字节异或的总差位数（形状不同返回 -1）。"""
    if (a.version, a.args, a.sym_argvs, a.sym_argv_len, len(a.objects)) \
       != (b.version, b.args, b.sym_argvs, b.sym_argv_len, len(b.objects)):
        return -1
    #end if
    bits = 0
    for oa, ob in zip(a.objects, b.objects):
        if oa.name != ob.name or len(oa.data) != len(ob.data):
            return -1
        #end if
        bits += sum((x ^ y).bit_count() for x, y in zip(oa.data, ob.data))
    #end for
    return bits
#end def


# ---- B 面：flip 代数 ----
def _b1() -> bool:
    f = diffmat.flip(K1, 0, 0, 7)
    return (isinstance(f, KTest)
            and f.objects[0].data == b"\x81\x02\x03"     # 0x01 ^ 0x80
            and f.version == 3 and f.args == ("prog",)
            and f.sym_argvs == 2 and f.sym_argv_len == 4)
#end def

def _b2() -> bool:
    f = diffmat.flip(K1, 0, 0, 7)
    return K1.objects[0].data == b"\x01\x02\x03" and f is not K1
#end def

def _b3() -> bool:
    """三向越界当值 FlipBad（obj/byte/bit 各一向；bit=8 出界 bit=7 合法）。"""
    return (isinstance(diffmat.flip(K1, 1, 0, 0), diffmat.FlipBad)
            and isinstance(diffmat.flip(K1, 0, 3, 0), diffmat.FlipBad)
            and isinstance(diffmat.flip(K1, 0, 0, 8), diffmat.FlipBad)
            and isinstance(diffmat.flip(K1, 0, 0, 7), KTest))
#end def

def _b4() -> bool:
    return diffmat.flip(diffmat.flip(K1, 0, 1, 3), 0, 1, 3) == K1
#end def

def _b5() -> bool:
    """零尺寸对象任何位都出界（当值）。"""
    return isinstance(diffmat.flip(KZ, 0, 0, 0), diffmat.FlipBad)
#end def


# ---- V 面：variants 计划 ----
def _v1() -> bool:
    p = diffmat.variants(K1, 10 ** 9)
    vs = p.variants
    return (len(vs) == 24                                        # 3 字节 × 8
            and all(isinstance(v, KTest) for v in vs)
            and all(total_xor_diff(v, K1) == 1 for v in vs)      # 逐个恰差一位
            and len({tuple(o.data for o in v.objects) for v in vs}) == 24)
#end def

def _v2() -> bool:
    """多对象全列：4B+2B=48 位，两对象都有变体可达。"""
    p = diffmat.variants(K2, 10 ** 9)
    if len(p.variants) != 48:
        return False
    #end if
    touch = [False, False]
    for v in p.variants:
        if v.objects[0].data != K2.objects[0].data:
            touch[0] = True
        #end if
        if v.objects[1].data != K2.objects[1].data:
            touch[1] = True
        #end if
    #end for
    return all(touch)
#end def

def _v3() -> bool:
    """cap 如实：分析位 ≤cap，未分析档=全列−分析（不静默截断）；全列时未分析=0。"""
    p_full = diffmat.variants(K1, 10 ** 9)
    p_cap = diffmat.variants(K1, 4)
    p_zero = diffmat.variants(K1, 0)
    return (p_full.unanalyzed == 0
            and len(p_cap.variants) <= 4 and p_cap.unanalyzed == 24 - len(p_cap.variants)
            and len(p_zero.variants) == 0 and p_zero.unanalyzed == 24)
#end def

def _v4() -> bool:
    """确定性：同输入同计划（无随机）。"""
    return diffmat.variants(K1, 4).variants == diffmat.variants(K1, 4).variants \
        and diffmat.variants(K1, 4).unanalyzed == diffmat.variants(K1, 4).unanalyzed
#end def


# ---- 查册（模块缺位时全查 FAIL=RED 基线） ----
CHECKS = []
def case(name, fn):
    CHECKS.append((name, fn))
#end def

case("B1: flip 单位翻转恰一位（0x01→0x81，其余字段全同）", _b1)
case("B2: flip 纯值不共享（原 k 不变）", _b2)
case("B3: 三向越界当值 FlipBad（obj/byte/bit；bit=8 出界 bit=7 合法）", _b3)
case("B4: 对合——同位翻两次=原值", _b4)
case("B5: 零尺寸对象任何位出界（当值）", _b5)
case("V1: 全列=8n 位（单对象 3B→24 变体，逐个恰差一位且互异）", _v1)
case("V2: 多对象全列（4B+2B→48，两对象各有变体可达）", _v2)
case("V3: cap 如实降级（≤cap；未分析档=全列−分析；全列 unanalyzed=0；cap=0 边界）", _v3)
case("V4: 确定性（同输入两次同计划）", _v4)

# ---- M 面：矩阵与判定（2026-10-26 步 4 钉入；RunObserved/matrix/dependent） ----
S11 = ("get_sign", 11)
S13 = ("get_sign", 13)

def _m1() -> bool:
    """走向集合>1 ⇒ input-dependent；恒定 ⇒ 否（样张同形：13 依赖/11 盲）。"""
    runs = [diffmat.RunObserved(frozenset({S11}), 0),
            diffmat.RunObserved(frozenset({S11, S13}), 1)]
    sm = diffmat.matrix(runs)
    a = diffmat.dependent(sm)
    return (sm.keys() == {S11, S13}
            and sm[S13] == frozenset({True, False})
            and sm[S11] == frozenset({True})
            and a.verdicts[S13].input_dependent
            and not a.verdicts[S11].input_dependent)
#end def

def _m2() -> bool:
    """两 run 全恒定 ⇒ 站点全不谎报 input-dependent。"""
    runs = [diffmat.RunObserved(frozenset({S11, S13}), 0),
            diffmat.RunObserved(frozenset({S11, S13}), 1)]
    a = diffmat.dependent(diffmat.matrix(runs))
    return all(not v.input_dependent for v in a.verdicts.values())
#end def

def _m3() -> bool:
    """置信如实：full/capped；capped 只动标签不动判定。"""
    runs = [diffmat.RunObserved(frozenset({S11}), 0),
            diffmat.RunObserved(frozenset({S11, S13}), 1)]
    sm = diffmat.matrix(runs)
    full = diffmat.dependent(sm, capped=False)
    cap = diffmat.dependent(sm, capped=True)
    return (full.confidence == "full" and cap.confidence == "capped"
            and full.verdicts == cap.verdicts)
#end def

def _m4() -> bool:
    """空输入边缘：空 runs → 空矩阵/空判定/置信 full。"""
    empty = diffmat.matrix([])
    a = diffmat.dependent(empty)
    return empty == {} and a.verdicts == {} and a.confidence == "full"
#end def

case("M1: 走向集合>1⇒input-dependent；恒定⇒否（13 依赖/11 盲，样张同形）", _m1)
case("M2: 全恒定 ⇒ 站点全不谎报", _m2)
case("M3: 置信 full/capped 如实；capped 只动标签不动判定", _m3)
case("M4: 空输入边缘（空 runs→空矩阵/空判定/full）", _m4)

# ---- X 面：互证臂（2026-10-26 步 5 钉入；crosscheck(fork_set, analysis)） ----
# 2×2 全空间：fork∧dep=STRONG／concrete∧dep=PROPAGATION_BOUNDARY（pathmark
# 闭包边界活证）／fork∧const=INSUFFICIENT_COVERAGE／concrete∧const=QUIET
# （双侧无信号，非 informative 臂——三臂文本指三个 informative 臂）。
S14 = ("get_sign", 14)
S15 = ("get_sign", 15)

def _x_analysis():
    """构四格全现的 analysis：S13 依赖/S11 盲/S14 越界依赖/S15 双盲。"""
    runs = [diffmat.RunObserved(frozenset({S11, S15}), 0),
            diffmat.RunObserved(frozenset({S11, S13, S14, S15}), 1)]
    return diffmat.dependent(diffmat.matrix(runs))
#end def

def _x1() -> bool:
    arms = diffmat.crosscheck({S11, S13}, _x_analysis())
    return (arms[S13] == diffmat.Arm.STRONG
            and arms[S11] == diffmat.Arm.INSUFFICIENT_COVERAGE)
#end def

def _x2() -> bool:
    arms = diffmat.crosscheck({S11, S13}, _x_analysis())
    return arms[S14] == diffmat.Arm.PROPAGATION_BOUNDARY
#end def

def _x3() -> bool:
    arms = diffmat.crosscheck({S11, S13}, _x_analysis())
    return arms[S15] == diffmat.Arm.QUIET
#end def

def _x4() -> bool:
    """恰一臂：站点全集=verdicts 键，每站点恰一值，无键外站点。"""
    arms = diffmat.crosscheck({S11, S13}, _x_analysis())
    return (len(arms) == 4
            and all(isinstance(a, diffmat.Arm) for a in arms.values())
            and set(arms) == {S11, S13, S14, S15})
#end def

def _x5() -> bool:
    """接面：pathmark.ForkSite 行级键同构——fork_sites() 输出直通不换算。"""
    import pathmark
    fk = {pathmark.ForkSite("get_sign", 11), pathmark.ForkSite("get_sign", 13)}
    return diffmat.crosscheck(fk, _x_analysis()) == \
        diffmat.crosscheck({S11, S13}, _x_analysis())
#end def

case("X1: fork∧dep=STRONG；fork∧恒定=INSUFFICIENT_COVERAGE", _x1)
case("X2: concrete∧dep=PROPAGATION_BOUNDARY（闭包边界活证）", _x2)
case("X3: concrete∧恒定=QUIET（双侧无信号，第四格如实）", _x3)
case("X4: 恰一臂（站点全集=verdicts 键，Arm 封闭）", _x4)
case("X5: pathmark.ForkSite 直通（行级键同构不换算）", _x5)

# ---- V5/R 面：位姿对齐+归因+报告（2026-10-26 步 6a 钉入） ----
KB1 = KTest(3, (), 0, 0, (KTestObject("x", b"\x01"),))   # 单字节对象：8 位姿

def _v5() -> bool:
    """positions 与 variants 等长对齐、全列序=枚举序、cap 选取⊆全位姿且无重复。"""
    fp = [(i, j, b) for i, o in enumerate(K1.objects)
          for j in range(len(o.data)) for b in range(8)]
    p = diffmat.variants(K1, 10 ** 9)
    if p.positions != tuple(fp) or len(p.positions) != len(p.variants):
        return False
    #end if
    p4 = diffmat.variants(K1, 4)
    return (p4.positions == tuple(dict.fromkeys(p4.positions))
            and set(p4.positions) <= set(fp)
            and len(p4.positions) == 4 and p4.unanalyzed == 20)
#end def

def _r_runs():
    """1 字节故事：variant (0,0,0) 独改走向与 exit——归因/分布的最小可手算案。"""
    run0 = diffmat.RunObserved(frozenset({S11}), 0)
    runs = [run0]
    for i in range(8):
        if i == 0:
            runs.append(diffmat.RunObserved(frozenset({S11, S13}), 1))
        else:
            runs.append(diffmat.RunObserved(frozenset({S11}), 0))
        #end if
    #end for
    return runs
#end def

def _r1() -> bool:
    """位级归因：成员相对 run0 变化的位姿——唯 (0,0,0)；恒定站点不入册。"""
    dp = diffmat.driving_positions(diffmat.variants(KB1, 10 ** 9), _r_runs())
    return dp == {S13: ((0, 0, 0),)}
#end def

def _r2() -> bool:
    """exit 分布：exit code → run 下标元组（run0=baseline 在册）。"""
    ec = diffmat.exit_classes(_r_runs())
    return ec == {0: (0, 2, 3, 4, 5, 6, 7, 8), 1: (1,)}
#end def

def _r3() -> bool:
    """报告全文钉（最小手算案；格式即样张家法）。fork={11,13} 照真 get_sign 形。"""
    plan = diffmat.variants(KB1, 10 ** 9)
    a = diffmat.dependent(diffmat.matrix(_r_runs()))
    text = diffmat.render(baseline="b.ktest", target="t", plan=plan,
                          runs=_r_runs(), analysis=a, fork={S11, S13})
    return text == (
        "# M4-a 差分回放矩阵 — t\n"
        "baseline : b.ktest   exit 0   hits [('get_sign', 11)]\n"
        "variants : 8（位翻转全列）+ baseline(run0) = 9 runs    置信: full\n"
        "互证     : 在场（fork=[('get_sign', 11), ('get_sign', 13)]）\n"
        "\n"
        "site (get_sign, 11)\n"
        "  走向集合 : hit 9 / miss 0（run0=hit，恒定）\n"
        "  判定     : 本观测内未见依赖\n"
        "  驱动翻转 : 无成员变化\n"
        "  互证     : 覆盖不足\n"
        "\n"
        "site (get_sign, 13)\n"
        "  走向集合 : hit 1 / miss 8（run0=miss）\n"
        "  判定     : input-dependent\n"
        "  驱动翻转 : 1/8 位姿：[(0, 0, 0)]\n"
        "  互证     : 强断言\n"
        "\n"
        "exit 分布 : 0 × 8（run0）｜1 × 1\n"
        "程序级    : exit 随翻转变化 ⇒ 输出 input-dependent（驱动位=[(0, 0, 0)]）\n"
        "未分析    : 无\n"
    )
#end def

def _r4() -> bool:
    """互证缺席+cap 报告面（结构钉）。"""
    plan = diffmat.variants(KB1, 4)
    runs4 = [diffmat.RunObserved(frozenset({S11}), 0),
             diffmat.RunObserved(frozenset({S11, S13}), 1),
             diffmat.RunObserved(frozenset({S11}), 0),
             diffmat.RunObserved(frozenset({S11}), 0),
             diffmat.RunObserved(frozenset({S11}), 0)]
    a = diffmat.dependent(diffmat.matrix(runs4), capped=True)
    text = diffmat.render(baseline="b.ktest", target="t", plan=plan,
                          runs=runs4, analysis=a, fork=None)
    return ("互证     : 缺席（未提供 .ll）" in text
            and "stride 采样（cap）" in text
            and "置信: capped" in text
            and "未分析    : 4 位姿未分析（stride 跳过）" in text
            and "互证     : —" in text)
#end def

case("V5: positions 与 variants 对齐（全列序=枚举序；cap 选取⊆位姿无重复）", _v5)
case("R1: driving_positions 位级归因（唯 (0,0,0)；恒定站点不入册）", _r1)
case("R2: exit_classes 分布（exit→run 下标元组）", _r2)
case("R3: render 报告全文钉（最小手算案）", _r3)
case("R4: render 缺席+cap 结构面（互证—/stride/capped/未分析行）", _r4)


if __name__ == "__main__":
    if MODULE_OK:
        for name, fn in CHECKS:
            try:
                check(name, fn())
            except Exception as e:
                check(name + f"（网炸 {type(e).__name__}: {e}）", False)
            #end try
        #end for
    else:
        print(f"diffmat 模块缺位（RED 全红基线）：{_IMPORT_DETAIL}")
        for name, _ in CHECKS:
            check(name, False)
        #end for
    #end if
    print("=" * 30)
    print("ALL PASS" if not fails else f"{len(fails)} FAIL: {fails}")
    sys.exit(1 if fails else 0)
#end if
