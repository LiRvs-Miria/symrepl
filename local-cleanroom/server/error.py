from typing import NamedTuple
from enum import IntEnum

# 进程退出码（8 位约束 0-255；粗类分层：日志记细粒度 reason，退出码只分大类）
# 代数：0=体面收场（eof / 信号优雅退出，daemon 惯例 SIGTERM→0），
#       2=传输暴毙（RST/EPIPE 合并，对齐 §4 实证锚点 RST→2）；
#       未捕获 bug 炸穿 = implicit 行：解释器默认 exit 1 + traceback，不建代码
class TransExitCode(IntEnum):
    FIN = 0
    ABRUPT = 2
#end class

# 会话错误码（代数：1xxx=协议层，2xxx=上游，3xxx=执行面 LLDB 会话原语；无兜底，炸穿直接补专属码）
class InternalErrCode(IntEnum):
    OK = 0
    BAD_PROTOCOL = 1001
    PAYLOAD_TOO_LARGE = 1002
    EVENT_NOT_ALLOWED = 1003   # 事件在当前协议状态不允许（A1 决策：粗类码，细节走 detail）
    UPSTREAM_TIMEOUT = 2001
    UPSTREAM_REFUSED = 2002
#end class

# 执行面错误码（调试器后端适配器壳路异常用；纯核错误当值返回不经此）
class SessionErrCode(IntEnum):
    DEBUGGER_EXISTS = 3001     # 单进程单 debugger 契约（复用 no-op 坑）
    BAD_TARGET = 3002
    LAUNCH_FAILED = 3003
    ENTRY_NOT_STOPPED = 3004
    EVENT_TIMEOUT = 3005
    NOT_EXITED = 3006
    NO_LLDB = 3007
    NO_LLDB_SERVER = 3008
    BAD_ARGS = 3009
    RESUME_FAILED = 3010
    NOT_STOPPED = 3011         # 相位守卫：该操作要求 stopped/entry_stopped
    NOT_LAUNCHED = 3012        # 相位守卫：该操作要求 entry_stopped
    NO_THREAD = 3013           # 线程号不存在（GetThreadByID 无效）
    ALREADY_LAUNCHED = 3014    # 相位守卫：该操作要求 launch 前（参考 already_launched 同款）
#end class

class SessionError(RuntimeError):
    """调试器后端原语失败（壳路异常）；码族 SessionErrCode，构造期逼显式无默认。
    端口面（session.py）捕获译成 wire 错误；适配器内当值抛出。"""
    def __init__(self, code: SessionErrCode, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
    #end def
#end class

class ProtoError(NamedTuple):
    code: InternalErrCode        # 无默认：类型系统逼每处显式选码（默认 OK = 静默成功地雷）
    message: str = ""
    detail: dict | None = None
#end class
