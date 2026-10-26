"""diffmat — M4-a 差分回放矩阵核件（扰动/走向/判定/互证代数，纯值零 I/O）。

契约源=ADR-0006 + design-notes replay.md「M4-a 差分回放矩阵」节。家法：
纯值无方法（运算一律模块函数）；错误当值不抛（Expect 代数）；纯核零 I/O。
在册：flip 单位翻转、variants 计划（stride 采样）、matrix/dependent 走向
判定、crosscheck 互证臂；驱动壳与报告渲染在壳侧（后续步）。
"""
from __future__ import annotations

from enum import Enum, auto
from typing import NamedTuple

from ktest import KTest, KTestObject


class FlipBadReason(Enum):
    INDEX_OUT_OF_RANGE = auto()   # obj/byte/bit 三向越界（detail 携人读事实）
#end class

class FlipBad(NamedTuple):
    """翻转越界（当值返回；reason 封闭枚举——新败类=新成员）。"""
    reason: FlipBadReason
    detail: str
#end class

FlipOutcome = KTest | FlipBad        # Expect 代数：值或错，二选一


class VariantPlan(NamedTuple):
    """一份扰动计划：入选变体（每个恰差原值一位）+未分析位数+位姿对齐。

    ``unanalyzed`` = 全部位数 −入选位数；>0 即 capped（如实标，非静默截断）。
    ``positions[i]`` = ``(obj, byte, bit)``，与 ``variants[i]`` 一一对应。
    """
    variants: tuple[KTest, ...]
    unanalyzed: int
    positions: tuple[tuple[int, int, int], ...]
#end class


