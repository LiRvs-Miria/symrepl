#!/usr/bin/env python3
"""probe_ktest — E1 ktest 解析重写（引擎线首件，纯读）验收（RED 起步，2026-10-20）。

需求文本=docs/design-notes/replay E1 契约（ADR-0002 字节布局冻结）：
- Expect 代数：parse/load 返回 KTest | KtestBad（当值不抛）；错误 reason 封闭枚举
  （BAD_MAGIC/VERSION_TOO_NEW/TRUNCATED，FrameBad 家法同构）
- 行为钉：语料册 17 例+真件 6 件，ok 臂钉字段值、bad 臂钉 reason——真值于
  2026-10-22 自 M1 oracle 收割一次后钉死（M1 随取代裁决退役，本网改纯形状钉，
  E2 无-oracle 先例）；收割存档见 git 历史（probe 本文件前版）
- 值形状钉：NamedTuple 字段面/v1 sym 0-0/零尺寸对象/非 UTF-8 名/version 0 合法
- load：真路径=parse(bytes) 同果；缺文件 FileNotFoundError 传播（壳路 I/O）
- RED 预期：模块缺位=全查 FAIL（纯件新册无桩可对，清单即需求文本）
"""
import struct, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent            # 仓库根（third_party 定位）
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src" / "replay"))   # src/replay（引擎代数册）

MODULE_OK = True
try:
    from ktest import KTest, KTestObject, KtestBad, KtestBadReason, load, parse
except ImportError as e:
    MODULE_OK = False
    _IMPORT_DETAIL = f"{type(e).__name__}: {e}"
#end try

KTESTS = ROOT / "third_party" / "klee" / "ktest"
fails = []

