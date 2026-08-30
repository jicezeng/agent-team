from __future__ import annotations

import asyncio
import base64
import contextlib
import datetime as dt
import errno
import fcntl
import json
import os
import pty
import signal
import stat
import struct
import sys
import termios
import tty
from pathlib import Path
from typing import Any

from .adapters import get_adapter
from .adapters.base import (
    AdapterEvidence,
    AdapterEvidenceSnapshot,
    LaunchSpec,
    ProcessResult,
    StreamRecord,
)
from .errors import IntegrityError
from .journal import scan_journal
from .processes import (
    current_identity,
    identity_matches,
    process_group_exists,
    process_start_id,
)
from .state import locked_run, read_owner
from .turns import (
    commit_technical_block_locked,
    load_outbox,
    load_runtime,
    mark_session_unavailable,
)
from .util import (
    atomic_json,
    create_empty_regular,
    parse_json_object,
    parse_rfc3339,
    path_entry_exists,
    read_json,
    read_regular,
    require_keys,
    require_schema_version,
    rfc3339,
    set_private_umask,
    sha256_bytes,
    write_all,
)

SUPERVISOR_REQUIRED = {
    "schema_version",
    "turn_id",
    "launch_nonce",
    "state",
    "supervisor_pid",
    "supervisor_start_id",
    "runner_pid",
    "runner_pgid",
    "runner_start_id",
    "agent_execution_started",
    "adapter_completed",
    "permission_required",
    "observed_session_ref",
    "process_exit_code",
    "termination_kind",
    "group_quiescent",
    "updated_at",
}
SUPERVISOR_STATE_ORDER = {
    "starting": 0,
    "waiting_authorization": 1,
    "running": 2,
    "stopping": 3,
    "finished": 4,
}
RUNNER_REQUIRED = {
    "schema_version",
    "turn_id",
    "launch_nonce",
    "runner_pid",
    "runner_pgid",
    "runner_start_id",
    "created_at",
}
AUTH_REQUIRED = {
    "schema_version",
    "turn_id",
    "launch_nonce",
    "supervisor_pid",
    "supervisor_start_id",
    "runner_pid",
    "runner_pgid",
    "runner_start_id",
    "launch_profile",
    "launch_profile_sha256",
    "authorized_at",
}
EXEC_ERROR_REQUIRED = {"schema_version", "code", "message"}
EXEC_ERROR_CODES = {
    "AUTHORIZATION_INVALID",
    "AUTHORIZATION_TIMEOUT",
    "EXEC_FAILED",
    "RUNNER_BOOTSTRAP_FAILED",
    "RUNNER_NOT_GROUP_LEADER",
}


def validate_exec_error(value: dict[str, Any]) -> dict[str, Any]:
    require_keys(value, required=EXEC_ERROR_REQUIRED, subject="Runner status")
    require_schema_version(value, 1, subject="Runner status")
    if (
        not isinstance(value["code"], str)
        or value["code"] not in EXEC_ERROR_CODES
        or not isinstance(value["message"], str)
        or not value["message"]
    ):
        raise IntegrityError("Runner status fields are invalid")
    return value


def _parse_exec_error_bytes(raw: bytes) -> dict[str, Any]:
    return validate_exec_error(parse_json_object(raw, subject="Runner status JSON"))


def validate_runner(
    value: dict[str, Any], *, turn_id: str, nonce: str
) -> dict[str, Any]:
    require_keys(value, required=RUNNER_REQUIRED, subject="runner identity")
    require_schema_version(value, 1, subject="runner identity")
    if (
        value["turn_id"] != turn_id
        or value["launch_nonce"] != nonce
    ):
        raise IntegrityError("runner identity context mismatch")
    pid = value["runner_pid"]
    pgid = value["runner_pgid"]
    if (
        isinstance(pid, bool)
        or not isinstance(pid, int)
        or pid <= 0
        or isinstance(pgid, bool)
        or not isinstance(pgid, int)
        or pgid != pid
    ):
        raise IntegrityError("runner must lead its own process group")
    if not isinstance(value["runner_start_id"], str) or not value["runner_start_id"]:
        raise IntegrityError("runner start id is invalid")
    parse_rfc3339(value["created_at"])
    return value


def validate_authorization(
    value: dict[str, Any],
    *,
    turn_id: str,
    nonce: str,
) -> dict[str, Any]:
    require_keys(value, required=AUTH_REQUIRED, subject="launch authorization")
    require_schema_version(value, 1, subject="launch authorization")
    if (
        value["turn_id"] != turn_id
        or value["launch_nonce"] != nonce
    ):
        raise IntegrityError("launch authorization context mismatch")
    for field in {"supervisor_pid", "runner_pid", "runner_pgid"}:
        if (
            isinstance(value[field], bool)
            or not isinstance(value[field], int)
            or value[field] <= 0
        ):
            raise IntegrityError(f"launch authorization {field} is invalid")
    for field in {
        "supervisor_start_id",
        "runner_start_id",
        "launch_profile",
        "launch_profile_sha256",
        "authorized_at",
    }:
        if not isinstance(value[field], str) or not value[field]:
            raise IntegrityError(f"launch authorization {field} is invalid")
    if value["runner_pgid"] != value["runner_pid"]:
        raise IntegrityError("authorized Runner must lead its process group")
    if len(value["launch_profile_sha256"]) != 64 or any(
        char not in "0123456789abcdef" for char in value["launch_profile_sha256"]
    ):
        raise IntegrityError("launch authorization profile hash is invalid")
    parse_rfc3339(value["authorized_at"])
    return value


