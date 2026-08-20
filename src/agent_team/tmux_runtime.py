from __future__ import annotations

import hashlib
import os
import shlex
import shutil
import subprocess
import sys
import time
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from .config import Role, Team
from .errors import AgentTeamError, IntegrityError
from .processes import process_identity_state, process_start_id
from .util import atomic_json, path_entry_exists, read_json, rfc3339


def tmux_executable() -> str:
    executable = shutil.which("tmux")
    if not executable:
        raise AgentTeamError("TMUX_NOT_FOUND", "tmux is required for External roles")
    return str(Path(executable).resolve(strict=True))


def session_name(run_id: str) -> str:
    cleaned = "".join(
        char if char.isalnum() or char in "_-" else "-" for char in run_id
    )
    return f"agent-team-{cleaned}"[:80]


def server_name(run_id: str) -> str:
    """Return the per-Run tmux server socket label.

    A shared tmux server keeps the environment from the process that first
    created it. Reusing that server for a later Run can therefore launch
    Workers with stale Harness credentials, endpoints, models, or proxy
    settings. A deterministic per-Run server makes the first ``new-session``
    inherit the current Agent-Team process environment without putting secrets
    on a tmux command line.
    """

    digest = hashlib.sha256(run_id.encode("utf-8")).hexdigest()[:24]
    return f"agent-team-{digest}"


def change_channel(run_id: str, role_id: str) -> str:
    return f"agent-team:{run_id}:{role_id}:changed"


def _run(
    *args: str,
    check: bool = True,
    server: str | None = None,
    sensitive_values: Iterable[str] = (),
) -> subprocess.CompletedProcess[str]:
    server_args = ["-L", server] if server is not None else []
    result = subprocess.run(
        [tmux_executable(), *server_args, *args],
        capture_output=True,
        text=True,
        check=False,
    )
    if check and result.returncode != 0:
        secrets = tuple(value for value in sensitive_values if value)

        def redact(value: str) -> str:
            for secret in secrets:
                value = value.replace(secret, "<redacted>")
            return value

        raise AgentTeamError(
            "TMUX_COMMAND_FAILED",
            f"tmux {redact(' '.join(args))} failed: {redact(result.stderr.strip())}",
        )
    return result


def has_session(run_id: str) -> bool:
    return (
        _run(
            "has-session",
            "-t",
            session_name(run_id),
            check=False,
            server=server_name(run_id),
        ).returncode
        == 0
    )


def list_windows(run_id: str) -> dict[str, dict[str, Any]]:
    if not has_session(run_id):
        return {}
    result = _run(
        "list-windows",
        "-t",
        session_name(run_id),
        "-F",
        "#{window_name}\t#{window_panes}\t#{pane_id}\t#{pane_pid}\t#{pane_dead}",
        server=server_name(run_id),
    )
    windows: dict[str, dict[str, Any]] = {}
    for line in result.stdout.splitlines():
        fields = line.split("\t")
        if len(fields) != 5:
            raise IntegrityError("tmux returned a malformed window identity")
        name, pane_count_raw, pane_id, pane_pid_raw, pane_dead_raw = fields
        if not name or not pane_id or name in windows:
            raise IntegrityError("tmux returned an ambiguous window identity")
        try:
            pane_count = int(pane_count_raw)
            pane_pid = int(pane_pid_raw)
        except ValueError as exc:
            raise IntegrityError("tmux returned invalid pane metadata") from exc
        if pane_count != 1:
            raise IntegrityError(
                f"tmux window {name!r} does not contain exactly one pane"
            )
        if pane_pid <= 0:
            raise IntegrityError("tmux returned a non-positive pane PID")
        if pane_dead_raw not in {"0", "1"}:
            raise IntegrityError("tmux returned an invalid pane lifecycle state")
        windows[name] = {
            "tmux_pane_id": pane_id,
            "pane_pid": pane_pid,
            "pane_dead": pane_dead_raw == "1",
        }
    return windows


def _worker_shell_command(run_dir: Path, role_id: str) -> str:
    argv = [
        sys.executable,
        "-m",
        "agent_team",
        "_worker",
        "--run-dir",
        str(run_dir),
        "--role",
        role_id,
    ]
    return "exec " + shlex.join(argv)


