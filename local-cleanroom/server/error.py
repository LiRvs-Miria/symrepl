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

# 会话错误码（代数：1xxx=协议层，2xxx=上游；无兜底，炸穿直接补专属码）
class InternalErrCode(IntEnum):
    OK = 0
    BAD_PROTOCOL = 1001
    PAYLOAD_TOO_LARGE = 1002
    EVENT_NOT_ALLOWED = 1003   # 事件在当前协议状态不允许（A1 决策：粗类码，细节走 detail）
    UPSTREAM_TIMEOUT = 2001
    UPSTREAM_REFUSED = 2002
#end class

class ProtoError(NamedTuple):
    code: InternalErrCode        # 无默认：类型系统逼每处显式选码（默认 OK = 静默成功地雷）
    message: str = ""
    detail: dict | None = None
#end class
