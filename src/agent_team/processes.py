from __future__ import annotations

import ctypes
import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass

from .errors import AgentTeamError


MAX_PROCESS_ID = (1 << 31) - 1


@dataclass(frozen=True, slots=True)
class ProcessIdentity:
    pid: int
    start_id: str
    pgid: int | None = None


class _DarwinProcBsdInfo(ctypes.Structure):
    _fields_ = [
        ("pbi_flags", ctypes.c_uint32),
        ("pbi_status", ctypes.c_uint32),
        ("pbi_xstatus", ctypes.c_uint32),
        ("pbi_pid", ctypes.c_uint32),
        ("pbi_ppid", ctypes.c_uint32),
        ("pbi_uid", ctypes.c_uint32),
        ("pbi_gid", ctypes.c_uint32),
        ("pbi_ruid", ctypes.c_uint32),
        ("pbi_rgid", ctypes.c_uint32),
        ("pbi_svuid", ctypes.c_uint32),
        ("pbi_svgid", ctypes.c_uint32),
        ("rfu_1", ctypes.c_uint32),
        ("pbi_comm", ctypes.c_char * 16),
        ("pbi_name", ctypes.c_char * 32),
        ("pbi_nfiles", ctypes.c_uint32),
        ("pbi_pgid", ctypes.c_uint32),
        ("pbi_pjobc", ctypes.c_uint32),
        ("e_tdev", ctypes.c_uint32),
        ("e_tpgid", ctypes.c_uint32),
        ("pbi_nice", ctypes.c_int32),
        ("pbi_start_tvsec", ctypes.c_uint64),
        ("pbi_start_tvusec", ctypes.c_uint64),
    ]


def _linux_proc_start_time(raw: bytes) -> str | None:
    # /proc/<pid>/stat field 2 is a parenthesized process name and may contain
    # spaces or closing parentheses, so a plain split can shift field 22.
    end_of_name = raw.rfind(b") ")
    if end_of_name < 0:
        return None
    fields_after_name = raw[end_of_name + 2 :].split()
    start_time_index = 22 - 3
    if len(fields_after_name) <= start_time_index:
        return None
    try:
        return fields_after_name[start_time_index].decode("ascii", errors="strict")
    except UnicodeDecodeError:
        return None


def _darwin_proc_start_id(pid: int) -> str | None:
    try:
        libproc = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
    except OSError:
        return None
    proc_pidinfo = libproc.proc_pidinfo
    proc_pidinfo.argtypes = [
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_uint64,
        ctypes.c_void_p,
        ctypes.c_int,
    ]
    proc_pidinfo.restype = ctypes.c_int
    info = _DarwinProcBsdInfo()
    size = ctypes.sizeof(info)
    copied = proc_pidinfo(pid, 3, 0, ctypes.byref(info), size)
    if copied != size or info.pbi_pid != pid or info.pbi_start_tvsec <= 0:
        return None
    return (
        f"darwin-v2:{info.pbi_start_tvsec}:"
        f"{info.pbi_start_tvusec:06d}"
    )


def _darwin_legacy_start_id(pid: int) -> str | None:
    environment = os.environ.copy()
    environment["LC_ALL"] = "C"
    result = subprocess.run(
        ["ps", "-p", str(pid), "-o", "lstart="],
        capture_output=True,
        text=True,
        check=False,
        env=environment,
    )
    value = result.stdout.strip()
    return f"darwin:{value}" if result.returncode == 0 and value else None


def process_start_id(pid: int) -> str | None:
    if (
        isinstance(pid, bool)
        or not isinstance(pid, int)
        or pid <= 0
        or pid > MAX_PROCESS_ID
    ):
        return None
    if sys.platform.startswith("linux"):
        try:
            with open(f"/proc/{pid}/stat", "rb") as stat_file:
                raw = stat_file.read()
        except (FileNotFoundError, ProcessLookupError, PermissionError):
            return None
        start_time = _linux_proc_start_time(raw)
        if start_time is None:
            return None
        return f"linux:{start_time}"
    if sys.platform == "darwin":
        return _darwin_proc_start_id(pid)
    return None


def current_identity(*, include_pgid: bool = False) -> ProcessIdentity:
    pid = os.getpid()
    start_id = process_start_id(pid)
    if start_id is None:
        raise AgentTeamError(
            "PROCESS_START_ID_UNAVAILABLE",
            f"cannot query stable process start id for PID {pid}",
        )
    return ProcessIdentity(
        pid,
        start_id,
        os.getpgid(pid) if include_pgid else None,
    )


