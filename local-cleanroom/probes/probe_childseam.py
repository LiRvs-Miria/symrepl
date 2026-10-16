#!/usr/bin/env python3
"""probe_childseam — 砖1 child 缝验收（RED 起步，2026-10-16）。

需求文本 = design-notes「砖1 契约」（用户先验+收敛裁决）：
- ping=启动握手专用；心跳撤除（权衡妥协非证伪，门留案可复议）
- 退场门统一：EOF（死）∪ 请求超时（挂死）→ 同一门，异常可辨（ChildGoneError vs TimeoutError）
- decision 8：child 已死请求立即失败不烧 timeout
- close=优雅窗 2s+强杀，幂等；单写者；Future 表无泄漏

跑法：python3 probe_childseam.py
child=本地桩 ../server/service.py（2026-10-16 桩案：形状忠实真协议，真逻辑后续填），
解释器=sys.executable——全封闭，无 venv/lldb 依赖，无 SKIP。
"""
import asyncio
import os
import sys
import tempfile
import time
from pathlib import Path

SRV_DIR = str(Path(__file__).resolve().parent.parent / "server")
sys.path.insert(0, SRV_DIR)

CHILD = [sys.executable, str(Path(SRV_DIR) / "session.py")]

results = []


def record(name, status, detail=""):
    results.append((name, status))
    print(f"[{status}] {name}" + (f" — {detail}" if detail else ""))


async def t0_childproto_algebra():
    """T0 代数：request_msg 形状 + parse_child_msg 三分流（response/event/未知）。"""
    import childproto
    assert childproto.VERSION == 1, "VERSION 应为 1"
    req = childproto.ChildRequest(id=7, cmd="ping", params={})
    m = childproto.request_msg(req)
    assert m == {"type": "request", "id": 7, "cmd": "ping", "params": {}}, f"request_msg 形状偏: {m}"
    # ok 响应
    r = childproto.parse_child_msg(
        {"type": "response", "id": 7, "ok": True, "result": {"pong": True}})
    assert isinstance(r, childproto.ChildResponse) and r.ok and r.id == 7
    assert r.result == {"pong": True}
    # error 响应
    e = childproto.parse_child_msg(
        {"type": "response", "id": 8, "ok": False, "error": {"code": "x", "message": "y"}})
    assert isinstance(e, childproto.ChildResponse) and not e.ok and e.error.get("code") == "x"
    # 事件
    v = childproto.parse_child_msg({"type": "event", "event": "stopped", "params": {"a": 1}})
    assert isinstance(v, childproto.ChildEvent) and v.event == "stopped" and v.params == {"a": 1}
    # 未知 type → Bad（错误前置，不抛）
    b = childproto.parse_child_msg({"type": "bogus"})
    assert isinstance(b, childproto.BadChildMsg), f"未知 type 应得 BadChildMsg，得 {b!r}"


async def t1_handshake_clean_exit():
    """T1 握手+干净退场：spawn 真服务 → ping→pong → close → child exit 0。"""
    import schedule
    with tempfile.NamedTemporaryFile(suffix=".log", delete=False) as f:
        logpath = f.name
    child = await schedule.spawn_child(CHILD + ["--log", logpath])
    pong = await schedule.child_ping(child, timeout=10.0)
    assert pong == {"pong": True, "service_version": 1}, f"pong 形状偏: {pong}"
    await schedule.child_close(child)
    assert child.returncode == 0, f"stdin-EOF 自杀路应 exit 0，得 {child.returncode}"


async def t2_spawn_fails_fast():
    """T2 spawn 快败：interp 不存在 → 立即抛，不烧等待。"""
    import schedule
    spawn = schedule.spawn_child   # 函数不存在=AttributeError（RED），不许蒙混快败断言
    t0 = time.monotonic()
    try:
        await spawn(["/nonexistent/interp/xyz"])
    except Exception:
        elapsed = time.monotonic() - t0
        assert elapsed < 5.0, f"spawn 失败应立即，耗了 {elapsed:.1f}s"
        return
    raise AssertionError("不存在的 interp 未抛异常")