def validate_supervisor(value: dict[str, Any]) -> dict[str, Any]:
    require_keys(value, required=SUPERVISOR_REQUIRED, subject="supervisor snapshot")
    require_schema_version(value, 1, subject="supervisor snapshot")
    state = value["state"]
    if not isinstance(state, str) or state not in SUPERVISOR_STATE_ORDER:
        raise IntegrityError("invalid supervisor state")
    if (
        isinstance(value["supervisor_pid"], bool)
        or not isinstance(value["supervisor_pid"], int)
        or value["supervisor_pid"] <= 0
        or not isinstance(value["supervisor_start_id"], str)
        or not value["supervisor_start_id"]
    ):
        raise IntegrityError("invalid supervisor identity")
    if not isinstance(value["launch_nonce"], str) or not value["launch_nonce"]:
        raise IntegrityError("invalid supervisor launch nonce")
    if not isinstance(value["turn_id"], str) or not value["turn_id"].startswith(
        "turn-"
    ):
        raise IntegrityError("invalid supervisor turn id")
    runner_fields = (
        value["runner_pid"],
        value["runner_pgid"],
        value["runner_start_id"],
    )
    all_empty = all(item is None for item in runner_fields)
    all_full = (
        isinstance(runner_fields[0], int)
        and not isinstance(runner_fields[0], bool)
        and runner_fields[0] > 0
        and isinstance(runner_fields[1], int)
        and not isinstance(runner_fields[1], bool)
        and runner_fields[1] == runner_fields[0]
        and isinstance(runner_fields[2], str)
        and bool(runner_fields[2])
    )
    if not (all_empty or all_full):
        raise IntegrityError("partial runner identity in supervisor snapshot")
    if state == "starting":
        if not all_empty or value["agent_execution_started"]:
            raise IntegrityError("invalid starting supervisor snapshot")
    elif state in {"waiting_authorization", "running", "stopping"} and not all_full:
        raise IntegrityError(f"{state} supervisor requires runner identity")
    if state == "waiting_authorization" and value["agent_execution_started"]:
        raise IntegrityError("waiting supervisor cannot have execution evidence")
    if state == "running" and not value["agent_execution_started"]:
        raise IntegrityError("running supervisor requires execution evidence")
    if state == "finished":
        if not value["group_quiescent"]:
            raise IntegrityError("finished supervisor must be group-quiescent")
        if all_empty and (
            value["agent_execution_started"]
            or value["adapter_completed"]
            or value["permission_required"]
            or value["observed_session_ref"] is not None
        ):
            raise IntegrityError(
                "finished supervisor without a Runner has execution evidence"
            )
    elif value["group_quiescent"]:
        raise IntegrityError("non-finished supervisor cannot be group-quiescent")
    if value["adapter_completed"] and not value["agent_execution_started"]:
        raise IntegrityError("adapter completion without execution start")
    if value["adapter_completed"] and value["permission_required"]:
        raise IntegrityError("adapter cannot complete while permission is required")
    for field in {
        "agent_execution_started",
        "adapter_completed",
        "permission_required",
        "group_quiescent",
    }:
        if not isinstance(value[field], bool):
            raise IntegrityError(f"supervisor {field} must be boolean")
    if value["observed_session_ref"] is not None and (
        not isinstance(value["observed_session_ref"], str)
        or not value["observed_session_ref"]
    ):
        raise IntegrityError("invalid supervisor session ref")
    if value["process_exit_code"] is not None and (
        isinstance(value["process_exit_code"], bool)
        or not isinstance(value["process_exit_code"], int)
    ):
        raise IntegrityError("invalid supervisor exit code")
    termination_kind = value["termination_kind"]
    if termination_kind is not None and (
        not isinstance(termination_kind, str)
        or termination_kind
        not in {
            "normal",
            "cancelled",
            "deadline",
            "signal",
            "crash",
            "action",
            "output_limit",
            "unknown",
        }
    ):
        raise IntegrityError("invalid supervisor termination kind")
    if state in {"starting", "waiting_authorization", "running"} and (
        value["process_exit_code"] is not None or value["termination_kind"] is not None
    ):
        raise IntegrityError("active supervisor cannot have a process result")
    if state == "finished" and (
        value["process_exit_code"] is None or value["termination_kind"] is None
    ):
        raise IntegrityError("finished supervisor requires a process result")
    parse_rfc3339(value["updated_at"])
    return value


