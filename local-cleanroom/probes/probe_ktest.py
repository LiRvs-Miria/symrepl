#!/usr/bin/env python3
"""probe_ktest — E1 ktest 解析重写（引擎线首件，纯读）验收（RED 起步，2026-10-20）。

需求文本=replay/design-notes E1 契约（ADR-0002 字节布局冻结+M1 ktest.py 行为规格；
零代码继承：M1 只当 oracle，代码不入境）：
- Expect 代数：parse/load 返回 KTest | KtestBad（当值不抛）；错误 reason 封闭枚举
  （BAD_MAGIC/VERSION_TOO_NEW/TRUNCATED，FrameBad 家法同构）
- oracle 对账：真 ktest 六件+合成字节案例喂双解析器——语料册先对预期自检（oracle 合
  契约行为），再成臂逐字段 diff、败臂按 M1 异常消息前缀对位 reason
- 值形状钉：NamedTuple 字段面/v1 sym 0-0/零尺寸对象/非 UTF-8 名/version 0 合法
- load：真路径=parse(bytes) 同果；缺文件 FileNotFoundError 照 M1 传播（壳路 I/O）
- RED 预期：cleanroom 模块缺位=全查 FAIL（纯件新册无桩可对，清单即需求文本）
"""
import struct, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "local-cleanroom" / "replay"))
sys.path.insert(0, str(ROOT / "src"))          # M1 oracle（行为对账，非代码继承）

MODULE_OK = True
try:
    from ktest import KTest, KTestObject, KtestBad, KtestBadReason, load, parse
except ImportError as e:
    MODULE_OK = False
    _IMPORT_DETAIL = f"{type(e).__name__}: {e}"

from symrepl.replay.ktest import parse as m1_parse   # oracle 缺位=网不成立，硬炸

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


# ---- 语料册：name → (bytes, 预期)；预期=("ok",) 或 ("bad", M1 消息前缀) ----
V3 = kfile(b"KTEST", 3, [kstr("prog")], (2, 4), [obj("a", b"\x01\x02\x03")])
V1 = kfile(b"KTEST", 1, [kstr("prog")], None, [obj("a", b"z")])
V0 = blob(b"KTEST", u32(0), u32(0), u32(0))                  # v0：无 sym 域，合法
CORPUS = [
    ("合成 v3 全字段",           V3,                                         ("ok",)),
    ("合成 v1 无 sym 域",        V1,                                         ("ok",)),
    ("合成 v2 有 sym 域",        kfile(b"KTEST", 2, [], (1, 3), [obj("b", b"")]),   ("ok",)),
    ("合成 v0（仅 >3 拒）",      V0,                                         ("ok",)),
    ("合成 BOUT\\n legacy 魔",   kfile(b"BOUT\n", 3, [kstr("prog")], (2, 4), [obj("a", b"\x01\x02\x03")]),   ("ok",)),
    ("尾随字节容忍",             V3 + b"JUNKJUNK",                           ("ok",)),
    ("非 UTF-8 对象名",          kfile(b"KTEST", 3, [], (0, 0), [obj(b"\xff\xfe-na\xc3me", b"x")]),   ("ok",)),
    ("零尺寸对象合法",           kfile(b"KTEST", 3, [], (0, 0), [obj("z", b"")]),   ("ok",)),
    ("坏 magic",                 blob(b"KTEXX", u32(3)),                     ("bad", "not a KTEST file")),
    ("版本 4 拒误析",            blob(b"KTEST", u32(4), u32(0), u32(0)),     ("bad", "unsupported .ktest version")),
    ("版本 0xFFFFFFFF 拒",       blob(b"KTEST", u32(0xFFFFFFFF), u32(0)),    ("bad", "unsupported .ktest version")),
    ("截断：3 字节半魔",         b"KTE",                                     ("bad", "truncated .ktest")),
    ("截断：args 数虚高",        blob(b"KTEST", u32(3), u32(5), kstr("only-one")),   ("bad", "truncated .ktest")),
    ("截断：对象数据半途",       blob(b"KTEST", u32(3), u32(0), u32(0), u32(0), u32(1), kstr("a"), u32(99), b"\x01\x02"),   ("bad", "truncated .ktest")),
    ("截断：numObjects 虚高",    blob(b"KTEST", u32(3), u32(0), u32(0), u32(0), u32(9), obj("a", b"z")),   ("bad", "truncated .ktest")),
    ("快败：海量对象数声明",     blob(b"KTEST", u32(3), u32(0), u32(0), u32(0), u32(0xFFFFFFFF)),   ("bad", "truncated .ktest")),
    ("快败：超大 numBytes 声明", blob(b"KTEST", u32(3), u32(0), u32(0), u32(0), u32(1), kstr("a"), u32(0xFFFFFFF0)),   ("bad", "truncated .ktest")),
]
REAL_FILES = sorted(KTESTS.glob("*.ktest"))

