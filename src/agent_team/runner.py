from __future__ import annotations

import contextlib
import json
import os
import time
from pathlib import Path

from .adapters.base import LaunchSpec
from .errors import IntegrityError
from .processes import current_identity, identity_matches
from .state import locked_run
from .supervisor import validate_authorization
from .turns import load_runtime
from .util import (
    atomic_json,
    path_entry_exists,
    read_json,
    rfc3339,
    set_private_umask,
    write_all,
)


def _status(fd: int, code: str, message: str) -> None:
    data = json.dumps(
        {"schema_version": 1, "code": code, "message": message},
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    write_all(fd, data)


def _run_harness_runner(
    *,
    run_dir: Path,
    turn_id: str,
    nonce: str,
    launch_sha256: str,
    supervisor_pid: int,
    supervisor_start_id: str,
    status_fd: int,
) -> int:
    identity = current_identity(include_pgid=True)
    if identity.pgid != identity.pid:
        _status(status_fd, "RUNNER_NOT_GROUP_LEADER", "runner does not lead its PGID")
        return 70
    process_dir = run_dir / "turns" / turn_id / "process"
    runner = {
        "schema_version": 1,
        "turn_id": turn_id,
        "launch_nonce": nonce,
        "runner_pid": identity.pid,
        "runner_pgid": identity.pgid,
        "runner_start_id": identity.start_id,
        "created_at": rfc3339(),
    }
    with locked_run(run_dir, exclusive=True):
        atomic_json(process_dir / "runner.json", runner, immutable=True)
    authorization_path = process_dir / "launch-authorized.json"
    deadline = time.monotonic() + 30.0
    while time.monotonic() < deadline:
        if path_entry_exists(authorization_path):
            break
        time.sleep(0.05)
    else:
        _status(
            status_fd, "AUTHORIZATION_TIMEOUT", "launch authorization was not committed"
        )
        return 75
    try:
        with locked_run(run_dir, exclusive=False):
            authorization = validate_authorization(
                read_json(authorization_path),
                turn_id=turn_id,
                nonce=nonce,
            )
            runtime = load_runtime(
                run_dir / "turns" / turn_id,
                team=None,
            )
            launch = LaunchSpec.from_json(read_json(process_dir / "launch.json"))
            if (
                launch.content_sha256() != launch_sha256
                or authorization["supervisor_pid"] != supervisor_pid
                or authorization["supervisor_start_id"] != supervisor_start_id
                or authorization["runner_pid"] != identity.pid
                or authorization["runner_pgid"] != identity.pgid
                or authorization["runner_start_id"] != identity.start_id
                or authorization["launch_profile"] != launch.launch_profile
                or authorization["launch_profile_sha256"]
                != launch.launch_profile_sha256
                or runtime["turn_id"] != turn_id
                or runtime["executor"] != "worker"
                or runtime["phase"] != "running"
                or runtime["launch_nonce"] != nonce
                or runtime["supervisor_pid"] != supervisor_pid
                or runtime["supervisor_start_id"] != supervisor_start_id
                or runtime["runner_pid"] != identity.pid
                or runtime["runner_pgid"] != identity.pgid
                or runtime["runner_start_id"] != identity.start_id
                or runtime["launch_profile"] != launch.launch_profile
                or runtime["launch_profile_sha256"] != launch.launch_profile_sha256
            ):
                raise IntegrityError(
                    "launch authorization identity/profile/spec mismatch"
                )
            if not identity_matches(supervisor_pid, supervisor_start_id):
                raise IntegrityError(
                    "authorizing supervisor identity is no longer valid"
                )
    except BaseException as exc:
        _status(status_fd, "AUTHORIZATION_INVALID", str(exc))
        return 76
    env = os.environ.copy()
    env.update(launch.env)
    try:
        os.chdir(launch.cwd)
        os.set_inheritable(status_fd, False)
        os.execvpe(launch.argv[0], list(launch.argv), env)
    except OSError as exc:
        _status(status_fd, "EXEC_FAILED", f"{type(exc).__name__}: {exc}")
        return 71


def run_harness_runner(
    *,
    run_dir: Path,
    turn_id: str,
    nonce: str,
    launch_sha256: str,
    supervisor_pid: int,
    supervisor_start_id: str,
    status_fd: int,
) -> int:
    try:
        set_private_umask()
        return _run_harness_runner(
            run_dir=run_dir,
            turn_id=turn_id,
            nonce=nonce,
            launch_sha256=launch_sha256,
            supervisor_pid=supervisor_pid,
            supervisor_start_id=supervisor_start_id,
            status_fd=status_fd,
        )
    except Exception as exc:
        with contextlib.suppress(OSError):
            _status(
                status_fd,
                "RUNNER_BOOTSTRAP_FAILED",
                f"{type(exc).__name__}: {exc}",
            )
        return 72
