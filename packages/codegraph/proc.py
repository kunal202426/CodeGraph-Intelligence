# CodeGraph -- Copyright (c) 2026 Kunal Mathur.
# Source-available under PolyForm Noncommercial 1.0.0. See LICENSE.
# https://github.com/kunal202426/CodeGraph-Intelligence
"""Process liveness + parent watchdog, shared by the MCP server and its workers.

Both long-lived child processes CodeGraph spawns need the same guarantee: exit
when whatever started you goes away. Neither can rely on stdio EOF to tell it
so -- that signal is not reliably delivered when a process tree is killed
abruptly rather than shut down cleanly, which is the observed behaviour on
Windows and the actual cause of the orphaned-process incidents this project
has had to diagnose by hand more than once.
"""

from __future__ import annotations

import os
import sys
import threading
import time

PARENT_CHECK_INTERVAL_SEC = 5.0


def process_alive(pid: int) -> bool:
    """True if a process with this PID is currently running.

    Platform-specific because the obvious cross-platform trick doesn't work:
    ``os.kill(pid, 0)`` is the standard POSIX liveness probe (raises
    ``ProcessLookupError`` without actually signaling anything), but on Windows
    ``os.kill`` with a non-special value calls ``TerminateProcess`` -- using it
    here would actually KILL the process instead of just checking it. Windows
    needs ``OpenProcess``/``GetExitCodeProcess``, which only query state.
    """
    if sys.platform == "win32":
        import ctypes
        import ctypes.wintypes

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        handle = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return False
        try:
            exit_code = ctypes.wintypes.DWORD()
            if not ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                return False
            return exit_code.value == STILL_ACTIVE
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists, just not ours to signal
    return True


def start_parent_watchdog(
    parent_pid: int, interval: float = PARENT_CHECK_INTERVAL_SEC
) -> threading.Thread:
    """Exit this process once *parent_pid* stops running.

    Runs on a daemon thread so it can never block serving or process exit.
    Uses ``os._exit`` deliberately: this is a "your reason to exist is gone"
    path, and running interpreter shutdown (flushing, atexit, joining threads)
    from a watchdog is exactly how a hung process stays hung.
    """

    def _loop() -> None:
        while True:
            time.sleep(interval)
            if not process_alive(parent_pid):
                os._exit(0)

    thread = threading.Thread(target=_loop, daemon=True, name="codegraph-parent-watchdog")
    thread.start()
    return thread