REASON_OF = {"not a KTEST file": "BAD_MAGIC",
             "unsupported .ktest version": "VERSION_TOO_NEW",
             "truncated .ktest": "TRUNCATED"}


# ---- oracle 对账（M1=规格 oracle：定义败类与值形状，cleanroom 认类对齐） ----
def m1_outcome(data: bytes):
    """M1 parse → ("ok", KTest) | ("bad", 消息前缀)。"""
    try:
        return ("ok", m1_parse(data))
    except Exception as e:
        msg = str(e)
        for prefix in REASON_OF:
            if msg.startswith(prefix):
                return ("bad", prefix)
            #end if
        #end for
        return ("bad", f"UNMAPPED-M1-ERROR: {msg}")
    #end try
#end def

def same_value(m1_kt, cr_kt) -> bool:
    """成臂逐字段 diff（M1 dataclass ↔ cleanroom NamedTuple，按名取）。"""
    if (m1_kt.version, m1_kt.args, m1_kt.sym_argvs, m1_kt.sym_argv_len) != \
       (cr_kt.version, cr_kt.args, cr_kt.sym_argvs, cr_kt.sym_argv_len):
        return False
    #end if
    if len(m1_kt.objects) != len(cr_kt.objects):
        return False
    #end if
    return all(o1.name == o2.name and o1.data == o2.data
               for o1, o2 in zip(m1_kt.objects, cr_kt.objects))
#end def

def diff_case(expect, data: bytes) -> bool:
    """一例三钉：oracle 合预期（语料册自检）+cleanroom 与 oracle 同臂同形。"""
    m1 = m1_outcome(data)
    if m1[0] != expect[0] or (m1[0] == "bad" and m1[1] != expect[1]):
        return False                     # oracle 与契约预期打架=语料册错，当场见
    #end if
    if not MODULE_OK:
        return False
    #end if
    cr = parse(data)
    if m1[0] == "ok":
        return cr is not None and not isinstance(cr, KtestBad) and same_value(m1[1], cr)
    #end if
    return isinstance(cr, KtestBad) and cr.reason.name == REASON_OF[m1[1]]
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
    data = kfile(b"KTEST", 3, [], (0, 0), [obj(b"\xff\xfe-na\xc3me", b"x")])
    r, m1 = parse(data), m1_outcome(data)
    return (not isinstance(r, KtestBad) and m1[0] == "ok"
            and r.objects[0].name == m1[1].objects[0].name)   # 与 M1 同译
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
    return same_value(pp, lp)
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

case(f"对账：合成 {len(CORPUS)} 例（oracle 合预期+双解析器同臂同形）",
     lambda: all(diff_case(expect, data) for _, data, expect in CORPUS))
case(f"对账：真 ktest {len(REAL_FILES)} 件（oracle 全收+双解析器同形）",
     lambda: all(diff_case(("ok",), p.read_bytes()) for p in REAL_FILES))
case("形状：NamedTuple 字段面 M1 同款",
     lambda: KTest._fields == ("version", "args", "sym_argvs", "sym_argv_len", "objects")
     and KTestObject._fields == ("name", "data"))
case("形状：v1 无 sym 域报告 0/0（M1 同款）", _v1_pin)
case("形状：非 UTF-8 名 surrogateescape 不炸且与 M1 同译", _name_pin)
case("形状：尾随字节不读（numObjects 耗尽即停，与 M1 同值）",
     lambda: diff_case(("ok",), V3 + b"JUNKJUNK"))
case("形状：version 0 合法（仅 >3 拒，M1 同款）",
     lambda: diff_case(("ok",), V0))
case("形状：KtestBad 携 reason 枚举成员+detail 串", _bad_shape_pin)
case("load：真路径同 parse(bytes)（六件同果）",
     lambda: all(_load_matches_parse(p) for p in REAL_FILES))
case("load：缺文件 FileNotFoundError 传播（M1 同款，壳路 I/O）", _load_missing_pin)


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
        print(f"cleanroom ktest 模块缺位（RED 全红基线）：{_IMPORT_DETAIL}")
        for name, _ in CHECKS:
            check(name, False)
        #end for
    #end if
    print("=" * 30)
    print("ALL PASS" if not fails else f"{len(fails)} FAIL: {fails}")
    sys.exit(1 if fails else 0)
#end if
