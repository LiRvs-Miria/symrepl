"""pathmark — .ll 静态符号性分析（M3 路径缩小，纯逻辑）。

klee_make_symbolic 调用点标记内存 → ``load``/``icmp`` 传播 → 直接调用
实参→形参绑定 → ``br`` 条件测闭包成员。行级 join 键 = (函数名,
``!DILocation`` line)。Expect 代数：parse 坏当值 ParseBad 不抛（launch
期 Rejected 的底座）。本模块零 I/O、零调试器依赖（只消费公开产物 .ll）。

已知边界（真撞到再扩）：指针别名、间接调用、getelementptr 数组地址、
binop 链不传播；轻量解析器只对 clang ``-O0 -g`` 文本形状负责。
"""
from __future__ import annotations

import re
from enum import Enum, auto
from typing import NamedTuple


class ForkSite(NamedTuple):
    """一个 fork 站点：函数名 + 源行（join 键；行来自 br 的 ``!dbg`` 解引用）。"""

    function: str
    line: int
#end class


class ParseBadReason(Enum):
    NO_MODULE = auto()       # 模块文本里没有任何 define
    UNTERMINATED = auto()    # define 主体未闭合到文件尾（截断件）
    WIRE_BAD = auto()        # 结构化下发形状不符（父→子过线面）
#end class


class ParseBad(NamedTuple):
    """解析失败（当值返回；reason 封闭，detail 携人读事实）。"""

    reason: ParseBadReason
    detail: str
#end class


class FnDef(NamedTuple):
    """单个函数定义：形参 % 名（按位序）+ 指令行原文（去首尾空白）。"""

    formals: tuple[str, ...]
    body: tuple[str, ...]
#end class


class LlModule(NamedTuple):
    """解析后的模块纯值：函数表 + ``!N`` → 行号元数据表。"""

    functions: dict[str, FnDef]
    dbg_lines: dict[str, int]
#end class


_VALUE_RE = re.compile(r"%[-a-zA-Z$._0-9]+")
_DEFINE_RE = re.compile(r"^define\b[^()]*@([-\w$.]+)\s*\(([^)]*)\)")
_DILOCATION_RE = re.compile(r"^!(\d+)\s*=\s*!DILocation\(line:\s*(\d+)")
_LOAD_RE = re.compile(r"^(%[-a-zA-Z$._0-9]+)\s*=\s*load\b")
_ICMP_RE = re.compile(r"^(%[-a-zA-Z$._0-9]+)\s*=\s*(?:icmp|fcmp)\b")
_STORE_RE = re.compile(r"^store\b")
_BR_RE = re.compile(r"\bbr\s+i1\s+(%[-a-zA-Z$._0-9]+)")
_CALL_RE = re.compile(r"\bcall\b[^()]*@([-\w$.]+)\(")
_DBG_RE = re.compile(r"!dbg\s+!(\d+)")
_MAKE_SYMBOLIC = "klee_make_symbolic"


def parse_module(text: str) -> LlModule | ParseBad:
    """模块文本 → LlModule 纯值；坏件（无 define/主体截断）→ ParseBad 当值。"""
    dbg_lines: dict[str, int] = {}
    functions: dict[str, FnDef] = {}
    name: str | None = None
    formals: tuple[str, ...] = ()
    body: list[str] = []
    for raw in text.splitlines():
        if name is None:
            m = _DILOCATION_RE.match(raw)
            if m is not None:
                dbg_lines[m.group(1)] = int(m.group(2))
                continue
            #end if
            m = _DEFINE_RE.match(raw)
            if m is not None:
                name = m.group(1)
                formals = tuple(_VALUE_RE.findall(m.group(2)))
                body = []
            #end if
            continue
        #end if
        if raw.strip() == "}":
            functions[name] = FnDef(formals=formals, body=tuple(body))
            name = None
            continue
        #end if
        body.append(raw.strip())
    #end for
    if name is not None:
        return ParseBad(ParseBadReason.UNTERMINATED,
                        f"function {name!r} body not closed at end of file")
    if not functions:
        return ParseBad(ParseBadReason.NO_MODULE,
                        "no function definition in module text")
    return LlModule(functions=functions, dbg_lines=dbg_lines)
#end def


def has_function(module: LlModule, name: str) -> bool:
    """函数名是否为模块内定义（噪声A 判据原语：不在=KLEE 世界不存在的代码）。"""
    return name in module.functions
#end def


def classify(fork_set: frozenset[ForkSite], module: LlModule,
             function: str, line: int) -> tuple[str, str]:
    """站点三分类（三分类本体）：回 (kind, detail)。kind 二值 fork|runtime；
    detail 三判据=symbolic-branch（条件 ∈ 符号闭包）/function-not-in-module
    （函数不在模块=KLEE 世界不存在的代码）/concrete-condition（条件在闭包外）。"""
    if not has_function(module, function):
        return ("runtime", "function-not-in-module")
    #end if
    if ForkSite(function, line) in fork_set:
        return ("fork", "symbolic-branch")
    #end if
    return ("runtime", "concrete-condition")