def check(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        fails.append(name)
    #end if
#end def


# ---- 线格式助手（uint32 BE；string=长度前缀+裸字节；对象=名串+尺寸+数据） ----
def u32(n: int) -> bytes:
    return struct.pack(">I", n)
#end def

def blob(*parts: bytes) -> bytes:
    return b"".join(parts)
#end def

def kstr(x) -> bytes:
    raw = x.encode("utf-8", "surrogateescape") if isinstance(x, str) else x
    return u32(len(raw)) + raw
#end def

def obj(name, data: bytes) -> bytes:
    return kstr(name) + u32(len(data)) + data
#end def

def kfile(magic: bytes, ver: int, args: list[bytes], sym: tuple[int, int],
          objs: list[bytes]) -> bytes:
    """合法 ktest 文件构造（布局：magic/version/numArgs/args/[sym×2]/numObjects/objs）。"""
    out = blob(magic, u32(ver), u32(len(args)), *args)
    if ver >= 2:
        out += blob(u32(sym[0]), u32(sym[1]))
    #end if
    return out + blob(u32(len(objs)), *objs)
#end def


# ---- 语料册：name → (bytes, 预期)；预期=("ok", 值钉) 或 ("bad", reason 名) ----
V3 = kfile(b"KTEST", 3, [kstr("prog")], (2, 4), [obj("a", b"\x01\x02\x03")])
V1 = kfile(b"KTEST", 1, [kstr("prog")], None, [obj("a", b"z")])
V0 = blob(b"KTEST", u32(0), u32(0), u32(0))                  # v0：无 sym 域，合法
BADNAME = kfile(b"KTEST", 3, [], (0, 0), [obj(b"\xff\xfe-na\xc3me", b"x")])
# 值钉形状=(version, args, sym_argvs, sym_argv_len, [(name, data_hex)])——2026-10-22 自 M1 收割
def val(ver, args, symv, syml, objs):
    return (ver, tuple(args), symv, syml, [(n, bytes.fromhex(h)) for n, h in objs])
#end def
CORPUS = [
    ("合成 v3 全字段",           V3,            ("ok", val(3, ["prog"], 2, 4, [("a", "010203")]))),
    ("合成 v1 无 sym 域",        V1,            ("ok", val(1, ["prog"], 0, 0, [("a", "7a")]))),
    ("合成 v2 有 sym 域",        kfile(b"KTEST", 2, [], (1, 3), [obj("b", b"")]),   ("ok", val(2, [], 1, 3, [("b", "")]))),
    ("合成 v0（仅 >3 拒）",      V0,            ("ok", val(0, [], 0, 0, []))),
    ("合成 BOUT\\n legacy 魔",   kfile(b"BOUT\n", 3, [kstr("prog")], (2, 4), [obj("a", b"\x01\x02\x03")]),   ("ok", val(3, ["prog"], 2, 4, [("a", "010203")]))),
    ("尾随字节容忍",             V3 + b"JUNKJUNK",   ("ok", val(3, ["prog"], 2, 4, [("a", "010203")]))),
    ("非 UTF-8 对象名",          BADNAME,       ("ok", val(3, [], 0, 0, [("\udcff\udcfe-na\udcc3me", "78")]))),
    ("零尺寸对象合法",           kfile(b"KTEST", 3, [], (0, 0), [obj("z", b"")]),   ("ok", val(3, [], 0, 0, [("z", "")]))),
    ("坏 magic",                 blob(b"KTEXX", u32(3)),                     ("bad", "BAD_MAGIC")),
    ("版本 4 拒误析",            blob(b"KTEST", u32(4), u32(0), u32(0)),     ("bad", "VERSION_TOO_NEW")),
    ("版本 0xFFFFFFFF 拒",       blob(b"KTEST", u32(0xFFFFFFFF), u32(0)),    ("bad", "VERSION_TOO_NEW")),
    ("截断：3 字节半魔",         b"KTE",                                     ("bad", "TRUNCATED")),
    ("截断：args 数虚高",        blob(b"KTEST", u32(3), u32(5), kstr("only-one")),   ("bad", "TRUNCATED")),
    ("截断：对象数据半途",       blob(b"KTEST", u32(3), u32(0), u32(0), u32(0), u32(1), kstr("a"), u32(99), b"\x01\x02"),   ("bad", "TRUNCATED")),
    ("截断：numObjects 虚高",    blob(b"KTEST", u32(3), u32(0), u32(0), u32(0), u32(9), obj("a", b"z")),   ("bad", "TRUNCATED")),
    ("快败：海量对象数声明",     blob(b"KTEST", u32(3), u32(0), u32(0), u32(0), u32(0xFFFFFFFF)),   ("bad", "TRUNCATED")),
    ("快败：超大 numBytes 声明", blob(b"KTEST", u32(3), u32(0), u32(0), u32(0), u32(1), kstr("a"), u32(0xFFFFFFF0)),   ("bad", "TRUNCATED")),
]
# 真件值钉（同收割，2026-10-22）
REAL_PINS = {
    "get_sign_test000001.ktest": val(3, ["get_sign.ll"], 0, 0, [("a", "00000000")]),
    "get_sign_test000002.ktest": val(3, ["get_sign.ll"], 0, 0, [("a", "01010101")]),
    "get_sign_test000003.ktest": val(3, ["get_sign.ll"], 0, 0, [("a", "00000080")]),
    "two_objs_test000001.ktest": val(3, ["two_objs.ll"], 0, 0, [("arr", "00000000"), ("b", "0000")]),
    "two_objs_test000002.ktest": val(3, ["two_objs.ll"], 0, 0, [("arr", "41ffffff"), ("b", "ff01")]),
    "two_objs_test000003.ktest": val(3, ["two_objs.ll"], 0, 0, [("arr", "41ffffff"), ("b", "0000")]),
}
REAL_FILES = sorted(KTESTS.glob("*.ktest"))


def value_matches(cr, pin) -> bool:
    """ok 臂逐字段对值钉。"""
    return (cr.version, cr.args, cr.sym_argvs, cr.sym_argv_len) == pin[:4] \
        and [(o.name, o.data) for o in cr.objects] == pin[4]
#end def

def pin_case(expect, data: bytes) -> bool:
    """一例双臂：ok=KTest 值钉 / bad=KtestBad.reason 名。"""
    if not MODULE_OK:
        return False
    #end if
    cr = parse(data)
    if expect[0] == "ok":
        return cr is not None and not isinstance(cr, KtestBad) and value_matches(cr, expect[1])
    #end if
    return isinstance(cr, KtestBad) and cr.reason.name == expect[1]
#end def


# ---- 形状钉助手 ----
def _v1_pin() -> bool:
    r = parse(V1)
    return (not isinstance(r, KtestBad) and r.version == 1
            and r.sym_argvs == 0 and r.sym_argv_len == 0
            and r.args == ("prog",) and r.objects[0].name == "a"
            and r.objects[0].data == b"z")
#end def

def _name_pin() -> bool:
    r = parse(BADNAME)
    return (not isinstance(r, KtestBad)
            and r.objects[0].name == "\udcff\udcfe-na\udcc3me")   # surrogateescape 逐字节同译
#end def

def _bad_shape_pin() -> bool:
    r = parse(b"KTE")
    return (isinstance(r, KtestBad) and isinstance(r.reason, KtestBadReason)
            and isinstance(r.detail, str) and r.detail != "")
#end def

def _load_matches_parse(p: Path) -> bool:
    lp, pp = load(str(p)), parse(p.read_bytes())
    if isinstance(lp, KtestBad) or isinstance(pp, KtestBad):
        return type(lp) is type(pp)
    #end if
    return lp == pp                              # NamedTuple 逐字段相等
#end def

def _load_missing_pin() -> bool:
    try:
        load(str(KTESTS / "no-such-file.ktest"))
        return False
    except FileNotFoundError:
        return True
    #end try
#end def


# ---- 查册（thunk；模块缺位时全查 FAIL=RED 基线） ----
CHECKS = []
def case(name, fn):
    CHECKS.append((name, fn))
#end def

case(f"语料册 {len(CORPUS)} 例（ok 值钉/bad reason 名，真值 2026-10-22 自 M1 收割）",
     lambda: all(pin_case(expect, data) for _, data, expect in CORPUS))
case(f"真 ktest {len(REAL_FILES)} 件（值钉照收割表）",
     lambda: len(REAL_FILES) == 6 and all(
         pin_case(("ok", REAL_PINS[p.name]), p.read_bytes()) for p in REAL_FILES))
case("形状：NamedTuple 字段面家法同款",
     lambda: KTest._fields == ("version", "args", "sym_argvs", "sym_argv_len", "objects")
     and KTestObject._fields == ("name", "data"))
case("形状：v1 无 sym 域报告 0/0", _v1_pin)
case("形状：非 UTF-8 名 surrogateescape 不炸（逐字节同译）", _name_pin)
case("形状：尾随字节不读（numObjects 耗尽即停）",
     lambda: pin_case(CORPUS[5][2], CORPUS[5][1]))
case("形状：version 0 合法（仅 >3 拒）",
     lambda: pin_case(CORPUS[3][2], CORPUS[3][1]))
case("形状：KtestBad 携 reason 枚举成员+detail 串", _bad_shape_pin)
case("load：真路径同 parse(bytes)（六件同果）",
     lambda: all(_load_matches_parse(p) for p in REAL_FILES))
case("load：缺文件 FileNotFoundError 传播（壳路 I/O）", _load_missing_pin)


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
        print(f"ktest 模块缺位（RED 全红基线）：{_IMPORT_DETAIL}")
        for name, _ in CHECKS:
            check(name, False)
        #end for
    #end if
    print("=" * 30)
    print("ALL PASS" if not fails else f"{len(fails)} FAIL: {fails}")
    sys.exit(1 if fails else 0)
#end if
