"""branches — BranchSite 值代数与路径对比（引擎线 E2，纯逻辑）。

函数式代数：BranchSite 为纯值（无方法，运算住模块函数）；相等自下而上
组合——字段各自 ``==``、单站点 = 逐域合取、路径对比 = ``map`` 逐位。
长度不齐当值 PathBad，全程不抛。本模块零 I/O、零调试器依赖（ADR-0001：
只消费公开格式）。
"""
from __future__ import annotations

from enum import Enum, auto
from typing import NamedTuple


class BranchSite(NamedTuple):
    """一个分支站点：地址+函数名+助记符（纯值；来源侧各域自足——目标侧
    三域全知，回放侧 address/function 取自调用栈、mnemonic 取自反汇编）。"""

    address: int
    function: str
    mnemonic: str
#end class


#: x86/x86_64 条件跳转助记符（AT&T 与 Intel 拼写在条件跳转上一致；尺寸
#: 后缀只出现在无条件 ``jmp`` 上，不设双拼写）。
_X86_CONDITIONAL = frozenset(
    {
        "ja", "jae", "jb", "jbe", "jc", "je", "jg", "jge", "jl", "jle",
        "jna", "jnae", "jnb", "jnbe", "jnc", "jne", "jng", "jnge",
        "jnl", "jnle", "jno", "jnp", "jns", "jo", "jp", "js", "jz",
        "jecxz", "jrcxz",
        "loop", "loope", "loopne", "loopnz", "loopz",
    }
)


def is_conditional(mnemonic: str) -> bool:
    """判定单条助记符是否条件跳转（空白+大小写归一后查表）。"""
    return mnemonic.strip().lower() in _X86_CONDITIONAL
#end def


class PathBadReason(Enum):
    LENGTH_MISMATCH = auto()   # 实际路径与目标路径长度不齐，逐位对比无定义
#end class


class PathBad(NamedTuple):
    """路径对比失败（当值返回；reason 封闭，detail 携人读事实）。"""

    reason: PathBadReason
    detail: str
#end class


MatchOutcome = list[bool] | PathBad   # Expect 代数：逐位 == 结果，或错误


def equal(a: BranchSite, b: BranchSite) -> bool:
    """单站点 ==：address/function/mnemonic 各域相等，逐域合取。"""
    return (a.address == b.address
            and a.function == b.function
            and a.mnemonic == b.mnemonic)
#end def


def match_path(actual: list[BranchSite],
               target: list[BranchSite]) -> MatchOutcome:
    """路径对比：``map equal actual target`` 逐位；长度不齐 → PathBad 当值。"""
    if len(actual) != len(target):
        return PathBad(
            PathBadReason.LENGTH_MISMATCH,
            f"path length mismatch: actual {len(actual)}"
            f" vs target {len(target)}")
    #end if
    return list(map(equal, actual, target))
#end def