def identity_matches(
    pid: int | None,
    start_id: str | None,
    *,
    pgid: int | None = None,
) -> bool:
    return process_identity_state(pid, start_id, pgid=pgid) == "match"


def process_identity_state(
    pid: int | None,
    start_id: str | None,
    *,
    pgid: int | None = None,
) -> str:
    """Return match, gone, reused, mismatch, unknown, or invalid."""
    if (
        isinstance(pid, bool)
        or not isinstance(pid, int)
        or pid <= 0
        or pid > MAX_PROCESS_ID
        or not isinstance(start_id, str)
        or not start_id
        or (
            pgid is not None
            and (
                isinstance(pgid, bool)
                or not isinstance(pgid, int)
                or pgid <= 0
                or pgid > MAX_PROCESS_ID
            )
        )
    ):
        return "invalid"
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return "gone"
    except PermissionError:
        pass
    current = (
        _darwin_legacy_start_id(pid)
        if sys.platform == "darwin" and start_id.startswith("darwin:")
        else process_start_id(pid)
    )
    if current is None:
        return "unknown"
    if current != start_id:
        return "reused"
    if pgid is not None:
        try:
            return "match" if os.getpgid(pid) == pgid else "mismatch"
        except ProcessLookupError:
            return "gone"
        except PermissionError:
            return "unknown"
    return "match"


def pid_exists(pid: int) -> bool:
    if (
        isinstance(pid, bool)
        or not isinstance(pid, int)
        or pid <= 0
        or pid > MAX_PROCESS_ID
    ):
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def process_group_exists(pgid: int) -> bool:
    if (
        isinstance(pgid, bool)
        or not isinstance(pgid, int)
        or pgid <= 0
        or pgid > MAX_PROCESS_ID
    ):
        # This predicate guards destructive actions and Owner release. Invalid
        # identity evidence must fail closed rather than look quiescent.
        return True
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def signal_process(
    pid: int,
    start_id: str,
    sig: signal.Signals,
) -> bool:
    if not identity_matches(pid, start_id):
        return False
    try:
        os.kill(pid, sig)
    except ProcessLookupError:
        return False
    return True


def wait_process_stopped(pid: int, start_id: str, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process_identity_state(pid, start_id) in {"gone", "reused"}:
            return True
        time.sleep(0.05)
    return process_identity_state(pid, start_id) in {"gone", "reused"}


def terminate_verified_process(
    *,
    pid: int,
    start_id: str,
    term_timeout: float = 2.0,
    kill_timeout: float = 2.0,
) -> bool:
    state = process_identity_state(pid, start_id)
    if state in {"gone", "reused"}:
        return True
    if state != "match":
        return False
    signal_process(pid, start_id, signal.SIGTERM)
    if wait_process_stopped(pid, start_id, term_timeout):
        return True
    signal_process(pid, start_id, signal.SIGKILL)
    return wait_process_stopped(pid, start_id, kill_timeout)


def signal_group(
    *,
    runner_pid: int,
    runner_pgid: int,
    runner_start_id: str,
    sig: signal.Signals,
) -> bool:
    if not identity_matches(runner_pid, runner_start_id, pgid=runner_pgid):
        return False
    try:
        os.killpg(runner_pgid, sig)
    except ProcessLookupError:
        return False
    return True


def wait_group_quiescent(pgid: int, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not process_group_exists(pgid):
            return True
        time.sleep(0.05)
    return not process_group_exists(pgid)


def terminate_verified_group(
    *,
    runner_pid: int,
    runner_pgid: int,
    runner_start_id: str,
    term_timeout: float = 2.0,
    kill_timeout: float = 2.0,
) -> bool:
    if not process_group_exists(runner_pgid):
        return True
    if not identity_matches(runner_pid, runner_start_id, pgid=runner_pgid):
        return False
    signal_group(
        runner_pid=runner_pid,
        runner_pgid=runner_pgid,
        runner_start_id=runner_start_id,
        sig=signal.SIGTERM,
    )
    if wait_group_quiescent(runner_pgid, term_timeout):
        return True
    signal_group(
        runner_pid=runner_pid,
        runner_pgid=runner_pgid,
        runner_start_id=runner_start_id,
        sig=signal.SIGKILL,
    )
    return wait_group_quiescent(runner_pgid, kill_timeout)