async def t3_hang_timeout():
    """T3 挂死路：哑 child 不应答 → 超时判死（TimeoutError），close 强杀不悬挂。"""
    import schedule
    child = await schedule.spawn_child(
        [sys.executable, "-c", "import time; time.sleep(30)"])
    t0 = time.monotonic()
    try:
        await schedule.child_ping(child, timeout=1.0)
    except TimeoutError:
        elapsed = time.monotonic() - t0
        assert elapsed < 3.0, f"超时应 ≈1s 触发，耗了 {elapsed:.1f}s"
    else:
        raise AssertionError("哑 child 的 ping 未超时")
    t1 = time.monotonic()
    await schedule.child_close(child)
    assert time.monotonic() - t1 < 10.0, "close 强杀悬挂"
    assert child.returncode is not None, "强杀后 child 未退"


async def t4_eof_fails_fast():
    """T4 EOF 路：在途请求时 child 被杀 → ChildGoneError 快败（decision 8，不烧 timeout）。

    用哑 child（不答话）保请求真正在途——会应答的桩答得太快，kill 前请求已完结。
    """
    import schedule
    child = await schedule.spawn_child(
        [sys.executable, "-c", "import time; time.sleep(30)"])
    task = asyncio.ensure_future(
        schedule.child_request(child, "ping", {}, timeout=30.0))
    await asyncio.sleep(0.3)  # 请求先上线（帧入管道，future 已登记）
    child.kill()
    t0 = time.monotonic()
    try:
        await task
    except schedule.ChildGoneError:
        elapsed = time.monotonic() - t0
        assert elapsed < 3.0, f"EOF 快败应即时，耗了 {elapsed:.1f}s"
    except TimeoutError:
        raise AssertionError("child 已死却走了超时路——decision 8 被烧穿")
    else:
        raise AssertionError("child 已死请求竟成功")
    await schedule.child_close(child)  # 幂等收尾


async def t5_generic_request():
    """T5 泛化请求：child_request 走 correlation 全链（ping 只是载荷）。"""
    import schedule
    child = await schedule.spawn_child(CHILD + ["--log", os.devnull])
    result = await schedule.child_request(child, "ping", {}, timeout=10.0)
    assert result == {"pong": True, "service_version": 1}, f"result 偏: {result}"
    await schedule.child_close(child)


async def t6_close_idempotent():
    """T6 close 幂等：重复关、关已死 child，均不炸。"""
    import schedule
    child = await schedule.spawn_child(CHILD + ["--log", os.devnull])
    await schedule.child_close(child)
    await schedule.child_close(child)  # 重复关
    stub = await schedule.spawn_child(
        [sys.executable, "-c", "import time; time.sleep(30)"])
    stub.kill()
    await asyncio.wait_for(stub.wait(), timeout=5)
    await schedule.child_close(stub)  # 关已死者


async def t7_request_after_death():
    """T7 发前已死：child 死透后发请求 → ChildGoneError 立即，不烧 timeout（decision 8 前半）。"""
    import schedule
    child = await schedule.spawn_child(CHILD + ["--log", os.devnull])
    await schedule.child_close(child)
    t0 = time.monotonic()
    try:
        await schedule.child_request(child, "ping", {}, timeout=10.0)
    except schedule.ChildGoneError:
        elapsed = time.monotonic() - t0
        assert elapsed < 3.0, f"快败应即时，耗了 {elapsed:.1f}s"
    except TimeoutError:
        raise AssertionError("child 已死却走了超时路——decision 8 被烧穿")
    else:
        raise AssertionError("child 已死请求竟成功")
    #end try


CASES = [
    ("T0 childproto 代数", t0_childproto_algebra),
    ("T1 握手+干净退场", t1_handshake_clean_exit),
    ("T2 spawn 快败", t2_spawn_fails_fast),
    ("T3 挂死超时判死", t3_hang_timeout),
    ("T4 EOF 快败(decision 8)", t4_eof_fails_fast),
    ("T5 泛化请求 correlation", t5_generic_request),
    ("T6 close 幂等", t6_close_idempotent),
    ("T7 发前已死快败", t7_request_after_death),
]


async def main():
    for name, fn in CASES:
        try:
            out = await asyncio.wait_for(fn(), timeout=60.0)
            if isinstance(out, tuple) and out and out[0] == "SKIP":
                record(name, "SKIP", out[1])
            else:
                record(name, "PASS")
        except Exception as exc:
            record(name, "RED", f"{type(exc).__name__}: {exc}")
    red = sum(1 for _, s in results if s == "RED")
    skip = sum(1 for _, s in results if s == "SKIP")
    passed = sum(1 for _, s in results if s == "PASS")
    print(f"\n== {passed} PASS / {red} RED / {skip} SKIP ==")
    return 1 if red else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
