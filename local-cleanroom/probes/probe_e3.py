#!/usr/bin/env python3
"""probe_e3 — LLDB 会话原语对账网（2026-10-19）。

oracle=直跑：同一靶子同一 ktest，KTEST_FILE 直跑的退出码 vs E3 原语全程
（入口停→续跑→exited）的退出码，diff=0 为过。
拓扑对齐真实部署：一 Session 一进程（复用守门是契约）——子进程逐 ktest 跑。
"""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
TARGET = "/tmp/opencode/replay-build/get_sign"
KTESTS = sorted((ROOT / "third_party" / "klee" / "ktest").glob("get_sign_test*.ktest"))
HERE = Path(__file__).resolve().parent

fails = []

def check(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        fails.append(name)
    #end if
#end def


def direct_code(ktest: Path) -> int:
    """直跑基线：最小环境（仅 KTEST_FILE）直跑靶子的退出码。"""
    env = {"KTEST_FILE": str(ktest)}
    return subprocess.run([TARGET], env=env, capture_output=True).returncode
#end def


def e3_code(ktest: Path) -> int:
    """E3 全程：子进程单 Session（launch→入口停→续跑→exited），末行=退出码。"""
    r = subprocess.run([sys.executable, str(HERE / "probe_e3.py"), "one", str(ktest)],
                       capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        print(r.stdout)
        print(r.stderr, file=sys.stderr)
        raise RuntimeError(f"E3 child failed rc={r.returncode}")
    #end if
    return int(r.stdout.strip().splitlines()[-1])
#end def


def run_one(ktest_str: str) -> None:
    """子进程模式：单 Session 全程（单进程单 debugger 契约）。

    启动层职责在位：lldb-server 发现策略（显式 env 优先→lldb 同目录→llvm 打包目录），
    结果作为启动参数注入 Session——模块内零环境发现。
    """
    sys.path.insert(0, str(HERE.parent / "server"))
    import glob
    import shutil
    import tempfile
    import lldb_session as ls

    dirs = []
    which = shutil.which("lldb")
    if which:
        dirs.append(os.path.dirname(os.path.realpath(which)))
    #end if
    dirs.extend(sorted(glob.glob("/usr/lib/llvm-*/bin"), reverse=True))
    override = os.environ.get(ls.DEBUG_SERVER_ENV)
    if override:
        server = override
    else:
        found = ls.find_lldb_server(dirs)
        if not isinstance(found, ls.ServerFound):
            raise ls.SessionError(ls.SessionErrCode.NO_LLDB_SERVER, found.detail)
        #end if
        server = found.path
    #end if

    with tempfile.TemporaryDirectory() as tmp:
        s = ls.Session(TARGET, debugserver_path=server)
        s.launch(ktest_str, stdout_path=os.path.join(tmp, "out"),
                 stderr_path=os.path.join(tmp, "err"))
        s.continue_()
        state = s.wait_state()
        assert state == ls.KnownState.EXITED, state
        print(s.exit_code())
        s.close()
    #end with
#end def


if __name__ == "__main__":
    if len(sys.argv) > 2 and sys.argv[1] == "one":
        run_one(sys.argv[2])
        sys.exit(0)
    #end if

    assert len(KTESTS) == 3, f"expect 3 get_sign ktests, got {len(KTESTS)}"
    base = {k.name: direct_code(k) for k in KTESTS}
    check("直跑基线三码互异（0/1/255 形）", len(set(base.values())) == 3)
    for k in KTESTS:
        want, got = base[k.name], e3_code(k)
        check(f"E3 vs 直跑 diff=0：{k.name}（直跑 {want} / E3 {got}）", got == want)
    #end for
    print("=" * 30)
    print("ALL PASS" if not fails else f"{len(fails)} FAIL: {fails}")
    sys.exit(1 if fails else 0)
#end if