def _base_snapshot(turn_id: str, nonce: str) -> dict[str, Any]:
    identity = current_identity()
    return {
        "schema_version": 1,
        "turn_id": turn_id,
        "launch_nonce": nonce,
        "state": "starting",
        "supervisor_pid": identity.pid,
        "supervisor_start_id": identity.start_id,
        "runner_pid": None,
        "runner_pgid": None,
        "runner_start_id": None,
        "agent_execution_started": False,
        "adapter_completed": False,
        "permission_required": False,
        "observed_session_ref": None,
        "process_exit_code": None,
        "termination_kind": None,
        "group_quiescent": False,
        "updated_at": rfc3339(),
    }


def _save_snapshot(
    run_dir: Path,
    turn_id: str,
    snapshot: dict[str, Any],
    *,
    initial: bool = False,
) -> None:
    snapshot["updated_at"] = rfc3339()
    validate_supervisor(snapshot)
    path = run_dir / "turns" / turn_id / "process" / "supervisor.json"
    if path_entry_exists(path) and not initial:
        previous = validate_supervisor(read_json(path))
        for field in {
            "schema_version",
            "turn_id",
            "launch_nonce",
            "supervisor_pid",
            "supervisor_start_id",
        }:
            if snapshot[field] != previous[field]:
                raise IntegrityError(f"supervisor field is immutable: {field}")
        if (
            SUPERVISOR_STATE_ORDER[snapshot["state"]]
            < SUPERVISOR_STATE_ORDER[previous["state"]]
        ):
            raise IntegrityError("supervisor state cannot move backward")
        for field in {"runner_pid", "runner_pgid", "runner_start_id"}:
            if previous[field] is not None and snapshot[field] != previous[field]:
                raise IntegrityError(f"supervisor Runner field changed: {field}")
        for field in {
            "agent_execution_started",
            "adapter_completed",
            "permission_required",
        }:
            if previous[field] and not snapshot[field]:
                raise IntegrityError(f"supervisor evidence regressed: {field}")
        if (
            previous["observed_session_ref"] is not None
            and snapshot["observed_session_ref"] != previous["observed_session_ref"]
        ):
            raise IntegrityError("supervisor session ref changed")
        for field in {"process_exit_code", "termination_kind"}:
            if previous[field] is not None and snapshot[field] != previous[field]:
                raise IntegrityError(f"supervisor result changed: {field}")
        if parse_rfc3339(snapshot["updated_at"]) < parse_rfc3339(
            previous["updated_at"]
        ):
            raise IntegrityError("supervisor updated_at cannot move backward")
    atomic_json(path, snapshot, immutable=initial)


