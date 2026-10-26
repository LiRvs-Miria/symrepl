#!/usr/bin/env python3
"""probe_m4a — M4-a 差分回放矩阵真饵网（2026-10-26，验收=issue #19+补裁二读法）。

需求文本=ADR-0006 + design-notes replay.md「M4-a」节：
- get_sign：test000001 基线（a=00 00 00 00 → exit 0，hits {L11}）位翻转全列
  32 变体；two_objs：test000003 基线（arr=41ffffff/b=0000 → exit 0）48 变体
- 验收三件：①三出口全可达（exit 0/1/255，255 驱动位=a[3]bit7 位级定位）
  ②L13 input-dependent+驱动字节定位；L11 恒被求值盲区如实（未见依赖+
  覆盖不足臂）③two_objs mismatch 双对象翻转均可触发（exit 驱动位横跨两对象）
- server/ 零 diff：直驱 lldb_backend（一变体一子进程，单进程单 Session
  守门家法照 probe_e3）；互证 fork=pathmark.fork_sites() 直通

真饵网：真 LLDB 22+ 真靶（clang 现场重建，易失）；无 lldb/clang → SKIP rc0。
用法：python3 probe_m4a.py                  # 全网
      python3 probe_m4a.py once K T SCOPE   # 单变体子进程模式（JSON 单行）
"""
import json, os, shutil, subprocess, sys, tempfile, time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HERE = Path(__file__).resolve().parent
KT = ROOT / "third_party" / "klee" / "ktest"

sys.path.insert(0, str(ROOT / "src" / "replay"))
import diffmat
import ktest as ktest_mod
import pathmark

fails = []

