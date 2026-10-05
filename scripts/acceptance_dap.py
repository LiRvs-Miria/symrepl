#!/usr/bin/env python3
"""DAP acceptance scenario (M2-e) — the full loop over a real server.

Compiles the get_sign fixture against the vendored kleeRuntest runtime
(the M1 recipe), starts the real DAP server (`symrepl dap --port`), and
drives the full lifecycle over a TCP socket: initialize → launch →
configurationDone → breakpoint hit → scopes/variables → continue →
exited → trace report (output + replay.branchTrace) → disconnect.

Runnable end-to-end:  python3 scripts/acceptance_dap.py
Exit codes: 0 pass, 1 fail, 2 environment unavailable.
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RUNTIME = ROOT / "third_party" / "klee" / "runtime"
KTEST = ROOT / "third_party" / "klee" / "ktest" / "get_sign_test000002.ktest"
PORT = 47777

_failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(("PASS " if ok else "FAIL ") + name + (f" — {detail}" if detail else ""),
          flush=True)
    if not ok:
        _failures.append(name)


class Client:
    """Minimal hand-written DAP client (acceptance-grade)."""

    def __init__(self, sock: socket.socket) -> None:
        sock.settimeout(20.0)
        self._sock = sock
        self._reader = FrameReader(sock.makefile("rb").read1)
        self._seq = 0
        self.events: list[dict] = []

    def send(self, command: str, arguments: dict | None = None) -> None:
        self._seq += 1
        self._sock.sendall(
            encode_frame({"seq": self._seq, "type": "request",
                          "command": command,
                          "arguments": arguments or {}})
        )

    def request(self, command: str, arguments: dict | None = None) -> dict:
        self.send(command, arguments)
        while True:
            message = self._reader.read_frame()
            if message.get("type") == "response":
                return message
            self.events.append(message)

    def wait_event(self, name: str) -> dict:
        while True:
            for index, message in enumerate(self.events):
                if (message.get("type") == "event"
                        and message.get("event") == name):
                    return self.events.pop(index)
            message = self._reader.read_frame()
            if message.get("type") == "event":
                self.events.append(message)

    def close(self) -> None:
        try:
            self._sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self._sock.close()


def build_fixture(work: Path) -> Path | None:
    clang = shutil.which("clang")
    clangxx = shutil.which("clang++")
    if clang is None or clangxx is None:
        return None
    inc = RUNTIME / "include"
    objects = []
    for source, compiler, extra in (
        (RUNTIME / "intrinsics.c", clang, []),
        (RUNTIME / "KTest.cpp", clangxx, ["-std=c++17"]),
    ):
        obj = work / (source.stem + ".o")
        subprocess.run(
            [compiler, "-O0", "-g", *extra, "-c", str(source),
             f"-I{inc}", "-o", str(obj)],
            check=True,
        )
        objects.append(obj)
    binary = work / "get_sign"
    subprocess.run(
        [clang, "-O0", "-g", str(ROOT / "tests" / "fixtures" / "src" /
                                 "get_sign.c"),
         f"-I{inc}", *(str(o) for o in objects), "-o", str(binary)],
        check=True,
    )
    return binary


def main() -> int:
    if shutil.which("lldb") is None or shutil.which("clang") is None:
        print("environment unavailable: lldb/clang not found")
        return 2

    with tempfile.TemporaryDirectory(prefix="symrepl-dap-acc-") as tmp:
        work = Path(tmp)
        target = build_fixture(work)
        if target is None:
            print("environment unavailable: fixture build failed")
            return 2
        check("fixture built", target.exists())

        server = subprocess.Popen(
            [sys.executable, "-m", "symrepl.cli", "dap", "--port", str(PORT)],
            cwd=str(ROOT),
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            sock = None
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline and sock is None:
                try:
                    sock = socket.create_connection(
                        ("127.0.0.1", PORT), timeout=1)
                except OSError:
                    time.sleep(0.1)
            check("server reachable via --port", sock is not None)
            if sock is None:
                return 1

            client = Client(sock)
            reply = client.request("initialize", {"adapterID": "symrepl"})
            check("initialize capabilities",
                  reply["success"] is True
                  and reply["body"]["supportsConfigurationDoneRequest"]
                  is True)

            client.send("launch", {
                "ktest": str(KTEST),
                "target": str(target),
                "stopOnBranch": True,
                "scope": "get_sign",
            })  # response withheld until configurationDone
            initialized = client.wait_event("initialized")
            check("initialized event fired", initialized["body"] == {})

            client.send("configurationDone")
            first = client.wait_event("stopped")
            check("stopOnBranch hit (site 1)",
                  first["body"]["reason"] == "breakpoint"
                  and "get_sign" in first["body"]["description"])

            frame_id = None
            reply = client.request("threads")
            if reply["success"] and reply["body"]["threads"]:
                thread_id = reply["body"]["threads"][0]["id"]
                reply = client.request("stackTrace", {"threadId": thread_id})
                frames = reply["body"]["stackFrames"]
                check("stack readable at the site", bool(frames))
                frame_id = frames[0]["id"]
            if frame_id is not None:
                reply = client.request("scopes", {"frameId": frame_id})
                ref = reply["body"]["scopes"][0]["variablesReference"]
                reply = client.request("variables",
                                       {"variablesReference": ref})
                names = {v["name"] for v in reply["body"]["variables"]}
                check("symbolic variable visible", "x" in names,
                      str(sorted(names)))

            client.send("continue")
            second = client.wait_event("stopped")
            check("stopOnBranch hit (site 2)",
                  second["body"]["reason"] == "breakpoint")
            client.send("continue")

            exited = client.wait_event("exited")
            check("exited with the recorded code",
                  exited["body"]["exitCode"] == 1)
            output = client.wait_event("output")
            check("trace report pushed via output",
                  "branch trace: scope=get_sign" in output["body"]["output"])

            reply = client.request("replay.branchTrace")
            check("replay.branchTrace available",
                  reply["success"] is True
                  and "get_sign" in (reply["body"]["trace"] or ""))

            client.send("disconnect")
            client.close()
            rc = server.wait(timeout=20)
            check("server clean exit after disconnect", rc == 0, f"rc={rc}")
        finally:
            if server.poll() is None:
                server.kill()

    print(f"\nDAP ACCEPTANCE: {'PASS' if not _failures else 'FAIL'} "
          f"({len(_failures)} failure(s))", flush=True)
    return 1 if _failures else 0


sys.path.insert(0, str(ROOT / "src"))
from symrepl.dap_framing import FrameReader, encode_frame  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