class StreamRecorder:
    def __init__(
        self,
        *,
        run_dir: Path,
        turn_id: str,
        adapter_id: str,
        snapshot: dict[str, Any],
        max_bytes: int = 64 * 1024 * 1024,
    ) -> None:
        self.run_dir = run_dir
        self.turn_id = turn_id
        self.process_dir = run_dir / "turns" / turn_id / "process"
        self.stream_path = self.process_dir / "stream.jsonl"
        self.stream_fd = create_empty_regular(self.stream_path)
        self.stderr_fd = create_empty_regular(self.process_dir / "stderr.log")
        self.seq = 0
        self.lock = asyncio.Lock()
        self.buffers = {
            "stdout": bytearray(),
            "stderr": bytearray(),
            "terminal": bytearray(),
        }
        self.first_seq: dict[str, int | None] = {
            "stdout": None,
            "stderr": None,
            "terminal": None,
        }
        self.adapter = get_adapter(adapter_id)
        self.evidence = AdapterEvidenceSnapshot()
        self.snapshot = snapshot
        self.max_bytes = max_bytes
        self.source_bytes = 0
        self.stored_source_bytes = 0
        self.dropped_source_bytes = 0
        self.chunks_observed = 0
        self.chunks_stored = 0
        self.truncated = False
        stream_info = os.fstat(self.stream_fd)
        self.stream_identity = (stream_info.st_dev, stream_info.st_ino)

    async def close(self) -> None:
        os.fsync(self.stream_fd)
        os.fsync(self.stderr_fd)
        os.close(self.stream_fd)
        os.close(self.stderr_fd)
        atomic_json(
            self.process_dir / "capture.json",
            {
                "schema_version": 1,
                "source_bytes": self.source_bytes,
                "stored_source_bytes": self.stored_source_bytes,
                "dropped_source_bytes": self.dropped_source_bytes,
                "chunks_observed": self.chunks_observed,
                "chunks_stored": self.chunks_stored,
                "truncated": self.truncated,
                "closed_at": rfc3339(),
            },
            immutable=True,
        )

    def stream_path_is_original(self) -> bool:
        try:
            path_info = self.stream_path.lstat()
            fd_info = os.fstat(self.stream_fd)
        except OSError:
            return False
        return (
            stat.S_ISREG(path_info.st_mode)
            and not self.stream_path.is_symlink()
            and (path_info.st_dev, path_info.st_ino) == self.stream_identity
            and (fd_info.st_dev, fd_info.st_ino) == self.stream_identity
        )

    async def record(self, source: str, data: bytes) -> None:
        if source not in self.buffers:
            raise IntegrityError(f"unsupported Harness stream source: {source}")
        if not data:
            return
        async with self.lock:
            self.chunks_observed += 1
            self.source_bytes += len(data)
            self.seq += 1
            seq = self.seq
            observed_at = rfc3339()
            remaining = max(0, self.max_bytes - self.stored_source_bytes)
            stored_data = data[:remaining]
            dropped = len(data) - len(stored_data)
            self.stored_source_bytes += len(stored_data)
            self.dropped_source_bytes += dropped
            if dropped:
                self.truncated = True
            if stored_data:
                self.chunks_stored += 1
            try:
                text = stored_data.decode("utf-8")
                encoding = "utf-8"
                stored = text
            except UnicodeDecodeError:
                encoding = "base64"
                stored = base64.b64encode(stored_data).decode("ascii")
            if stored_data:
                outer = {
                    "schema_version": 1,
                    "seq": seq,
                    "observed_at": observed_at,
                    "source": source,
                    "encoding": encoding,
                    "data": stored,
                }
                line = (
                    json.dumps(outer, ensure_ascii=False, separators=(",", ":"))
                    + "\n"
                ).encode("utf-8")
                write_all(self.stream_fd, line)
                if source == "stderr":
                    write_all(self.stderr_fd, stored_data)
            # Native TUI bytes are a terminal recording, not a line-oriented
            # structured protocol. Interactive execution evidence is merged
            # explicitly by the PTY relay and Pane text never controls state.
            if source == "terminal":
                return
            if self.first_seq[source] is None:
                self.first_seq[source] = seq
            self.buffers[source].extend(data)
            while True:
                newline = self.buffers[source].find(b"\n")
                if newline < 0:
                    break
                record_bytes = bytes(self.buffers[source][: newline + 1])
                del self.buffers[source][: newline + 1]
                first = self.first_seq[source] or seq
                self.first_seq[source] = seq if self.buffers[source] else None
                try:
                    record_data = record_bytes.decode("utf-8")
                    record_encoding = "utf-8"
                except UnicodeDecodeError:
                    record_data = base64.b64encode(record_bytes).decode("ascii")
                    record_encoding = "base64"
                record = StreamRecord(
                    source=source,
                    first_seq=first,
                    last_seq=seq,
                    observed_at=observed_at,
                    encoding=record_encoding,
                    data=record_data,
                )
                evidence = self.adapter.parse_stream_record(record)
                if evidence is not None:
                    # Evidence may only become durable after its raw source.
                    os.fsync(self.stream_fd)
                    changed = self.evidence.merge(evidence)
                    if changed:
                        self.snapshot.update(self.evidence.to_json())
                        if (
                            self.snapshot["agent_execution_started"]
                            and self.snapshot["state"] == "waiting_authorization"
                        ):
                            self.snapshot["state"] = "running"
                        with locked_run(self.run_dir, exclusive=True):
                            if self.evidence.session_unavailable_reason is not None:
                                projection = scan_journal(self.run_dir)
                                runtime = load_runtime(
                                    self.run_dir / "turns" / self.turn_id,
                                    team=projection.team,
                                )
                                role = projection.team.roles[runtime["role_id"]]
                                mark_session_unavailable(
                                    self.run_dir,
                                    role=role,
                                    runtime=runtime,
                                    reason=self.evidence.session_unavailable_reason,
                                )
                            _save_snapshot(
                                self.run_dir,
                                self.turn_id,
                                self.snapshot,
                            )

    async def merge_evidence(self, evidence: AdapterEvidence) -> None:
        async with self.lock:
            changed = self.evidence.merge(evidence)
            if not changed:
                return
            self.snapshot.update(self.evidence.to_json())
            if (
                self.snapshot["agent_execution_started"]
                and self.snapshot["state"] == "waiting_authorization"
            ):
                self.snapshot["state"] = "running"
            with locked_run(self.run_dir, exclusive=True):
                _save_snapshot(self.run_dir, self.turn_id, self.snapshot)


async def _read_pipe(
    stream: asyncio.StreamReader,
    source: str,
    recorder: StreamRecorder,
) -> None:
    while True:
        chunk = await stream.read(65536)
        if not chunk:
            return
        await recorder.record(source, chunk)


def _terminal_size(fd: int) -> tuple[int, int]:
    try:
        size = os.get_terminal_size(fd)
    except OSError:
        return 40, 120
    return max(1, size.lines), max(1, size.columns)


