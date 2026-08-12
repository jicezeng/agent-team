from __future__ import annotations

import contextlib
import json
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from agent_team.adapters.base import LaunchSpec
from agent_team.errors import IntegrityError
from agent_team.runner import run_harness_runner
from agent_team.supervisor import validate_exec_error, validate_runner
from agent_team.util import atomic_json, rfc3339


PROFILE_HASH = "0" * 64
TURN_ID = "turn-0001"
NONCE = "runner-test-nonce"
SUPERVISOR_PID = 700_001
RUNNER_PID = 700_002


def test_runner_identity_rejects_boolean_pid_fields() -> None:
    value = {
        "schema_version": 1,
        "turn_id": TURN_ID,
        "launch_nonce": NONCE,
        "runner_pid": True,
        "runner_pgid": True,
        "runner_start_id": "runner-start",
        "created_at": rfc3339(),
    }

    with pytest.raises(IntegrityError, match="process group"):
        validate_runner(value, turn_id=TURN_ID, nonce=NONCE)


@pytest.mark.parametrize("schema_version", [True, 1.0])
def test_runner_identity_rejects_non_integer_schema_version(
    schema_version: object,
) -> None:
    value = {
        "schema_version": schema_version,
        "turn_id": TURN_ID,
        "launch_nonce": NONCE,
        "runner_pid": RUNNER_PID,
        "runner_pgid": RUNNER_PID,
        "runner_start_id": "runner-start",
        "created_at": rfc3339(),
    }

    with pytest.raises(IntegrityError, match="unsupported runner identity schema"):
        validate_runner(value, turn_id=TURN_ID, nonce=NONCE)


@pytest.mark.parametrize(
    "value",
    [
        {"schema_version": True, "code": "EXEC_FAILED", "message": "failed"},
        {"schema_version": 1.0, "code": "EXEC_FAILED", "message": "failed"},
        {"schema_version": 1, "code": "UNKNOWN", "message": "failed"},
        {"schema_version": 1, "code": "EXEC_FAILED", "message": ""},
    ],
)
def test_runner_status_uses_a_closed_schema(value: dict[str, object]) -> None:
    with pytest.raises(IntegrityError):
        validate_exec_error(value)


def _runner_fixture(
    root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[Path, LaunchSpec]:
    run_dir = root / "at-runner-test"
    process_dir = run_dir / "turns" / TURN_ID / "process"
    process_dir.mkdir(parents=True)
    launch = LaunchSpec(
        adapter_id="codex",
        argv=("/bin/true",),
        cwd=str(root),
        env={},
        stdin="",
        launch_profile="test-profile",
        launch_profile_sha256=PROFILE_HASH,
        starts_new_session=True,
    )
    atomic_json(process_dir / "launch.json", launch.to_json(), immutable=True)
    atomic_json(
        process_dir / "launch-authorized.json",
        {
            "schema_version": 1,
            "turn_id": TURN_ID,
            "launch_nonce": NONCE,
            "supervisor_pid": SUPERVISOR_PID,
            "supervisor_start_id": "supervisor-start",
            "runner_pid": RUNNER_PID,
            "runner_pgid": RUNNER_PID,
            "runner_start_id": "runner-start",
            "launch_profile": launch.launch_profile,
            "launch_profile_sha256": launch.launch_profile_sha256,
            "authorized_at": rfc3339(),
        },
        immutable=True,
    )
    runtime = {
        "turn_id": TURN_ID,
        "executor": "worker",
        "phase": "running",
        "launch_nonce": NONCE,
        "supervisor_pid": SUPERVISOR_PID,
        "supervisor_start_id": "supervisor-start",
        "runner_pid": RUNNER_PID,
        "runner_pgid": RUNNER_PID,
        "runner_start_id": "runner-start",
        "launch_profile": launch.launch_profile,
        "launch_profile_sha256": launch.launch_profile_sha256,
    }
    monkeypatch.setattr("agent_team.runner.set_private_umask", lambda: None)
    monkeypatch.setattr(
        "agent_team.runner.current_identity",
        lambda **_kwargs: SimpleNamespace(
            pid=RUNNER_PID,
            pgid=RUNNER_PID,
            start_id="runner-start",
        ),
    )
    monkeypatch.setattr(
        "agent_team.runner.locked_run",
        lambda *_args, **_kwargs: contextlib.nullcontext(),
    )
    monkeypatch.setattr(
        "agent_team.runner.load_runtime",
        lambda *_args, **_kwargs: runtime,
    )
    monkeypatch.setattr(
        "agent_team.runner.load_team",
        lambda _run_dir: SimpleNamespace(),
    )
    monkeypatch.setattr(
        "agent_team.runner.identity_matches",
        lambda *_args, **_kwargs: True,
    )
    return run_dir, launch


def _invoke_runner(
    run_dir: Path,
    launch_sha256: str,
) -> tuple[int, dict[str, Any]]:
    status_r, status_w = os.pipe()
    try:
        result = run_harness_runner(
            run_dir=run_dir,
            turn_id=TURN_ID,
            nonce=NONCE,
            launch_sha256=launch_sha256,
            supervisor_pid=SUPERVISOR_PID,
            supervisor_start_id="supervisor-start",
            status_fd=status_w,
        )
        status = json.loads(os.read(status_r, 65536))
        return result, status
    finally:
        os.close(status_r)
        os.close(status_w)


def test_runner_rejects_launch_spec_digest_mismatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_dir, _launch = _runner_fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "agent_team.runner.os.execvpe",
        lambda *_args, **_kwargs: pytest.fail("mismatched LaunchSpec was executed"),
    )

    result, status = _invoke_runner(run_dir, "f" * 64)

    assert result == 76
    assert status["code"] == "AUTHORIZATION_INVALID"
    assert "identity/profile/spec mismatch" in status["message"]


def test_runner_reports_pre_exec_working_directory_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_dir, launch = _runner_fixture(tmp_path, monkeypatch)

    def fail_chdir(_path: str) -> None:
        raise FileNotFoundError("injected missing working directory")

    monkeypatch.setattr("agent_team.runner.os.chdir", fail_chdir)
    monkeypatch.setattr(
        "agent_team.runner.os.execvpe",
        lambda *_args, **_kwargs: pytest.fail("exec should not be reached"),
    )

    result, status = _invoke_runner(run_dir, launch.content_sha256())

    assert result == 71
    assert status["code"] == "EXEC_FAILED"
    assert "FileNotFoundError" in status["message"]


def test_runner_reports_failure_before_identity_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_dir, launch = _runner_fixture(tmp_path, monkeypatch)

    @contextlib.contextmanager
    def fail_lock(*_args: Any, **_kwargs: Any) -> Any:
        raise IntegrityError("injected workspace lock failure")
        yield

    monkeypatch.setattr("agent_team.runner.locked_run", fail_lock)

    result, status = _invoke_runner(run_dir, launch.content_sha256())

    assert result == 72
    assert status["code"] == "RUNNER_BOOTSTRAP_FAILED"
    assert "workspace lock failure" in status["message"]