#end def


def to_wire(module: LlModule) -> dict:
    """模块值→结构化下发形状（ktest sym_objects 家法：父面解析一次，对象过线，
    子面零 .ll 文件 I/O）。"""
    return {"functions": {name: {"formals": list(fn.formals),
                                 "body": list(fn.body)}
                          for name, fn in module.functions.items()},
            "dbg_lines": dict(module.dbg_lines)}
#end def


def from_wire(data) -> LlModule | ParseBad:
    """结构化下发形状→模块值；形状不符→ParseBad(WIRE_BAD) 当值不抛。"""
    if not isinstance(data, dict):
        return ParseBad(ParseBadReason.WIRE_BAD, "wire payload must be an object")
    #end if
    functions = data.get("functions")
    dbg = data.get("dbg_lines")
    if not isinstance(functions, dict) or not isinstance(dbg, dict):
        return ParseBad(ParseBadReason.WIRE_BAD,
                        "'functions'/'dbg_lines' must be objects")
    #end if
    try:
        mods = {name: FnDef(formals=tuple(fn["formals"]), body=tuple(fn["body"]))
                for name, fn in functions.items()}
        dbg_lines = {str(k): int(v) for k, v in dbg.items()}
    except (KeyError, TypeError, ValueError) as e:
        return ParseBad(ParseBadReason.WIRE_BAD, f"bad module entry: {e!r}")
    #end try
    return LlModule(functions=mods, dbg_lines=dbg_lines)
#end def


def fork_sites(module: LlModule) -> list[ForkSite]:
    """符号闭包不动点：返回全部 fork 站点（按 (函数名, 行) 排序）。

    闭包两域：values（符号 SSA 值，按函数）与 cells（符号内存槽，按函数）。
    种子=make_symbolic 首实参；传播臂=store 值→槽、load 槽→值、icmp/fcmp
    任一操作数、直接调用实参→形参；br 条件 ∈ values 即 fork 站点（无 ``!dbg``
    或元数据缺行的站点不出——join 不了的不注记）。
    """
    values: dict[str, set[str]] = {n: set() for n in module.functions}
    cells: dict[str, set[str]] = {n: set() for n in module.functions}
    sites: set[ForkSite] = set()
    while True:
        changed = False
        for fname, fn in module.functions.items():
            for line in fn.body:
                if _scan_line(fname, line, module, values, cells, sites):
                    changed = True
                #end if
            #end for
        #end for
        if not changed:
            break
        #end if
    #end while
    return sorted(sites)
#end def


def _add(target: set, item) -> bool:
    """集入（报告是否新入=不动点推进信号）。"""
    if item in target:
        return False
    target.add(item)
    return True
#end def


def _call_args(line: str, open_idx: int) -> tuple[str, ...]:
    """取调用实参原文（open_idx=实参表左括号下标；顶层逗号切分）。"""
    close = line.rfind(")")
    if close <= open_idx:
        return ()
    return tuple(line[open_idx + 1:close].split(","))
#end def


def _scan_line(fname: str, line: str, module: LlModule,
               values: dict[str, set[str]], cells: dict[str, set[str]],
               sites: set[ForkSite]) -> bool:
    """单行传播臂（入集即报 True=不动点未稳）。"""
    m = _LOAD_RE.match(line)
    if m is not None:
        toks = _VALUE_RE.findall(line)
        if len(toks) >= 2 and toks[-1] in cells[fname]:
            return _add(values[fname], toks[0])
        return False
    #end if
    m = _ICMP_RE.match(line)
    if m is not None:
        toks = _VALUE_RE.findall(line)
        if any(t in values[fname] for t in toks[1:]):
            return _add(values[fname], toks[0])
        return False
    #end if
    if _STORE_RE.match(line) is not None:
        toks = _VALUE_RE.findall(line)
        if len(toks) >= 2 and toks[0] in values[fname]:
            return _add(cells[fname], toks[-1])
        return False
    #end if
    m = _BR_RE.search(line)
    if m is not None:
        if m.group(1) in values[fname]:
            dm = _DBG_RE.search(line)
            if dm is not None:
                line_no = module.dbg_lines.get(dm.group(1))
                if line_no is not None:
                    return _add(sites, ForkSite(fname, line_no))
                #end if
            #end if
        #end if
        return False
    #end if
    m = _CALL_RE.search(line)
    if m is not None:
        callee = m.group(1)
        args = _call_args(line, m.end() - 1)
        if not args:
            return False
        #end if
        if callee == _MAKE_SYMBOLIC:
            first = _VALUE_RE.findall(args[0])
            if first:
                return _add(cells[fname], first[0])
            return False
        #end if
        if callee in module.functions:
            changed = False
            for arg, formal in zip(args, module.functions[callee].formals):
                toks = _VALUE_RE.findall(arg)
                if toks and toks[0] in values[fname]:
                    changed = _add(values[callee], formal) or changed
                #end if
            #end for
            return changed
        #end if
        return False
    #end if
    return False
#end def