def _set_pty_size(fd: int, size: tuple[int, int]) -> None:
    rows, columns = size
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, columns, 0, 0))


def _fresh_interactive_session_candidate(
    adapter: Any,
    launch: LaunchSpec,
    *,
    baseline: set[str],
    observed: str | None,
) -> str | None:
    current_refs = adapter.interactive_session_refs(launch)
    candidates = current_refs - baseline
    if len(candidates) > 1:
        raise IntegrityError(
            "interactive Harness created multiple candidate Sessions"
        )
    if not candidates:
        if observed is not None:
            raise IntegrityError(
                "interactive Harness Session candidate disappeared"
            )
        return None
    candidate = next(iter(candidates))
    if observed not in {None, candidate}:
        raise IntegrityError("interactive Harness Session candidate changed")
    return candidate


async def _read_pty(
    master_fd: int,
    recorder: StreamRecorder,
    *,
    expected_session_ref: str | None,
) -> None:
    os.set_blocking(master_fd, False)
    execution_observed = False
    try:
        while True:
            try:
                chunk = os.read(master_fd, 65536)
            except BlockingIOError:
                await asyncio.sleep(0.02)
                continue
            except OSError as exc:
                if exc.errno in {errno.EIO, errno.EBADF}:
                    return
                raise
            if not chunk:
                return
            with contextlib.suppress(OSError):
                write_all(sys.stdout.fileno(), chunk)
            await recorder.record("terminal", chunk)
            if not execution_observed:
                await recorder.merge_evidence(
                    AdapterEvidence(
                        agent_execution_started=True,
                        observed_session_ref=expected_session_ref,
                    )
                )
                execution_observed = True
    finally:
        with contextlib.suppress(OSError):
            os.close(master_fd)


async def _relay_terminal_input(master_fd: int) -> None:
    try:
        source_fd = sys.stdin.fileno()
        if not os.isatty(source_fd):
            return
        original_flags = fcntl.fcntl(source_fd, fcntl.F_GETFL)
        original_termios = termios.tcgetattr(source_fd)
    except (AttributeError, OSError, ValueError, termios.error):
        return
    try:
        # Native TUIs consume individual bytes. Canonical mode would buffer a
        # line, ECHO would duplicate it in the Worker pane, and ICRNL would
        # rewrite Enter before it reached the Harness PTY.
        tty.setraw(source_fd, when=termios.TCSANOW)
        fcntl.fcntl(source_fd, fcntl.F_SETFL, original_flags | os.O_NONBLOCK)
        while True:
            try:
                data = os.read(source_fd, 4096)
            except BlockingIOError:
                await asyncio.sleep(0.02)
                continue
            except OSError as exc:
                if exc.errno in {errno.EIO, errno.EBADF}:
                    return
                raise
            if not data:
                return
            write_all(master_fd, data)
    finally:
        with contextlib.suppress(OSError):
            fcntl.fcntl(source_fd, fcntl.F_SETFL, original_flags)
        with contextlib.suppress(OSError, termios.error):
            termios.tcsetattr(
                source_fd,
                termios.TCSANOW,
                original_termios,
            )


async def _read_status_fd(fd: int) -> bytes:
    try:
        return await asyncio.to_thread(os.read, fd, 65536)
    finally:
        os.close(fd)


async def _wait_for_runner(
    path: Path,
    *,
    turn_id: str,
    nonce: str,
    process: asyncio.subprocess.Process,
    timeout: float = 15.0,
) -> dict[str, Any]:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if path_entry_exists(path):
            value = validate_runner(read_json(path), turn_id=turn_id, nonce=nonce)
            if value["runner_pid"] != process.pid:
                raise IntegrityError("runner identity PID does not match child")
            return value
        if process.returncode is not None:
            raise IntegrityError("runner exited before persisting its process identity")
        await asyncio.sleep(0.05)
    raise IntegrityError("runner identity was not persisted before timeout")


async def _terminate_group(
    *,
    runner: dict[str, Any],
    allow_reaped_leader: bool = False,
    term_timeout: float = 2.0,
    kill_timeout: float = 2.0,
) -> bool:
    pgid = runner["runner_pgid"]
    if not process_group_exists(pgid):
        return True
    runner_alive = process_start_id(runner["runner_pid"])
    if runner_alive is None:
        if not allow_reaped_leader:
            return False
    elif not identity_matches(
        runner["runner_pid"],
        runner["runner_start_id"],
        pgid=pgid,
    ):
        return False
    with contextlib.suppress(ProcessLookupError):
        os.killpg(pgid, signal.SIGTERM)
    deadline = asyncio.get_running_loop().time() + term_timeout
    while asyncio.get_running_loop().time() < deadline:
        if not process_group_exists(pgid):
            return True
        await asyncio.sleep(0.05)
    with contextlib.suppress(ProcessLookupError):
        os.killpg(pgid, signal.SIGKILL)
    deadline = asyncio.get_running_loop().time() + kill_timeout
    while asyncio.get_running_loop().time() < deadline:
        if not process_group_exists(pgid):
            return True
        await asyncio.sleep(0.05)
    return not process_group_exists(pgid)