def _worker_environment_args(
    run_dir: Path,
    role: Role,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    from .adapters import get_adapter
    from .adapters.base import HarnessLaunchOptions

    adapter = get_adapter(role.adapter or "")
    names = adapter.worker_environment_names(
        run_dir=run_dir,
        role_id=role.role_id,
        options=HarnessLaunchOptions(
            model=role.model,
            reasoning_effort=role.reasoning_effort,
            fast_mode=role.fast_mode,
            model_provider=role.model_provider,
            model_provider_config=role.model_provider_config,
        ),
    )
    arguments: list[str] = []
    sensitive_values: list[str] = []
    for name in names:
        value = os.environ.get(name)
        if not value:
            raise AgentTeamError(
                "HARNESS_ENVIRONMENT_UNAVAILABLE",
                f"role {role.role_id} requires non-empty environment variable "
                f"{name!r} for its frozen provider",
            )
        arguments.extend(("-e", f"{name}={value}"))
        sensitive_values.append(value)
    return tuple(arguments), tuple(sensitive_values)


def ensure_workers(
    run_dir: Path,
    team: Team,
    *,
    role_ids: Iterable[str] | None = None,
) -> dict[str, Any]:
    all_roles = [
        role
        for role in sorted(team.roles.values(), key=lambda item: item.role_id)
        if role.binding == "external"
    ]
    requested = (
        {role.role_id for role in all_roles}
        if role_ids is None
        else set(role_ids)
    )
    known = {role.role_id for role in all_roles}
    unknown = sorted(requested - known)
    if unknown:
        raise AgentTeamError(
            "ROLE_NOT_EXTERNAL",
            f"cannot create Worker for non-External role(s): {', '.join(unknown)}",
        )
    roles = [role for role in all_roles if role.role_id in requested]
    if not all_roles or (not roles and not has_session(team.run_id)):
        return {"session": None, "created": [], "existing": []}
    tmux_executable()
    created: list[str] = []
    existing: list[str] = []
    name = session_name(team.run_id)

    def load_worker(role_id: str) -> dict[str, Any] | None:
        role_path = run_dir / "roles" / f"{role_id}.json"
        if not path_entry_exists(role_path):
            return None
        # Lazy import avoids the Worker -> tmux_runtime module cycle.
        from .worker import validate_role_snapshot

        return validate_role_snapshot(read_json(role_path), role_id)

    def assert_worker_not_live_without_window(role_id: str) -> None:
        worker = load_worker(role_id)
        if worker is None:
            return
        state = process_identity_state(
            worker["worker_pid"],
            worker["worker_start_id"],
        )
        if state == "match":
            raise AgentTeamError(
                "LIVE_WORKER_WITHOUT_TMUX",
                f"role {role_id} has a live Worker but no tmux window; "
                "refusing to create a duplicate",
            )
        if state not in {"gone", "reused"}:
            raise AgentTeamError(
                "PROCESS_IDENTITY_UNKNOWN",
                f"role {role_id} Worker identity is {state}; "
                "refusing to create a duplicate",
            )

    def record_created_worker(role_id: str) -> None:
        deadline = time.monotonic() + 2.0
        last_state = "missing"
        while time.monotonic() < deadline:
            window = list_windows(team.run_id).get(role_id)
            if window is None:
                last_state = "missing"
                time.sleep(0.02)
                continue
            if window["pane_dead"]:
                last_state = "dead"
                time.sleep(0.02)
                continue
            start_id = process_start_id(window["pane_pid"])
            if start_id is None:
                last_state = "identity-unknown"
                time.sleep(0.02)
                continue
            state = process_identity_state(window["pane_pid"], start_id)
            if state != "match":
                last_state = state
                time.sleep(0.02)
                continue
            value = {
                "schema_version": 1,
                "role_id": role_id,
                "worker_pid": window["pane_pid"],
                "worker_start_id": start_id,
                "tmux_session": name,
                "tmux_pane_id": window["tmux_pane_id"],
                "updated_at": rfc3339(),
            }
            from .worker import validate_role_snapshot

            validate_role_snapshot(value, role_id)
            atomic_json(run_dir / "roles" / f"{role_id}.json", value)
            return
        raise AgentTeamError(
            "WORKER_START_UNCONFIRMED",
            f"tmux Worker for role {role_id} could not be verified ({last_state})",
        )

    def validate_existing_worker(
        role_id: str,
        window: dict[str, Any],
    ) -> bool:
        worker = load_worker(role_id)
        if worker is None:
            raise AgentTeamError(
                "WORKER_IDENTITY_MISSING",
                f"tmux window for role {role_id} has no persisted Worker identity",
            )
        if (
            worker["tmux_session"] != name
            or worker["tmux_pane_id"] != window["tmux_pane_id"]
            or worker["worker_pid"] != window["pane_pid"]
        ):
            raise IntegrityError(
                f"tmux window identity conflicts with role {role_id} snapshot"
            )
        state = process_identity_state(
            worker["worker_pid"],
            worker["worker_start_id"],
        )
        if window["pane_dead"]:
            if state not in {"gone", "reused"}:
                raise AgentTeamError(
                    "PROCESS_IDENTITY_UNKNOWN",
                    f"dead tmux pane has Worker identity state {state} "
                    f"for role {role_id}",
                )
            return False
        if state != "match":
            raise AgentTeamError(
                "PROCESS_IDENTITY_UNKNOWN",
                f"live tmux pane has Worker identity state {state} "
                f"for role {role_id}",
            )
        return True

    newly_created: set[str] = set()
    if not has_session(team.run_id):
        for role in roles:
            assert_worker_not_live_without_window(role.role_id)
        first = roles[0]
        environment_args, sensitive_values = _worker_environment_args(
            run_dir,
            first,
        )
        _run(
            "new-session",
            "-d",
            "-s",
            name,
            "-n",
            first.role_id,
            *environment_args,
            _worker_shell_command(run_dir, first.role_id),
            server=server_name(team.run_id),
            sensitive_values=sensitive_values,
        )
        created.append(first.role_id)
        newly_created.add(first.role_id)
    windows = list_windows(team.run_id)
    unexpected = sorted(set(windows) - known)
    if unexpected:
        raise IntegrityError(
            f"tmux session contains unexpected windows: {unexpected}"
        )
    for role in roles:
        window = windows.get(role.role_id)
        if role.role_id in newly_created:
            record_created_worker(role.role_id)
            continue
        if window is not None:
            if validate_existing_worker(role.role_id, window):
                existing.append(role.role_id)
                continue
            environment_args, sensitive_values = _worker_environment_args(
                run_dir,
                role,
            )
            _run(
                "respawn-pane",
                "-k",
                "-t",
                window["tmux_pane_id"],
                *environment_args,
                _worker_shell_command(run_dir, role.role_id),
                server=server_name(team.run_id),
                sensitive_values=sensitive_values,
            )
            created.append(role.role_id)
            record_created_worker(role.role_id)
            continue
        assert_worker_not_live_without_window(role.role_id)
        environment_args, sensitive_values = _worker_environment_args(
            run_dir,
            role,
        )
        _run(
            "new-window",
            "-d",
            "-t",
            name,
            "-n",
            role.role_id,
            *environment_args,
            _worker_shell_command(run_dir, role.role_id),
            server=server_name(team.run_id),
            sensitive_values=sensitive_values,
        )
        created.append(role.role_id)
        record_created_worker(role.role_id)
    return {"session": name, "created": created, "existing": existing}


def signal_change(run_id: str, role_id: str) -> bool:
    try:
        result = _run(
            "wait-for",
            "-S",
            change_channel(run_id, role_id),
            check=False,
            server=server_name(run_id),
        )
    except (AgentTeamError, OSError):
        return False
    return result.returncode == 0


def capture_pane(run_id: str, role_id: str) -> str | None:
    if role_id not in list_windows(run_id):
        return None
    result = _run(
        "capture-pane",
        "-p",
        "-J",
        "-S",
        "-200",
        "-t",
        f"{session_name(run_id)}:{role_id}",
        check=False,
        server=server_name(run_id),
    )
    if result.returncode != 0:
        return None
    encoded = result.stdout.encode("utf-8", errors="replace")
    if len(encoded) > 65536:
        encoded = encoded[-65536:]
    text = encoded.decode("utf-8", errors="replace")
    return "".join(
        char
        if char in "\n\t" or (ord(char) >= 0x20 and not 0x7F <= ord(char) <= 0x9F)
        else f"\\x{ord(char):02x}"
        for char in text
    )


def attach(run_id: str, role_id: str | None = None) -> int:
    if not has_session(run_id):
        raise AgentTeamError("NO_TMUX_RUNTIME", "run has no tmux runtime")
    target = session_name(run_id)
    if role_id:
        target = f"{target}:{role_id}"
    return subprocess.call(
        [
            tmux_executable(),
            "-L",
            server_name(run_id),
            "attach-session",
            "-r",
            "-t",
            target,
        ]
    )