def _stride_indices(total: int, cap: int) -> list[int]:
    """从 ``total`` 个位姿里均匀取 ``cap`` 个下标（含首尾；确定性无随机）。"""
    if cap <= 0:
        return []
    #end if
    if cap >= total:
        return list(range(total))
    #end if
    if cap == 1:
        return [0]
    #end if
    return [i * (total - 1) // (cap - 1) for i in range(cap)]
#end def


def variants(k: KTest, cap: int) -> VariantPlan:
    """位翻转全列计划：n 字节=8n 个位姿，逐个 ``flip`` 生成变体。

    cap 超限走全域均匀 stride 采样（大对象密度按比例占优——按尺寸分档的
    v1 语义）；cap 为负按 0 处理。同输入同计划（纯函数，无随机）。
    """
    positions = [
        (i, j, b)
        for i, o in enumerate(k.objects)
        for j in range(len(o.data))
        for b in range(8)
    ]
    chosen = _stride_indices(len(positions), cap)
    sel = tuple(positions[t] for t in chosen)
    results: list[KTest] = []
    for p in sel:
        r = flip(k, *p)
        if isinstance(r, FlipBad):
            # 不可达：位姿由本函数按对象边界构造，恒在界内（不变量显性化）
            raise AssertionError(f"variants internal invariant broken: {r.detail}")
        #end if
        results.append(r)
    #end for
    return VariantPlan(variants=tuple(results),
                       unanalyzed=len(positions) - len(results),
                       positions=sel)
#end def


#: 站点 join 键=(函数名, 行号)——pathmark ForkSite 同构（行级对账）。
Site = tuple[str, int]

#: 站点 → 命中成员状态集（True=该 run 命中；>1 态=走向集合非恒定）。
SiteMatrix = dict[Site, frozenset[bool]]

class RunObserved(NamedTuple):
    """单次重放观测：命中站点集+exit code（exit v1 只随行不参与判定）。"""
    hit_set: frozenset[Site]
    exit_code: int
#end class

class Verdict(NamedTuple):
    """站点级判定：走向集合>1 ⇔ input_dependent。"""
    input_dependent: bool
#end class

class Analysis(NamedTuple):
    """判定结果：逐站点 verdict+置信级别（full|capped，如实标不谎）。"""
    verdicts: dict[Site, Verdict]
    confidence: str
#end class


def matrix(runs: list[RunObserved]) -> SiteMatrix:
    """从观测序列提取站点走向集合：站点全集=各 run 命中集之并。"""
    universe: set[Site] = set()
    for r in runs:
        universe |= r.hit_set
    #end for
    return {s: frozenset(s in r.hit_set for r in runs) for s in universe}
#end def


def dependent(sm: SiteMatrix, *, capped: bool = False) -> Analysis:
    """站点级判定：走向集合>1 ⇒ input-dependent；恒定只判「未见依赖」
    （翻转=必要非充分，不谎称无关）。capped 只动置信标签不动判定。"""
    verdicts = {s: Verdict(input_dependent=len(states) > 1)
                for s, states in sm.items()}
    return Analysis(verdicts=verdicts,
                    confidence="capped" if capped else "full")
#end def


class Arm(Enum):
    """互证臂（2×2 全空间；三 informative 臂+QUIET=双侧无信号）。"""
    STRONG                = auto()   # fork ∧ input-dependent：静态动态互证
    PROPAGATION_BOUNDARY  = auto()   # concrete ∧ input-dependent：闭包边界活证
    INSUFFICIENT_COVERAGE = auto()   # fork ∧ 恒定：动态信号不足
    QUIET                 = auto()   # concrete ∧ 恒定：双侧无信号
#end class


def crosscheck(fork_set: set[Site], analysis: Analysis) -> dict[Site, Arm]:
    """静态三分类（pathmark）× 动态判定互证：任一站点恰落一臂。

    ``fork_set`` 照 ``pathmark.fork_sites()`` 输出直通（ForkSite 行级键与
    ``Site`` 元组同构，不换算）；站点全集= ``analysis.verdicts`` 键。
    """
    arms: dict[Site, Arm] = {}
    for s, v in analysis.verdicts.items():
        if s in fork_set:
            arms[s] = Arm.STRONG if v.input_dependent \
                else Arm.INSUFFICIENT_COVERAGE
        else:
            arms[s] = Arm.PROPAGATION_BOUNDARY if v.input_dependent \
                else Arm.QUIET
        #end if
    #end for
    return arms
#end def


def driving_positions(
    plan: VariantPlan, runs: list[RunObserved],
) -> dict[Site, tuple[tuple[int, int, int], ...]]:
    """位级归因：成员相对 run0（baseline）发生变化的变体位姿集。

    run i+1 ↔ ``plan.variants[i]``/``plan.positions[i]``（baseline 入册
    run0 家法）。恒定站点不入册（无成员变化=无位级归因）。
    """
    if not runs:
        return {}
    #end if
    universe: set[Site] = set()
    for r in runs:
        universe |= r.hit_set
    #end for
    out: dict[Site, tuple[tuple[int, int, int], ...]] = {}
    for s in universe:
        base_state = s in runs[0].hit_set
        changed = tuple(plan.positions[i] for i, r in enumerate(runs[1:])
                        if (s in r.hit_set) != base_state)
        if changed:
            out[s] = changed
        #end if
    #end for
    return out
#end def


def exit_classes(runs: list[RunObserved]) -> dict[int, tuple[int, ...]]:
    """exit 分布：exit code → 取该值的 run 下标元组（run0=baseline 在册）。"""
    out: dict[int, list[int]] = {}
    for i, r in enumerate(runs):
        out.setdefault(r.exit_code, []).append(i)
    #end for
    return {c: tuple(ix) for c, ix in out.items()}
#end def


_ARM_ZH = {
    Arm.STRONG: "强断言",
    Arm.PROPAGATION_BOUNDARY: "传播边界报警",
    Arm.INSUFFICIENT_COVERAGE: "覆盖不足",
    Arm.QUIET: "双盲无信号",
#end dict
}


def render(*, baseline: str, target: str, plan: VariantPlan,
           runs: list[RunObserved], analysis: Analysis,
           fork: set[Site] | None = None) -> str:
    """渲染人读报告（纯函数产出文本；格式=契约样张家法）。

    ``fork=None`` ⇒ 互证缺席行（IR 可选参数，缺席零增）。
    """
    arms = crosscheck(fork, analysis) if fork is not None else None
    r0 = runs[0]
    lines: list[str] = []
    lines.append(f"# M4-a 差分回放矩阵 — {target}")
    lines.append(f"baseline : {baseline}   exit {r0.exit_code}   "
                 f"hits {sorted(r0.hit_set)}")
    sampling = "位翻转全列" if plan.unanalyzed == 0 else "stride 采样（cap）"
    lines.append(f"variants : {len(plan.variants)}（{sampling}）+ "
                 f"baseline(run0) = {len(runs)} runs    置信: {analysis.confidence}")
    lines.append("互证     : " +
                 (f"在场（fork={sorted(fork)}）" if fork is not None
                  else "缺席（未提供 .ll）"))
    attribution = driving_positions(plan, runs)
    for s in sorted(analysis.verdicts):
        v = analysis.verdicts[s]
        hits = sum(1 for r in runs if s in r.hit_set)
        misses = len(runs) - hits
        r0_state = "hit" if s in r0.hit_set else "miss"
        constant = "，恒定" if hits in (0, len(runs)) else ""
        lines.append("")
        lines.append(f"site ({s[0]}, {s[1]})")
        lines.append(f"  走向集合 : hit {hits} / miss {misses}"
                     f"（run0={r0_state}{constant}）")
        lines.append("  判定     : " +
                     ("input-dependent" if v.input_dependent
                      else "本观测内未见依赖"))
        if s in attribution:
            pos = attribution[s]
            shown = ", ".join(str(p) for p in pos[:8]) \
                + ("， …" if len(pos) > 8 else "")
            lines.append(f"  驱动翻转 : {len(pos)}/{len(plan.variants)}"
                         f" 位姿：[{shown}]")
        else:
            lines.append("  驱动翻转 : 无成员变化")
        #end if
        lines.append("  互证     : " +
                     (_ARM_ZH[arms[s]] if arms is not None else "—"))
    #end for
    lines.append("")
    classes = exit_classes(runs)
    parts = []
    for c in sorted(classes):
        idx = classes[c]
        tag = "（run0）" if 0 in idx else ""
        parts.append(f"{c} × {len(idx)}{tag}")
    #end for
    lines.append("exit 分布 : " + "｜".join(parts))
    if len(classes) > 1:
        bits = sorted({plan.positions[i - 1]
                       for i in range(1, len(runs))
                       if runs[i].exit_code != r0.exit_code})
        lines.append("程序级    : exit 随翻转变化 ⇒ 输出 input-dependent"
                     f"（驱动位={bits}）")
    else:
        lines.append("程序级    : exit 恒定（本观测内无输出依赖证据）")
    #end if
    lines.append("未分析    : " +
                 ("无" if plan.unanalyzed == 0
                  else f"{plan.unanalyzed} 位姿未分析（stride 跳过）"))
    return "\n".join(lines) + "\n"
#end def


def flip(k: KTest, obj: int, byte: int, bit: int) -> FlipOutcome:
    """单位翻转：第 ``obj`` 对象第 ``byte`` 字节的第 ``bit`` 位取反
    （bit 0=最低位；返回新 KTest，原值不动）。

    obj/byte/bit 三向越界（含负数）当值 FlipBad；零尺寸对象任何位皆出界。
    """
    if not 0 <= obj < len(k.objects):
        return FlipBad(
            FlipBadReason.INDEX_OUT_OF_RANGE,
            f"object index {obj} out of range (have {len(k.objects)} object(s))",
        )
    #end if
    target = k.objects[obj]
    if not 0 <= byte < len(target.data):
        return FlipBad(
            FlipBadReason.INDEX_OUT_OF_RANGE,
            f"byte index {byte} out of range "
            f"(object {target.name!r} has {len(target.data)} byte(s))",
        )
    #end if
    if not 0 <= bit < 8:
        return FlipBad(
            FlipBadReason.INDEX_OUT_OF_RANGE,
            f"bit index {bit} out of range (0..7)",
        )
    #end if
    flipped = target.data[:byte] \
        + bytes([target.data[byte] ^ (1 << bit)]) \
        + target.data[byte + 1:]
    objects = k.objects[:obj] \
        + (KTestObject(name=target.name, data=flipped),) \
        + k.objects[obj + 1:]
    return KTest(version=k.version, args=k.args, sym_argvs=k.sym_argvs,
                 sym_argv_len=k.sym_argv_len, objects=objects)
#end def