async def supervise_turn(
    run_dir: Path,
    turn_id: str,
    nonce: str,
    launch_sha256: str,
) -> int:
    turn_dir = run_dir / "turns" / turn_id
    process_dir = turn_dir / "process"
    process_dir.mkdir(mode=0o700, exist_ok=True)
    launch = LaunchSpec.from_json(read_json(process_dir / "launch.json"))
    if launch.content_sha256() != launch_sha256:
        raise IntegrityError("LaunchSpec does not match the authorized launch digest")
    adapter = get_adapter(launch.adapter_id)
    interactive_session_baseline = (
        adapter.interactive_session_refs(launch)
        if (
            launch.launch_mode == "interactive"
            and launch.expected_session_ref is None
        )
        else set()
    )
    snapshot = _base_snapshot(turn_id, nonce)
    with locked_run(run_dir, exclusive=True):
        projection = scan_journal(run_dir)
        runtime = load_runtime(turn_dir, team=projection.team)
        if runtime["launch_nonce"] != nonce:
            raise IntegrityError("supervisor nonce does not match runtime")
        if projection.terminal_for_turn(turn_id) is not None:
            # A terminal Event won the race before Runner creation.
            return 0
        _save_snapshot(run_dir, turn_id, snapshot, initial=True)
        policy = projection.team.observability
    recorder = StreamRecorder(
        run_dir=run_dir,
        turn_id=turn_id,
        adapter_id=launch.adapter_id,
        snapshot=snapshot,
        max_bytes=policy.max_trace_bytes,
    )
    status_r, status_w = os.pipe()
    os.set_inheritable(status_w, True)
    runner_argv = [
        sys.executable,
        "-m",
        "agent_team",
        "_harness-runner",
        "--run-dir",
        str(run_dir),
        "--turn",
        turn_id,
        f"--nonce={nonce}",
        "--launch-sha256",
        launch_sha256,
        "--supervisor-pid",
        str(snapshot["supervisor_pid"]),
        "--supervisor-start-id",
        snapshot["supervisor_start_id"],
        "--status-fd",
        str(status_w),
    ]
    terminal_task: asyncio.Task[None] | None = None
    terminal_input_task: asyncio.Task[None] | None = None
    master_fd: int | None = None
    if launch.launch_mode == "interactive":
        master_fd, slave_fd = pty.openpty()
        _set_pty_size(slave_fd, _terminal_size(sys.stdout.fileno()))
        try:
            process = await asyncio.create_subprocess_exec(
                *runner_argv,
                stdin=slave_fd,
                stdout=slave_fd,
                stderr=slave_fd,
                start_new_session=True,
                pass_fds=(status_w,),
            )
        finally:
            os.close(slave_fd)
        terminal_task = asyncio.create_task(
            _read_pty(
                master_fd,
                recorder,
                expected_session_ref=launch.expected_session_ref,
            )
        )
        terminal_input_task = asyncio.create_task(_relay_terminal_input(master_fd))
    else:
        process = await asyncio.create_subprocess_exec(
            *runner_argv,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
            pass_fds=(status_w,),
        )
    os.close(status_w)
    status_task = asyncio.create_task(_read_status_fd(status_r))
    runner_path = process_dir / "runner.json"
    try:
        runner = await _wait_for_runner(
            runner_path,
            turn_id=turn_id,
            nonce=nonce,
            process=process,
        )
    except BaseException as exc:
        with contextlib.suppress(ProcessLookupError):
            process.terminate()
        if launch.launch_mode == "interactive":
            await process.wait()
            if terminal_task is not None:
                with contextlib.suppress(Exception):
                    await asyncio.wait_for(terminal_task, timeout=2.0)
            if terminal_input_task is not None:
                terminal_input_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await terminal_input_task
        else:
            stdout_bytes, stderr_bytes = await process.communicate()
            if stdout_bytes:
                await recorder.record("stdout", stdout_bytes)
            if stderr_bytes:
                await recorder.record("stderr", stderr_bytes)
        status_bytes = await status_task
        if status_bytes:
            atomic_json(
                process_dir / "exec-error.json",
                _parse_exec_error_bytes(status_bytes),
                immutable=True,
            )
        elif not isinstance(exc, asyncio.CancelledError):
            atomic_json(
                process_dir / "exec-error.json",
                {
                    "schema_version": 1,
                    "code": "RUNNER_BOOTSTRAP_FAILED",
                    "message": f"{type(exc).__name__}: {exc}",
                },
                immutable=True,
            )
        snapshot["state"] = "finished"
        snapshot["process_exit_code"] = process.returncode
        snapshot["termination_kind"] = "unknown"
        snapshot["group_quiescent"] = True
        await recorder.close()
        with locked_run(run_dir, exclusive=True):
            _save_snapshot(run_dir, turn_id, snapshot)
        raise
    snapshot.update(
        {
            "state": "waiting_authorization",
            "runner_pid": runner["runner_pid"],
            "runner_pgid": runner["runner_pgid"],
            "runner_start_id": runner["runner_start_id"],
        }
    )
    with locked_run(run_dir, exclusive=True):
        _save_snapshot(run_dir, turn_id, snapshot)
    if launch.launch_mode == "interactive":
        assert terminal_task is not None and master_fd is not None
        reader_tasks = (terminal_task,)
        last_terminal_size = _terminal_size(sys.stdout.fileno())
    else:
        assert process.stdout is not None and process.stderr is not None
        stdout_task = asyncio.create_task(
            _read_pipe(process.stdout, "stdout", recorder)
        )
        stderr_task = asyncio.create_task(
            _read_pipe(process.stderr, "stderr", recorder)
        )
        reader_tasks = (stdout_task, stderr_task)
        last_terminal_size = None
    launch_activated = False
    interactive_action_staged = False
    termination_reason: str | None = None
    auth_path = process_dir / "launch-authorized.json"
    while process.returncode is None:
        try:
            await asyncio.wait_for(process.wait(), timeout=0.2)
        except asyncio.TimeoutError:
            pass
        reader_failure = next(
            (
                task.exception()
                for task in reader_tasks
                if task.done() and not task.cancelled() and task.exception() is not None
            ),
            None,
        )
        if reader_failure is not None:
            termination_reason = "corrupted"
        if launch.launch_mode == "interactive":
            try:
                current_size = _terminal_size(sys.stdout.fileno())
                if current_size != last_terminal_size and master_fd is not None:
                    _set_pty_size(master_fd, current_size)
                    last_terminal_size = current_size
                if launch.expected_session_ref is None:
                    candidate = _fresh_interactive_session_candidate(
                        adapter,
                        launch,
                        baseline=interactive_session_baseline,
                        observed=recorder.evidence.observed_session_ref,
                    )
                    if candidate is not None:
                        await recorder.merge_evidence(
                            AdapterEvidence(observed_session_ref=candidate)
                        )
            except (IntegrityError, OSError):
                termination_reason = "corrupted"
        authorization: dict[str, Any] | None = None
        try:
            with locked_run(run_dir, exclusive=True):
                projection = scan_journal(run_dir)
                terminal = projection.terminal_for_turn(turn_id)
                runtime = load_runtime(turn_dir, team=projection.team)
                owner = read_owner(projection.team.workspace)
                if owner is None or owner["run_id"] != projection.team.run_id:
                    raise IntegrityError(
                        "workspace ownership changed during External execution"
                    )
                input_bytes = read_regular(turn_dir / "input.md")
                if sha256_bytes(input_bytes) != runtime["input_payload_sha256"]:
                    raise IntegrityError("frozen Turn input changed during execution")
                persisted_launch = LaunchSpec.from_json(
                    read_json(process_dir / "launch.json")
                )
                if persisted_launch != launch:
                    raise IntegrityError("LaunchSpec changed during execution")
                if launch.launch_mode == "interactive" and (
                    launch.prompt_file is None
                    or read_regular(Path(launch.prompt_file))
                    != launch.stdin.encode("utf-8")
                ):
                    raise IntegrityError("interactive prompt changed during execution")
                persisted_runner = validate_runner(
                    read_json(runner_path),
                    turn_id=turn_id,
                    nonce=nonce,
                )
                if persisted_runner != runner:
                    raise IntegrityError("Runner identity changed during execution")
                if path_entry_exists(auth_path):
                    authorization = validate_authorization(
                        read_json(auth_path),
                        turn_id=turn_id,
                        nonce=nonce,
                    )
                    if (
                        authorization["supervisor_pid"] != snapshot["supervisor_pid"]
                        or authorization["supervisor_start_id"]
                        != snapshot["supervisor_start_id"]
                        or authorization["runner_pid"] != runner["runner_pid"]
                        or authorization["runner_pgid"] != runner["runner_pgid"]
                        or authorization["runner_start_id"] != runner["runner_start_id"]
                        or authorization["launch_profile"] != launch.launch_profile
                        or authorization["launch_profile_sha256"]
                        != launch.launch_profile_sha256
                    ):
                        raise IntegrityError(
                            "launch authorization does not match identities/spec"
                        )
                elif launch_activated:
                    raise IntegrityError("consumed launch authorization disappeared")
                deadline = parse_rfc3339(
                    projection.kickoff["created_at"]
                ) + dt.timedelta(seconds=projection.team.max_wall_time_seconds)
                if terminal is not None:
                    termination_reason = terminal["event_type"]
                    if terminal["event_type"] == "block":
                        termination_reason = terminal.get("limit_reason") or "block"
                elif dt.datetime.now(dt.timezone.utc) >= deadline:
                    terminal = commit_technical_block_locked(
                        run_dir,
                        runtime=runtime,
                        reason="limit",
                        limit_reason="deadline",
                        message="External turn exceeded the run wall-time deadline.",
                    )
                    termination_reason = "deadline"
                elif recorder.evidence.permission_required:
                    terminal = commit_technical_block_locked(
                        run_dir,
                        runtime=runtime,
                        reason="permission",
                        message="Harness emitted structured permission-required evidence.",
                    )
                    termination_reason = "permission"
                elif recorder.evidence.session_unavailable_reason is not None:
                    termination_reason = "session_unavailable"
                if launch.launch_mode == "interactive":
                    outbox = load_outbox(turn_dir)
                    if outbox is not None:
                        if not launch_activated and authorization is None:
                            raise IntegrityError(
                                "interactive Outbox appeared before launch authorization"
                            )
                        interactive_action_staged = True
        except (IntegrityError, OSError):
            termination_reason = "corrupted"
        if (
            termination_reason is None
            and authorization is not None
            and not launch_activated
        ):
            if launch.launch_mode == "headless" and process.stdin is not None:
                process.stdin.write(launch.stdin.encode("utf-8"))
                await process.stdin.drain()
                process.stdin.close()
            launch_activated = True
        if (
            termination_reason is None
            and interactive_action_staged
            and recorder.evidence.agent_execution_started
            and recorder.evidence.observed_session_ref is not None
        ):
            await recorder.merge_evidence(
                AdapterEvidence(adapter_completed=True)
            )
            # A native TUI normally remains open after completing one model
            # turn. The durable, validated Outbox is the completion boundary;
            # stop the verified Runner group without pretending its real
            # signal-derived process exit code was zero.
            termination_reason = "action_staged"
        if termination_reason is not None:
            if snapshot["state"] != "stopping":
                snapshot["state"] = "stopping"
                try:
                    with locked_run(run_dir, exclusive=True):
                        _save_snapshot(run_dir, turn_id, snapshot)
                except (IntegrityError, OSError):
                    # Corrupted durable state must not prevent best-effort cleanup
                    # of the already verified Runner process group.
                    pass
            await _terminate_group(runner=runner)
    return_code = await process.wait()
    # Reap the leader before testing the process group, then clear ordinary children.
    await asyncio.sleep(0.2)
    group_quiescent = await _terminate_group(
        runner=runner,
        allow_reaped_leader=True,
    )
    for task in reader_tasks:
        try:
            await asyncio.wait_for(task, timeout=2.0)
        except asyncio.TimeoutError:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        except Exception:
            termination_reason = termination_reason or "corrupted"
    if terminal_input_task is not None:
        terminal_input_task.cancel()
        with contextlib.suppress(asyncio.CancelledError, OSError):
            await terminal_input_task
    status_bytes = await status_task
    if status_bytes:
        atomic_json(
            process_dir / "exec-error.json",
            _parse_exec_error_bytes(status_bytes),
            immutable=True,
        )
    if not recorder.stream_path_is_original():
        termination_reason = termination_reason or "corrupted"
    if termination_reason == "deadline":
        termination_kind = "deadline"
    elif termination_reason in {"cancel", "cancelled"}:
        termination_kind = "cancelled"
    elif termination_reason == "action_staged":
        termination_kind = "action"
    elif termination_reason is not None:
        termination_kind = "signal"
    elif return_code == 0:
        termination_kind = "normal"
    elif return_code < 0:
        termination_kind = "signal"
    else:
        termination_kind = "crash"
    recoverable_kind = None
    if termination_kind == "crash":
        recoverable_kind = adapter.recoverable_termination_kind(
            ProcessResult(
                process_exit_code=return_code,
                termination_kind=termination_kind,
                group_quiescent=group_quiescent,
                launch_mode=launch.launch_mode,
            ),
            recorder.evidence,
        )
    if recoverable_kind is not None:
        if recoverable_kind != "output_limit":
            raise IntegrityError(
                f"adapter returned unsupported recoverable termination kind: "
                f"{recoverable_kind}"
            )
        termination_kind = recoverable_kind
    snapshot.update(recorder.evidence.to_json())
    snapshot.update(
        {
            "state": "finished" if group_quiescent else "stopping",
            "process_exit_code": return_code,
            "termination_kind": termination_kind,
            "group_quiescent": group_quiescent,
        }
    )
    await recorder.close()
    # The Worker anchors and validates the normalized trace after it verifies
    # the Runner process group is quiescent; do not finalize here.
    with locked_run(run_dir, exclusive=True):
        _save_snapshot(run_dir, turn_id, snapshot)
    return 0 if group_quiescent else 1


def run_supervisor(
    run_dir: Path,
    turn_id: str,
    nonce: str,
    launch_sha256: str,
) -> int:
    set_private_umask()
    return asyncio.run(supervise_turn(run_dir, turn_id, nonce, launch_sha256))
