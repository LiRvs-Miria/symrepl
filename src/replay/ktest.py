"""ktest — KLEE `.ktest` 解析（引擎线 E1，纯读值代数）。

字节布局（全整数 uint32 **大端**；规格冻结源=ADR-0002，对 KLEE
lib/Basic/KTest.cpp 核对）：

    magic      : 5 字节——b"KTEST"（现行）或 b"BOUT\\n"（legacy），读侧双收
    version    : uint32 BE；>3 拒（新格式永不误析）；==1 无 sym 域；>=2 携 sym 两域
    numArgs    : uint32 BE
    args       : numArgs × string（uint32 BE 长度 + 裸字节）
    [v>=2]     : symArgvs uint32 BE, symArgvLen uint32 BE
    numObjects : uint32 BE
    objects    : numObjects × { name: string; numBytes: uint32 BE; data: numBytes 字节 }

语义（M1 对齐）：坏魔/超版/截断→KtestBad 当值返回（不抛）；截断=严格短读
（need>remaining 即败）；尾随字节容忍（KLEE 不读）；零尺寸对象合法；v1 无 sym
域报告 0/0。名/串解码 utf-8/surrogateescape——任意字节不炸。

本模块零 I/O 除 load（壳：open/read 转交 parse，OS 错误传播）。运行时零第三方
依赖、零 KLEE 安装要求（ADR-0001）。
"""
from __future__ import annotations

import os
import struct
from enum import Enum, auto
from typing import NamedTuple

KTEST_MAGIC = b"KTEST"
BOUT_MAGIC = b"BOUT\n"

#: magic 字段字节数。
MAGIC_SIZE = 5

#: 本读取器认的最高 `.ktest` 版本（KLEE 的 ``KTEST_VERSION``）。
CURRENT_KTEST_VERSION = 3

_UINT32 = struct.Struct(">I")


class KtestBadReason(Enum):
    BAD_MAGIC       = auto()   # 首五字节 KTEST/BOUT\n 都不中
    VERSION_TOO_NEW = auto()   # version > 3：拒误析新格式
    TRUNCATED       = auto()   # 严格短读：need > remaining
#end class

class KTestObject(NamedTuple):
    """一个符号输入对象：变量名+具体字节。"""
    name: str
    data: bytes
#end class

class KTest(NamedTuple):
    """一份解析后的 `.ktest`：命令行参数+符号输入对象。"""
    version: int
    args: tuple[str, ...]
    #: KLEE 记录的符号 argv 条数（v1 文件为 0）。
    sym_argvs: int
    #: KLEE 记录的符号 argv 每条长度（v1 文件为 0）。
    sym_argv_len: int
    objects: tuple[KTestObject, ...]
#end class

class KtestBad(NamedTuple):
    """解析失败（当值返回；reason 封闭枚举，detail 携人读事实）。"""
    reason: KtestBadReason
    detail: str
#end class

KtestOutcome = KTest | KtestBad     # Expect 代数：值或错，二选一


def _decode(raw: bytes) -> str:
    return raw.decode("utf-8", errors="surrogateescape")
#end def


class _Truncated(Exception):
    """私有控制流：短读 fail-fast；parse 边界单点转 KtestBad（不外泄）。"""
#end class


class _Reader:
    """字节缓冲游标：严格短读检查（need>remaining 即 _Truncated）。"""

    __slots__ = ("_data", "_pos")

    def __init__(self, data: bytes) -> None:
        self._data = data
        self._pos = 0
    #end def

    @property
    def remaining(self) -> int:
        return len(self._data) - self._pos
    #end def

    def take(self, count: int) -> bytes:
        if count > self.remaining:
            raise _Truncated(
                f"truncated .ktest: need {count} byte(s) at offset "
                f"{self._pos}, only {self.remaining} left"
            )
        #end if
        chunk = self._data[self._pos : self._pos + count]
        self._pos += count
        return chunk
    #end def

    def uint32(self) -> int:
        return _UINT32.unpack(self.take(_UINT32.size))[0]
    #end def

    def string(self) -> str:
        return _decode(self.take(self.uint32()))
    #end def
#end class


def parse(data: bytes) -> KtestOutcome:
    """解析 `.ktest` 字节串。

    坏 magic→BAD_MAGIC、version>3→VERSION_TOO_NEW、短读→TRUNCATED（当值返回，
    不抛）；尾随字节忽略（对齐 kTest_fromFile）。
    """
    reader = _Reader(data)
    try:
        magic = reader.take(MAGIC_SIZE)
        if magic not in (KTEST_MAGIC, BOUT_MAGIC):
            return KtestBad(KtestBadReason.BAD_MAGIC,
                            f"not a KTEST file (bad magic {magic!r})")
        #end if
        version = reader.uint32()
        if version > CURRENT_KTEST_VERSION:
            return KtestBad(
                KtestBadReason.VERSION_TOO_NEW,
                f"unsupported .ktest version {version} (reader knows up to "
                f"{CURRENT_KTEST_VERSION}); refusing to misparse a newer file"
            )
        #end if
        num_args = reader.uint32()
        args = tuple(reader.string() for _ in range(num_args))
        sym_argvs = 0
        sym_argv_len = 0
        if version >= 2:
            sym_argvs = reader.uint32()
            sym_argv_len = reader.uint32()
        #end if
        num_objects = reader.uint32()
        objects: list[KTestObject] = []
        for _ in range(num_objects):
            name = reader.string()
            size = reader.uint32()
            objects.append(KTestObject(name=name, data=reader.take(size)))
        #end for
        return KTest(version=version, args=args, sym_argvs=sym_argvs,
                     sym_argv_len=sym_argv_len, objects=tuple(objects))
    except _Truncated as e:
        return KtestBad(KtestBadReason.TRUNCATED, str(e))
    #end try
#end def


def load(path: str | os.PathLike[str]) -> KtestOutcome:
    """读入并解析 ``path`` 处的 `.ktest` 文件（壳：OS 错误照常传播）。"""
    with open(path, "rb") as f:
        return parse(f.read())
    #end with
#end def