def check(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        fails.append(name)
    #end if
#end def


def run_once_child(ktest_str: str, target: str, scope: str) -> None:
    """子进程模式：单 Session 全程（probe_e3 run_one 家法+poll_events 收报告）。"""
    sys.path.insert(0, str(HERE.parent / "src" / "server"))
    import lldb_backend as ls
    which = shutil.which("lldb")
    assert which is not None, "probe harness: lldb not found"
    lldb_dir = os.path.dirname(os.path.realpath(which))
    found = ls.find_lldb_server([lldb_dir])
    if not isinstance(found, ls.ServerFound):
        raise ls.SessionError(ls.SessionErrCode.NO_LLDB_SERVER, found.detail)
    #end if
    with tempfile.TemporaryDirectory() as tmp:
        s = ls.Session(target, debugserver_path=found.path, lldb_dir=lldb_dir)
        s.launch(ktest_str, scope=scope,
                 stdout_path=os.path.join(tmp, "out"),
                 stderr_path=os.path.join(tmp, "err"))
        s.report_entry_stop()       # scope 在册=入口停内自动插桩
        s.continue_()
        report, code = None, None
        deadline = time.time() + 120
        while time.time() < deadline and code is None:
            events = s.poll_events()
            stopped_seen = False
            for ev in events:
                if ev.get("event") == "stopped":
                    stopped_seen = True     # 插桩站点命中：inferior 停着，需再放行
                elif ev.get("event") == "exited":
                    code = ev["params"]["exit_code"]
                    report = ev["params"].get("trace_report") or {}
                #end if
            #end for
            if code is None and stopped_seen:
                s.continue_()               # 逐站放行（DAP 链客户端职责，直驱自担）
            elif code is None:
                time.sleep(0.05)
            #end if
        #end while
        if code is None:
            raise RuntimeError("no exited event in 60s")
        #end if
        hits = sorted({(n["function"], int(n["line"]))
                       for n in report.get("nodes", [])})
        s.close()
    #end with
    print(json.dumps({"exit_code": int(code), "hits": hits}))
#end def


def replay_once(ktest_path: Path, target: str, scope: str):
    """一变体一子进程；回 (exit_code, hit_set)。"""
    r = subprocess.run(
        [sys.executable, str(HERE / "probe_m4a.py"), "once",
         str(ktest_path), target, scope],
        capture_output=True, text=True, timeout=180)
    if r.returncode != 0:
        raise RuntimeError(f"once-child rc={r.returncode}: {r.stderr[-300:]}")
    #end if
    d = json.loads(r.stdout.strip().splitlines()[-1])
    return d["exit_code"], {(f, l) for f, l in d["hits"]}
#end def


def make_fixtures(work: Path):
    """clang 同配方现场做（probe_ir 家法）：两靶二进制+两 .ll。"""
    clang = shutil.which("clang")
    if clang is None:
        return None
    #end if
    inc = ROOT / "third_party" / "klee" / "runtime" / "include"
    work_bin = work / "bin"
    work_bin.mkdir(exist_ok=True)
    objects = []
    for src, cc, extra in ((ROOT / "third_party/klee/runtime/intrinsics.c", clang, []),
                           (ROOT / "third_party/klee/runtime/KTest.cpp", "clang++", ["-std=c++17"])):
        obj = work_bin / (src.stem + ".o")
        proc = subprocess.run([cc, "-O0", "-g", *extra, "-c", str(src), f"-I{inc}",
                               "-o", str(obj)], capture_output=True, text=True)
        if proc.returncode != 0:
            raise RuntimeError(f"{cc} failed: " + proc.stderr[:200])
        #end if
        objects.append(obj)
    #end for
    for name, src in (("get_sign", ROOT / "tests/fixtures/src/get_sign.c"),
                      ("two_objs", ROOT / "tests/fixtures/src/two_objs.c")):
        proc = subprocess.run(
            [clang, "-O0", "-g", "-S", "-emit-llvm", f"-I{inc}", str(src),
             "-o", str(work / f"{name}.ll")], capture_output=True, text=True)
        if proc.returncode != 0:
            raise RuntimeError("clang -emit-llvm failed: " + proc.stderr[:200])
        #end if
        proc = subprocess.run(
            [clang, "-O0", "-g", str(src), f"-I{inc}", *(str(o) for o in objects),
             "-o", str(work_bin / name)], capture_output=True, text=True)
        if proc.returncode != 0:
            raise RuntimeError("clang link failed: " + proc.stderr[:200])
        #end if
    #end for
    return work_bin / "get_sign", work_bin / "two_objs"
#end def


def collect_runs(baseline_bytes: bytes, plan, target: str, scope: str,
                 vdir: Path):
    """run0=baseline+逐变体观测（一变体一子进程）。"""
    runs = []
    base = vdir / "base.ktest"
    base.write_bytes(baseline_bytes)
    code, hits = replay_once(base, target, scope)
    runs.append(diffmat.RunObserved(frozenset(hits), code))
    for i, v in enumerate(plan.variants):
        p = vdir / f"v{i}.ktest"
        p.write_bytes(ktest_mod.dump(v))
        code, hits = replay_once(p, target, scope)
        runs.append(diffmat.RunObserved(frozenset(hits), code))
    #end for
    return runs
#end def


def fork_set_of(ll_path: Path):
    m = pathmark.parse_module(ll_path.read_text())
    assert not isinstance(m, pathmark.ParseBad), f"bad .ll: {ll_path}"
    return set(pathmark.fork_sites(m))
#end def


def no_orphans() -> bool:
    return all(not subprocess.run(["pgrep", "-x", n], capture_output=True,
                                  text=True).stdout.strip()
               for n in ("get_sign", "two_objs", "lldb-server"))
#end def


def scenario_get_sign(work: Path) -> None:
    """get_sign 全矩阵+验收（#19 三件之①②+补裁二盲区）。"""
    target = str(work / "bin" / "get_sign")
    baseline = ktest_mod.parse((KT / "get_sign_test000001.ktest").read_bytes())
    assert not isinstance(baseline, ktest_mod.KtestBad)
    plan = diffmat.variants(baseline, 10 ** 9)
    runs = collect_runs((KT / "get_sign_test000001.ktest").read_bytes(),
                        plan, target, "main,get_sign", work / "vgs")
    fork = fork_set_of(work / "get_sign.ll")
    analysis = diffmat.dependent(diffmat.matrix(runs))
    arms = diffmat.crosscheck(fork, analysis)
    report = diffmat.render(baseline="get_sign_test000001.ktest",
                            target="get_sign", plan=plan, runs=runs,
                            analysis=analysis, fork=fork)
    print(report, end="")
    ec = diffmat.exit_classes(runs)
    drive_255 = {plan.positions[i - 1] for i in ec[255]} if 255 in ec else set()
    L11, L13 = ("get_sign", 11), ("get_sign", 13)
    check("G1: baseline 真值复现（exit 0，hits={L11}——M3 验收锚）",
          runs[0] == diffmat.RunObserved(frozenset({L11}), 0))
    check("G2: 三出口全可达（0/1/255）；255 唯驱动位=(0,3,7)（INT_MIN 位级定位）",
          set(ec) == {0, 1, 255} and drive_255 == {(0, 3, 7)})
    check("G3: L13 input-dependent（走向集合>1）",
          analysis.verdicts[L13].input_dependent)
    check("G4: L11 盲区如实（恒被求值 ⇒ 未见依赖，补裁二）",
          not analysis.verdicts[L11].input_dependent)
    check("G5: 互证臂——L13=强断言；L11=覆盖不足（fork∧恒定）",
          arms[L13] == diffmat.Arm.STRONG
          and arms[L11] == diffmat.Arm.INSUFFICIENT_COVERAGE)
    check("G6: 报告渲染齐（判定/盲区/程序级行在场）",
          "input-dependent" in report and "覆盖不足" in report
          and "程序级" in report and "本观测内未见依赖" in report)
#end def


def scenario_two_objs(work: Path) -> None:
    """two_objs 全矩阵+验收（#19 三件之③：双对象翻转均可触发 mismatch）。"""
    target = str(work / "bin" / "two_objs")
    baseline = ktest_mod.parse((KT / "two_objs_test000003.ktest").read_bytes())
    assert not isinstance(baseline, ktest_mod.KtestBad)
    plan = diffmat.variants(baseline, 10 ** 9)
    runs = collect_runs((KT / "two_objs_test000003.ktest").read_bytes(),
                        plan, target, "main", work / "vto")
    fork = fork_set_of(work / "two_objs.ll")
    analysis = diffmat.dependent(diffmat.matrix(runs))
    arms = diffmat.crosscheck(fork, analysis)
    report = diffmat.render(baseline="two_objs_test000003.ktest",
                            target="two_objs", plan=plan, runs=runs,
                            analysis=analysis, fork=fork)
    print(report, end="")
    ec = diffmat.exit_classes(runs)
    drive_1 = {plan.positions[i - 1] for i in ec[1]} if 1 in ec else set()
    objs_triggered = {p[0] for p in drive_1}
    M11 = ("main", 11)
    check("T1: baseline 真值复现（exit 0，L11 命中在册）",
          runs[0].exit_code == 0 and M11 in runs[0].hit_set)
    check("T2: mismatch 双对象独立触发（exit 1 驱动位横跨 arr 与 b 两对象）",
          1 in ec and objs_triggered == {0, 1})
    check("T3: main L11 盲区如实（未见依赖）；互证=QUIET——&& 组合在 pathmark "
          "闭包外（噪声B），M4-a 镜像照出传播边界活证",
          not analysis.verdicts[M11].input_dependent
          and arms[M11] == diffmat.Arm.QUIET)
    check("T4: 程序级归因在场（exit 随翻转变化）",
          "exit 随翻转变化" in report)
#end def


def main() -> None:
    if shutil.which("lldb") is None:
        print("SKIP all (lldb not available)")
        print("=" * 30)
        print("ALL PASS" if not fails else f"{len(fails)} FAIL: {fails}")
        sys.exit(0)
    #end if
    with tempfile.TemporaryDirectory(prefix="probe-m4a-") as tmp:
        work = Path(tmp)
        built = make_fixtures(work)
        if built is None:
            print("SKIP all (clang not available)")
            print("=" * 30)
            print("ALL PASS" if not fails else f"{len(fails)} FAIL: {fails}")
            sys.exit(0)
        #end if
        (work / "vgs").mkdir()
        (work / "vto").mkdir()
        scenario_get_sign(work)
        scenario_two_objs(work)
    #end with
    check("收场无孤儿（靶/lldb-server 全清）", no_orphans())
    print("=" * 30)
    print("ALL PASS" if not fails else f"{len(fails)} FAIL: {fails}")
    sys.exit(1 if fails else 0)
#end if


if __name__ == "__main__":
    if len(sys.argv) == 5 and sys.argv[1] == "once":
        run_once_child(sys.argv[2], sys.argv[3], sys.argv[4])
        sys.exit(0)
    #end if
    main()
#end if
