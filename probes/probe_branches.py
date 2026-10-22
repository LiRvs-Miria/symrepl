#!/usr/bin/env python3
"""probe_branches — E2 BranchSite 值代数与路径对比（纯件）验收（RED 2026-10-21）。

需求文本=docs/design-notes/replay E2 契约（三答定稿+步 2/步 3 两轮撞裁定形）：
- 取代裁决：无 oracle 臂（M1 代码完全不入境——本网为纯形状钉网）
- 纯值 ADT：BranchSite=(address, function, mnemonic) 三字段 NamedTuple，
  **无方法**（代数运算住模块函数，值类型不混 OOP）
- 识别：x86 专用单表（aarch64 不收），strip+lower 归一
- == 自下而上：字段各自 ==（int/str 原生）→ 单站点 == = 逐域合取（equal）
- 路径对比：map 高阶逐位（实际执行只负责记录实际 list；对比是独立代数）；
  长度不齐 → PathBad(LENGTH_MISMATCH, detail 携两侧长度) 当值不抛
- RED 预期：模块缺位=全查 FAIL；小步转录=已落地查 PASS、缺名查网炸 FAIL
  （模块属性访问，缺名不连坐）
"""
import sys
from pathlib import Path

REPLAY_DIR = Path(__file__).resolve().parent.parent / "src" / "replay"   # src/replay（引擎代数册）
sys.path.insert(0, str(REPLAY_DIR))

MODULE_OK = True
try:
    import branches
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


# ---- 构造助手 ----
def site(addr, fn="f", mn="je"):
    return branches.BranchSite(addr, fn, mn)
#end def


# ---- 查册（thunk；模块缺位时全查 FAIL=RED 基线） ----
CHECKS = []
def case(name, fn):
    CHECKS.append((name, fn))
#end def

case("形状：BranchSite 三字段 NamedTuple（address/function/mnemonic）",
     lambda: branches.BranchSite._fields
     == ("address", "function", "mnemonic"))

def _pure_value_pin():
    """纯值=类不自定义运算（看自身 __dict__，不看 tuple 继承面）；
    行为面 site+int 必炸 TypeError（tuple 拼接收不了 int）。"""
    if "__add__" in branches.BranchSite.__dict__:
        return False
    #end if
    try:
        site(0x1000) + 2
    except TypeError:
        return (not hasattr(branches.BranchSite, "equal")
                and not hasattr(branches.BranchSite, "match_path"))
    #end try
    return False
#end def

case("纯值：BranchSite 不自定义运算（代数在模块函数，不在值上）",
     _pure_value_pin)

case("识别：x86 条件成员 True（je/jge/loopnz/jecxz，空白+大小写归一）",
     lambda: all(branches.is_conditional(m) for m in
                 ("je", " JE ", "Jge", "loopnz", "jecxz")))

case("识别：非条件/未知 False（jmp/call/ret/mov/lea/push/b/空串）",
     lambda: not any(branches.is_conditional(m) for m in
                     ("jmp", "call", "ret", "mov", "lea", "push", "b", "")))

case("识别：aarch64 不收（b.eq/cbz/tbnz → False，2026-10-21 裁 x86 专用）",
     lambda: not branches.is_conditional("b.eq")
     and not branches.is_conditional("cbz")
     and not branches.is_conditional("tbnz"))

case("站点 ==：全字段同值 → True",
     lambda: branches.equal(site(0x1000, "fn", "je"),
                            site(0x1000, "fn", "je")) is True)

case("站点 ==：逐域合取，无地址捷径（同址异助记符/异函数 → False）",
     lambda: not branches.equal(site(0x1000, "fn", "je"),
                                site(0x1000, "fn", "jmp"))
     and not branches.equal(site(0x1000, "fn", "je"),
                            site(0x1000, "other", "je")))

case("整路：等长全同 → [True, True]",
     lambda: branches.match_path([site(0x1000), site(0x1002)],
                                 [site(0x1000), site(0x1002)])
     == [True, True])

case("整路：等长逐位 map（True/False 各就各位）",
     lambda: branches.match_path(
         [site(0x1000, "f", "je"), site(0x2000, "g", "jge")],
         [site(0x1000, "f", "je"), site(0x2000, "g", "jne")])
     == [True, False])

case("整路：长度不齐 → PathBad(LENGTH_MISMATCH)，detail 携两侧长度",
     lambda: _length_pin())

case("整路：空输入 → 空列表（平凡）",
     lambda: branches.match_path([], []) == [])

case("错误代数：reason 封闭单成员 LENGTH_MISMATCH",
     lambda: {m.name for m in branches.PathBadReason}
     == {"LENGTH_MISMATCH"})


def _length_pin():
    r = branches.match_path([site(0x1000), site(0x1002)],
                            [site(0x1000)])
    return (isinstance(r, branches.PathBad)
            and r.reason is branches.PathBadReason.LENGTH_MISMATCH
            and isinstance(r.detail, str) and r.detail != ""
            and "2" in r.detail and "1" in r.detail)
#end def


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
        print(f"cleanroom branches 模块缺位（RED 全红基线）：{_IMPORT_DETAIL}")
        for name, _ in CHECKS:
            check(name, False)
        #end for
    #end if
    print("=" * 30)
    print("ALL PASS" if not fails else f"{len(fails)} FAIL: {fails}")
    sys.exit(1 if fails else 0)
#end if
