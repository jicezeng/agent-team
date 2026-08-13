from __future__ import annotations

import argparse
import json
import os
import secrets
import shutil
import stat
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Sequence

from . import __version__
from .adapters import get_adapter
from .assets import (
    claude_plugin_source,
    codex_skill_source,
    installed_claude_plugin,
    installed_codex_skill,
)
from .bootstrap import initialize_run, parse_role_spec, start_run
from .config import (
    REQUIRED_AUDIT_PAYLOAD_SECTIONS,
    ObservabilityPolicy,
    generate_run_id,
    load_team,
    make_team,
    validate_role_id,
)
from .errors import (
    AgentTeamError,
    IntegrityError,
    InvalidArgument,
    ObservationInternalError,
    ObservationIOError,
    RunNotFound,
)
from .management import cancel_run, recover_run, unlock_workspace
from .observation import (
    corrupted_observation,
    derive_observation,
    diagnose,
    status_text,
)
from .origin import origin_action, origin_context, origin_resume, wait_origin
from .processes import current_identity
from .runner import run_harness_runner
from .state import (
    discover_workspace,
    ensure_state_directories,
    fixed_state_dir,
    get_run_dir,
    probe_filesystem,
    read_owner,
    runs_dir,
    state_paths,
    validate_git_boundaries,
    validate_state_root,
    workspace_lock,
)
from .supervisor import run_supervisor
from .tmux_runtime import attach as attach_tmux
from .trace import (
    build_transcript,
    flattened_trace_events,
    render_trace_event,
    render_transcript,
)
from .turns import load_runtime, stage_external_action_locked
from .util import (
    envelope,
    fsync_dir,
    path_entry_exists,
    read_regular,
    rfc3339,
)
from .worker import run_worker


KNOWN_COMMANDS = {
    "install",
    "doctor",
    "init",
    "start",
    "status",
    "watch",
    "diagnose",
    "transcript",
    "tail",
    "attach",
    "cancel",
    "recover",
    "unlock",
    "context",
    "handoff",
    "complete",
    "block",
    "wait-origin",
    "origin-context",
    "origin-handoff",
    "origin-complete",
    "origin-block",
    "origin-resume",
    "_worker",
    "_turn-supervisor",
    "_harness-runner",
}
OBSERVATION_COMMANDS = {"status", "diagnose", "watch", "transcript", "tail"}


def _json(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


def _workspace(value: str | None = None) -> Path:
    return discover_workspace(Path(value or "."))


def _run_dir(run_id: str, workspace: str | None = None) -> Path:
    return get_run_dir(_workspace(workspace), run_id)


def _role_value_options(
    values: Sequence[str] | None,
    *,
    option: str,
) -> dict[str, str]:
    parsed: dict[str, str] = {}
    for spec in values or ():
        if "=" not in spec:
            raise InvalidArgument(f"{option} must be ROLE=VALUE: {spec!r}")
        role_id, value = spec.split("=", 1)
        validate_role_id(role_id)
        if not value:
            raise InvalidArgument(f"{option} value must not be empty for {role_id}")
        if role_id in parsed:
            raise InvalidArgument(f"duplicate {option} for role: {role_id}")
        parsed[role_id] = value
    return parsed


def _role_flags(values: Sequence[str] | None, *, option: str) -> set[str]:
    parsed: set[str] = set()
    for role_id in values or ():
        validate_role_id(role_id)
        if role_id in parsed:
            raise InvalidArgument(f"duplicate {option} for role: {role_id}")
        parsed.add(role_id)
    return parsed


def _observation_run_dir(
    run_id: str | None,
    workspace_value: str | None,
) -> tuple[Path, str | None]:
    """Resolve an observation target without creating locks or state."""
    workspace = _workspace(workspace_value)
    try:
        with workspace_lock(workspace, exclusive=False):
            try:
                owner = read_owner(workspace)
            except IntegrityError as exc:
                if run_id is None:
                    raise ObservationIOError(
                        f"cannot resolve the active run from workspace ownership: {exc.message}"
                    ) from exc
                owner = None
            selected = run_id
            if selected is None:
                if owner is None:
                    raise RunNotFound(
                        "workspace has no active owner; provide an explicit run id"
                    )
                selected = owner["run_id"]
            candidate = runs_dir(workspace) / selected
            if not candidate.exists():
                if owner is not None and owner["run_id"] == selected:
                    return (
                        candidate,
                        "workspace owner references a missing run directory",
                    )
                raise RunNotFound(f"run {selected!r} not found in {workspace}")
            if candidate.is_symlink() or not candidate.is_dir():
                return candidate, "run directory is not a non-symlink directory"
            return candidate, None
    except (RunNotFound, ObservationIOError):
        raise
    except (IntegrityError, OSError) as exc:
        if run_id is not None:
            candidate = runs_dir(workspace) / run_id
            if candidate.exists():
                return (
                    candidate,
                    f"workspace operation lock is missing or invalid: {exc}",
                )
        raise ObservationIOError(
            f"unable to resolve observation target without creating state: {exc}"
        ) from exc


def _env_turn() -> tuple[Path, dict[str, Any]]:
    raw_run_dir = os.environ.get("AGENT_TEAM_RUN_DIR")
    turn_id = os.environ.get("AGENT_TEAM_TURN_ID")
    role_id = os.environ.get("AGENT_TEAM_ROLE_ID")
    run_id = os.environ.get("AGENT_TEAM_RUN_ID")
    if not all((raw_run_dir, turn_id, role_id, run_id)):
        raise AgentTeamError(
            "NOT_IN_AGENT_TEAM_TURN",
            "AGENT_TEAM_RUN_ID/ROLE_ID/TURN_ID/RUN_DIR are required",
        )
    run_dir = Path(raw_run_dir).resolve(strict=True)
    from .state import locked_run

    with locked_run(run_dir, exclusive=False):
        team = load_team(run_dir)
        runtime = load_runtime(run_dir / "turns" / turn_id, team=team)
        if runtime["role_id"] != role_id or run_dir.name != run_id:
            raise IntegrityError("worker environment does not match turn runtime")
    return run_dir, runtime


def _observe(
    run_dir: Path,
    *,
    preexisting_corruption: str | None = None,
) -> dict[str, Any]:
    if preexisting_corruption:
        return corrupted_observation(
            run_dir.name,
            preexisting_corruption,
            run_dir=run_dir,
        )
    try:
        return derive_observation(run_dir)
    except IntegrityError as exc:
        return corrupted_observation(
            run_dir.name,
            str(exc),
            run_dir=run_dir,
            evidence_paths=exc.evidence_paths,
        )
    except OSError as exc:
        raise ObservationIOError(f"unable to read run observation: {exc}") from exc


class _ArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        self.print_usage(sys.stderr)
        raise InvalidArgument(message)


def _doctor(workspace_value: str | None) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []

    def add(name: str, ok: bool | None, details: Any) -> None:
        checks.append(
            {
                "check": name,
                "status": "pass" if ok else ("unknown" if ok is None else "fail"),
                "details": details,
            }
        )

    def permission_report(path: Path, *, recursive: bool) -> dict[str, Any]:
        if not path_entry_exists(path):
            return {"path": str(path), "exists": False, "private": None}
        candidates = [path]
        if recursive and path.is_dir() and not path.is_symlink():
            candidates.extend(path.rglob("*"))
        unsafe: list[str] = []
        invalid: list[str] = []
        for candidate in candidates:
            try:
                info = candidate.lstat()
            except OSError:
                invalid.append(str(candidate))
                continue
            if stat.S_ISLNK(info.st_mode) or not (
                stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)
            ):
                invalid.append(str(candidate))
                continue
            if stat.S_IMODE(info.st_mode) & 0o077:
                unsafe.append(str(candidate))
        return {
            "path": str(path),
            "exists": True,
            "mode": oct(stat.S_IMODE(path.lstat().st_mode)),
            "private": not unsafe and not invalid,
            "unsafe_paths": unsafe[:20],
            "unsafe_count": len(unsafe),
            "invalid_paths": invalid[:20],
            "invalid_count": len(invalid),
        }

    def integration_report(source: Path, target: Path) -> tuple[bool, dict[str, Any]]:
        details: dict[str, Any] = {
            "source": str(source),
            "target": str(target),
            "installed": path_entry_exists(target),
            "matches_bundled": False,
        }
        if (
            not path_entry_exists(target)
            or target.is_symlink()
            or not target.is_dir()
        ):
            return False, details

        def manifest(root: Path) -> dict[str, bytes]:
            result: dict[str, bytes] = {}
            for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
                if path.is_symlink():
                    raise IntegrityError(
                        f"integration contains a symbolic link: {path}"
                    )
                if path.is_file():
                    result[path.relative_to(root).as_posix()] = read_regular(path)
                elif not path.is_dir():
                    raise IntegrityError(
                        f"integration contains an unsupported entry: {path}"
                    )
            return result

        try:
            source_manifest = manifest(source)
            target_manifest = manifest(target)
        except (IntegrityError, OSError) as exc:
            details["error"] = str(exc)
            return False, details
        details["files"] = sorted(target_manifest)
        details["matches_bundled"] = target_manifest == source_manifest
        return bool(details["matches_bundled"]), details

    add(
        "python",
        sys.version_info >= (3, 11),
        {"version": sys.version.split()[0], "executable": sys.executable},
    )
    for command in ("git", "tmux", "codex", "claude"):
        located = shutil.which(command)
        add(command, located is not None, {"executable": located})
    for adapter_id in ("codex", "claude-code"):
        try:
            report = get_adapter(adapter_id).probe()
        except Exception as exc:
            add(f"adapter:{adapter_id}", False, {"error": str(exc)})
            add(
                f"authentication:{adapter_id}",
                None,
                {"available": False, "reason": "adapter probe failed"},
            )
        else:
            add(
                f"adapter:{adapter_id}",
                report.launcher_stays_in_process_group,
                report.to_json(),
            )
            add(
                f"authentication:{adapter_id}",
                report.authenticated,
                {"authenticated": report.authenticated},
            )
            adapter = get_adapter(adapter_id)
            for launch_mode in ("headless", "interactive"):
                mappings = adapter.profile_mappings(launch_mode)
                for profile, mapping in sorted(mappings.items()):
                    profile_ok = bool(mapping.get("start")) and bool(
                        mapping.get("resume")
                    )
                    if profile_ok:
                        profile_ok = mapping["start"] == mapping["resume"]
                    check_name = f"launch_profile:{adapter_id}:{profile}"
                    if launch_mode == "interactive":
                        check_name += ":interactive"
                    add(
                        check_name,
                        profile_ok,
                        {
                            "launch_mode": launch_mode,
                            "start": mapping.get("start"),
                            "resume": mapping.get("resume"),
                            "equivalent_permissions": profile_ok,
                        },
                    )
            try:
                command = (
                    [report.executable, "exec", "resume", "--help"]
                    if adapter_id == "codex"
                    else [report.executable, "--help"]
                )
                resume_help = subprocess.run(
                    command,
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=10,
                )
                help_text = resume_help.stdout + resume_help.stderr
                resume_ok = resume_help.returncode == 0 and (
                    "Resume a previous session" in help_text
                    if adapter_id == "codex"
                    else "--resume" in help_text
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                add(
                    f"session_resume:{adapter_id}",
                    False,
                    {"error": str(exc)},
                )
            else:
                add(
                    f"session_resume:{adapter_id}",
                    resume_ok,
                    {"command": command, "returncode": resume_help.returncode},
                )
    try:
        identity = current_identity()
    except Exception as exc:
        add("process_start_id", False, {"error": str(exc)})
    else:
        add(
            "process_start_id",
            bool(identity.start_id),
            {"pid": identity.pid, "start_id": identity.start_id},
        )

    try:
        state_dir = ensure_state_directories()
    except (AgentTeamError, OSError) as exc:
        state_dir = fixed_state_dir()
        add("fixed_state_dir", False, {"path": str(state_dir), "error": str(exc)})
    else:
        state_permissions = permission_report(state_dir, recursive=True)
        add(
            "fixed_state_dir",
            bool(state_permissions["private"]),
            state_permissions,
        )
    try:
        state_filesystem = probe_filesystem(state_dir)
    except (AgentTeamError, OSError) as exc:
        add(
            "filesystem:fixed_state", False, {"path": str(state_dir), "error": str(exc)}
        )
    else:
        add(
            "filesystem:fixed_state",
            all(state_filesystem.values()),
            {"path": str(state_dir), **state_filesystem},
        )

    codex_target = installed_codex_skill()
    codex_ok, codex_details = integration_report(
        codex_skill_source(),
        codex_target,
    )
    add("integration:codex_skill", codex_ok, codex_details)
    plugin_ok, plugin_details = integration_report(
        claude_plugin_source(),
        installed_claude_plugin(),
    )
    add("integration:claude_plugin", plugin_ok, plugin_details)

    workspace: Path | None = None
    try:
        workspace = _workspace(workspace_value)
        validate_git_boundaries(workspace)
    except AgentTeamError as exc:
        add("workspace", False, {"error": str(exc)})
    else:
        workspace_access = {
            "readable": os.access(workspace, os.R_OK),
            "writable": os.access(workspace, os.W_OK),
            "searchable": os.access(workspace, os.X_OK),
        }
        add(
            "workspace",
            all(workspace_access.values()),
            {
                "path": str(workspace),
                "mode": oct(stat.S_IMODE(workspace.lstat().st_mode)),
                **workspace_access,
            },
        )
        tracked = subprocess.run(
            ["git", "-C", str(workspace), "ls-files", "-z", "--", ".agent-team"],
            capture_output=True,
            check=False,
        )
        add(
            "workspace_state_not_tracked",
            tracked.returncode == 0 and not tracked.stdout,
            {
                "tracked_paths": [
                    os.fsdecode(item) for item in tracked.stdout.split(b"\0") if item
                ],
                "returncode": tracked.returncode,
            },
        )
        ignored = subprocess.run(
            [
                "git",
                "-C",
                str(workspace),
                "check-ignore",
                "-q",
                ".agent-team/",
            ],
            capture_output=True,
            check=False,
        )
        add(
            "workspace_state_ignore",
            True,
            {
                "ignored": ignored.returncode == 0,
                "warning": (
                    None
                    if ignored.returncode == 0
                    else (
                        ".agent-team/ is not ignored; never stage it and consider "
                        "adding a user-managed ignore rule"
                    )
                ),
            },
        )
        root_dir = workspace / ".agent-team"
        root_permissions = permission_report(root_dir, recursive=True)
        add(
            "workspace_state_permissions",
            (
                True
                if not root_permissions["exists"]
                else bool(root_permissions["private"])
            ),
            root_permissions,
        )
        try:
            workspace_filesystem = probe_filesystem(workspace)
        except (AgentTeamError, OSError) as exc:
            add(
                "filesystem:workspace",
                False,
                {"path": str(workspace), "error": str(exc)},
            )
        else:
            add(
                "filesystem:workspace",
                all(workspace_filesystem.values()),
                {"path": str(workspace), **workspace_filesystem},
            )

        _, workspace_lock_path, owner_path = state_paths(workspace)
        root_marker = root_dir / "root.json"
        state_details: dict[str, Any] = {
            "state_root": str(root_dir),
            "root_marker": str(root_marker),
            "workspace_lock": str(workspace_lock_path),
            "fixed_state_dir": str(state_dir),
            "initialized": path_entry_exists(root_dir),
        }
        owner: dict[str, Any] | None = None
        state_ok = True
        if path_entry_exists(root_dir):
            if not path_entry_exists(workspace_lock_path):
                state_ok = False
                state_details["error"] = "workspace operation lock is missing"
            else:
                try:
                    with workspace_lock(workspace, exclusive=False):
                        state_details["root"] = validate_state_root(workspace)
                        owner = read_owner(workspace)
                except (AgentTeamError, OSError) as exc:
                    state_ok = False
                    state_details["error"] = str(exc)
        elif path_entry_exists(owner_path):
            state_ok = False
            state_details["error"] = (
                "fixed owner exists while the Workspace State Root is absent"
            )
        add("state_root_consistency", state_ok, state_details)
        owner_details: dict[str, Any] = {
            "owner_path": str(owner_path),
            "owner": owner,
            "workspace_available": owner is None,
        }
        add("workspace_owner", state_ok, owner_details)

        if owner is not None:
            run_dir = runs_dir(workspace) / owner["run_id"]
            profile_details: dict[str, Any] = {
                "run_id": owner["run_id"],
                "run_dir": str(run_dir),
                "roles": [],
            }
            profiles_ok = path_entry_exists(run_dir)
            if profiles_ok:
                try:
                    team = load_team(run_dir)
                    for role in sorted(
                        team.roles.values(),
                        key=lambda item: item.role_id,
                    ):
                        if role.binding != "external":
                            continue
                        adapter = get_adapter(role.adapter or "")
                        adapter.assert_profile(
                            role.launch_profile or "",
                            role.session_policy or "",
                            role.launch_profile_sha256 or "",
                            role.launch_mode or "headless",
                        )
                        profile_details["roles"].append(
                            {
                                "role_id": role.role_id,
                                "adapter": role.adapter,
                                "session_policy": role.session_policy,
                                "launch_profile": role.launch_profile,
                                "launch_profile_sha256": (role.launch_profile_sha256),
                                "launch_mode": role.launch_mode,
                                "model": role.model,
                                "reasoning_effort": role.reasoning_effort,
                                "fast_mode": role.fast_mode,
                                "valid": True,
                            }
                        )
                except (AgentTeamError, OSError) as exc:
                    profiles_ok = False
                    profile_details["error"] = str(exc)
            add("active_run_launch_profiles", profiles_ok, profile_details)
    return {
        "agent_team_version": __version__,
        "state_dir": str(state_dir),
        "checks": checks,
        "healthy": all(item["status"] == "pass" for item in checks),
    }


def _install_skill() -> dict[str, Any]:
    def install_tree(source: Path, target: Path) -> None:
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if target.is_symlink() or (target.exists() and not target.is_dir()):
            raise InvalidArgument(
                f"integration target must be a non-symlink directory: {target}"
            )
        suffix = f"{os.getpid()}-{secrets.token_hex(6)}"
        temporary = target.parent / f".tmp-{target.name}-{suffix}"
        backup = target.parent / f".old-{target.name}-{suffix}"
        try:
            shutil.copytree(source, temporary)
            for path in temporary.rglob("*"):
                if path.is_symlink():
                    raise IntegrityError(
                        f"bundled integration contains a symbolic link: {path}"
                    )
                path.chmod(0o700 if path.is_dir() else 0o600)
            temporary.chmod(0o700)
            moved_old = False
            if target.exists():
                os.rename(target, backup)
                moved_old = True
            try:
                os.rename(temporary, target)
                fsync_dir(target.parent)
            except BaseException:
                if moved_old and not target.exists() and backup.exists():
                    os.rename(backup, target)
                raise
            if backup.exists():
                shutil.rmtree(backup)
                fsync_dir(target.parent)
        finally:
            if temporary.exists():
                shutil.rmtree(temporary)

    codex_source = codex_skill_source()
    codex_target = installed_codex_skill()
    install_tree(codex_source, codex_target)
    claude_source = claude_plugin_source()
    claude_target = installed_claude_plugin()
    install_tree(claude_source, claude_target)
    return {
        "code": "INTEGRATIONS_INSTALLED",
        "codex": {
            "source": str(codex_source),
            "target": str(codex_target),
        },
        "claude_code": {
            "source": str(claude_source),
            "target": str(claude_target),
        },
    }


def build_parser() -> argparse.ArgumentParser:
    parser = _ArgumentParser(prog="agent-team")
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("install")

    doctor = sub.add_parser("doctor")
    doctor.add_argument("--workspace")
    doctor.add_argument("--json", action="store_true")

    init = sub.add_parser("init")
    init.add_argument("--workspace", default=".")
    init.add_argument("--request", type=Path, required=True)
    init.add_argument("--protocol", type=Path, required=True)
    init.add_argument("--role", action="append", required=True)
    init.add_argument(
        "--role-model",
        action="append",
        metavar="ROLE=MODEL",
        help="override one External role's model; otherwise inherit its user default",
    )
    init.add_argument(
        "--role-reasoning-effort",
        action="append",
        metavar="ROLE=EFFORT",
        help=(
            "override one External role's reasoning effort; otherwise inherit "
            "its user default"
        ),
    )
    init.add_argument(
        "--role-fast",
        action="append",
        metavar="ROLE",
        help="enable Codex fast mode for one External role",
    )
    init.add_argument(
        "--role-launch-mode",
        action="append",
        metavar="ROLE=MODE",
        help=(
            "select interactive (default) or headless execution for one "
            "External role"
        ),
    )
    init.add_argument("--initial-role", required=True)
    init.add_argument("--origin-harness", default="codex")
    init.add_argument("--max-turns", type=int, default=20)
    init.add_argument("--max-wall-time-seconds", type=int, default=7200)
    init.add_argument(
        "--audit-mode",
        choices=("standard", "full"),
        default="standard",
    )
    init.add_argument(
        "--trace-redaction",
        choices=("standard", "none"),
        default="standard",
    )
    init.add_argument("--max-trace-bytes", type=int, default=64 * 1024 * 1024)
    init.add_argument(
        "--raw-retention",
        choices=("redacted", "keep", "delete"),
        default="redacted",
    )
    init.add_argument("--require-rationale-evidence", action="store_true")
    init.add_argument("--run-id")

    start = sub.add_parser("start")
    start.add_argument("run_id")
    start.add_argument("--workspace")
    start.add_argument(
        "--confirm-full-access",
        action="store_true",
        help=(
            "confirm once that this new Run's full-access External roles may "
            "use the host filesystem and network without per-command approvals"
        ),
    )

    for name in ("status", "watch", "diagnose"):
        command = sub.add_parser(name)
        command.add_argument("run_id", nargs="?")
        command.add_argument("--workspace")
        if name == "status":
            command.add_argument("--json", action="store_true")
        elif name == "watch":
            command.add_argument("--jsonl", action="store_true")
        else:
            command.add_argument("--role")
            command.add_argument("--json", action="store_true")

    transcript = sub.add_parser("transcript")
    transcript.add_argument("run_id", nargs="?")
    transcript.add_argument("--workspace")
    transcript.add_argument("--role")
    transcript.add_argument("--turn")
    transcript.add_argument("--json", action="store_true")

    tail = sub.add_parser("tail")
    tail.add_argument("run_id", nargs="?")
    tail.add_argument("--workspace")
    tail.add_argument("--role")
    tail.add_argument("--turn")
    tail.add_argument("--lines", type=int, default=50)
    tail.add_argument("--follow", action="store_true")
    tail.add_argument("--jsonl", action="store_true")

    attach = sub.add_parser("attach")
    attach.add_argument("run_id")
    attach.add_argument("--workspace")
    attach.add_argument("--role")

    cancel = sub.add_parser("cancel")
    cancel.add_argument("run_id")
    cancel.add_argument("--workspace")

    recover = sub.add_parser("recover")
    recover.add_argument("run_id")
    recover.add_argument("--workspace")

    unlock = sub.add_parser("unlock")
    unlock.add_argument("--workspace", required=True)
    unlock.add_argument("--expect-run", required=True)
    unlock.add_argument("--confirm-origin-stopped", action="store_true")

    sub.add_parser("context")
    for name in ("handoff", "complete", "block"):
        action = sub.add_parser(name)
        if name == "handoff":
            action.add_argument("--to", required=True)
        action.add_argument("--file", type=Path, required=True)

    wait = sub.add_parser("wait-origin")
    wait.add_argument("--run", required=True)
    wait.add_argument("--workspace")
    wait.add_argument("--timeout", type=float, default=90)
    wait.add_argument("--claim")

    origin_context = sub.add_parser("origin-context")
    origin_context.add_argument("--run", required=True)
    origin_context.add_argument("--workspace")
    origin_context.add_argument("--event", required=True)
    origin_context.add_argument("--claim")

    for name in ("origin-handoff", "origin-complete", "origin-block"):
        action = sub.add_parser(name)
        action.add_argument("--run", required=True)
        action.add_argument("--workspace")
        action.add_argument("--turn", required=True)
        action.add_argument("--claim", required=True)
        action.add_argument("--from-role", required=True)
        if name == "origin-handoff":
            action.add_argument("--to", required=True)
            action.add_argument("--wait-timeout", type=float, default=90)
        action.add_argument("--file", type=Path, required=True)

    resume = sub.add_parser("origin-resume")
    resume.add_argument("--run", required=True)
    resume.add_argument("--workspace")
    resume.add_argument("--claim", required=True)
    resume.add_argument("--to", required=True)
    resume.add_argument("--file", type=Path, required=True)
    resume.add_argument("--wait-timeout", type=float, default=90)

    worker = sub.add_parser("_worker")
    worker.add_argument("--run-dir", type=Path, required=True)
    worker.add_argument("--role", required=True)

    supervisor = sub.add_parser("_turn-supervisor")
    supervisor.add_argument("--run-dir", type=Path, required=True)
    supervisor.add_argument("--turn", required=True)
    supervisor.add_argument("--nonce", required=True)
    supervisor.add_argument("--launch-sha256", required=True)

    runner = sub.add_parser("_harness-runner")
    runner.add_argument("--run-dir", type=Path, required=True)
    runner.add_argument("--turn", required=True)
    runner.add_argument("--nonce", required=True)
    runner.add_argument("--launch-sha256", required=True)
    runner.add_argument("--supervisor-pid", type=int, required=True)
    runner.add_argument("--supervisor-start-id", required=True)
    runner.add_argument("--status-fd", type=int, required=True)
    return parser


def dispatch(args: argparse.Namespace) -> int:
    command = args.command
    if command == "install":
        _json(_install_skill())
        return 0
    if command == "doctor":
        data = _doctor(args.workspace)
        if args.json:
            _json(envelope("doctor", data=data))
        else:
            print(f"Agent-Team {data['agent_team_version']}")
            for check in data["checks"]:
                print(f"{check['status']:<7} {check['check']}")
        return 0
    if command == "init":
        workspace = _workspace(args.workspace)
        role_models = _role_value_options(
            args.role_model,
            option="--role-model",
        )
        role_efforts = _role_value_options(
            args.role_reasoning_effort,
            option="--role-reasoning-effort",
        )
        fast_roles = _role_flags(args.role_fast, option="--role-fast")
        role_launch_modes = _role_value_options(
            args.role_launch_mode,
            option="--role-launch-mode",
        )
        roles = {}
        for spec in args.role:
            candidate = spec.split("=", 1)[0] if "=" in spec else ""
            role_id, role = parse_role_spec(
                spec,
                model=role_models.get(candidate),
                reasoning_effort=role_efforts.get(candidate),
                fast_mode=True if candidate in fast_roles else None,
                launch_mode=role_launch_modes.get(candidate),
            )
            if role_id in roles:
                raise InvalidArgument(f"duplicate role: {role_id}")
            roles[role_id] = role
        unknown_options = (
            set(role_models)
            | set(role_efforts)
            | fast_roles
            | set(role_launch_modes)
        ) - set(roles)
        if unknown_options:
            raise InvalidArgument(
                "role launch options reference unknown roles: "
                + ", ".join(sorted(unknown_options))
            )
        run_id = args.run_id or generate_run_id()
        required_sections = (
            REQUIRED_AUDIT_PAYLOAD_SECTIONS
            if args.audit_mode == "full" or args.require_rationale_evidence
            else ()
        )
        observability = ObservabilityPolicy(
            audit_mode=args.audit_mode,
            redaction=args.trace_redaction,
            max_trace_bytes=args.max_trace_bytes,
            raw_retention=args.raw_retention,
            required_payload_sections=required_sections,
        )
        team = make_team(
            run_id=run_id,
            workspace=workspace,
            origin_harness=args.origin_harness,
            roles=roles,
            initial_role=args.initial_role,
            max_turns=args.max_turns,
            max_wall_time_seconds=args.max_wall_time_seconds,
            observability=observability,
        )
        run_dir = initialize_run(
            team=team,
            request_path=args.request,
            protocol_path=args.protocol,
        )
        _json(
            {
                "code": "RUN_INITIALIZED",
                "run_id": run_id,
                "run_dir": str(run_dir),
                "status": "UNSTARTED",
            }
        )
        return 0
    if command == "start":
        _json(
            start_run(
                _run_dir(args.run_id, args.workspace),
                confirm_full_access=args.confirm_full_access,
            )
        )
        return 0
    if command == "status":
        run_dir, corruption = _observation_run_dir(args.run_id, args.workspace)
        data = _observe(run_dir, preexisting_corruption=corruption)
        observed_at = data.pop("_observed_at", rfc3339())
        if args.json:
            _json(envelope("status", data=data, observed_at=observed_at))
        else:
            print(status_text(data))
        return 0
    if command == "diagnose":
        run_dir, corruption = _observation_run_dir(args.run_id, args.workspace)
        data = diagnose(
            run_dir,
            role_id=args.role,
            preexisting_failure=corruption,
        )
        observed_at = data.pop("_observed_at", rfc3339())
        if args.json:
            _json(envelope("diagnose", data=data, observed_at=observed_at))
        else:
            print(status_text(data["observation"]))
            print("\nChecks:")
            for check in data["checks"]:
                print(f"  {check['status']:<14} {check['check']}: {check['summary']}")
        return 0
    if command == "watch":
        run_dir, initial_corruption = _observation_run_dir(
            args.run_id,
            args.workspace,
        )
        sequence = 0
        try:
            while True:
                sequence += 1
                data = _observe(
                    run_dir,
                    preexisting_corruption=initial_corruption,
                )
                observed_at = data.pop("_observed_at", rfc3339())
                if args.jsonl:
                    item = envelope("watch", data=data, observed_at=observed_at)
                    item["watch_seq"] = sequence
                    _json(item)
                else:
                    if sequence > 1:
                        print("\033[2J\033[H", end="")
                    print(status_text(data), flush=True)
                if (
                    data["run_status"] in {"COMPLETED", "CANCELLED"}
                    and data["recommended_action"] != "WAIT"
                ) or data["health"] == "corrupted":
                    return 0
                time.sleep(2)
        except KeyboardInterrupt:
            return 130
    if command == "attach":
        run_dir = _run_dir(args.run_id, args.workspace)
        team = load_team(run_dir)
        external_roles = {
            role.role_id
            for role in team.roles.values()
            if role.binding == "external"
        }
        if not external_roles:
            raise AgentTeamError(
                "NO_TMUX_RUNTIME",
                "pure Origin run has no tmux runtime",
            )
        if args.role is not None and args.role not in external_roles:
            raise AgentTeamError(
                "ROLE_NOT_EXTERNAL",
                f"{args.role!r} is not an External role in this run",
            )
        return attach_tmux(args.run_id, args.role)
    if command == "transcript":
        run_dir, corruption = _observation_run_dir(args.run_id, args.workspace)
        if corruption is not None:
            raise IntegrityError(corruption)
        data = build_transcript(
            run_dir,
            role_id=args.role,
            turn_id=args.turn,
        )
        if args.json:
            _json(envelope("transcript", data=data))
        else:
            print(render_transcript(data))
        return 0
    if command == "tail":
        if args.lines < 1:
            raise InvalidArgument("--lines must be a positive integer")
        run_dir, corruption = _observation_run_dir(args.run_id, args.workspace)
        if corruption is not None:
            raise IntegrityError(corruption)
        seen: set[tuple[str, int]] = set()
        first = True
        while True:
            transcript_data = build_transcript(
                run_dir,
                role_id=args.role,
                turn_id=args.turn,
            )
            events = flattened_trace_events(transcript_data)
            if first and not args.follow:
                events = events[-args.lines :]
            elif first:
                events = events[-args.lines :]
            else:
                events = [
                    event
                    for event in events
                    if (event["turn_id"], event["trace_seq"]) not in seen
                ]
            for event in events:
                seen.add((event["turn_id"], event["trace_seq"]))
                if args.jsonl:
                    _json(event)
                else:
                    print(render_trace_event(event), flush=True)
            if not args.follow:
                return 0
            first = False
            observation = derive_observation(run_dir)
            if observation["run_status"] in {"COMPLETED", "CANCELLED"}:
                return 0
            time.sleep(0.5)
    if command == "cancel":
        _json(cancel_run(_run_dir(args.run_id, args.workspace)))
        return 0
    if command == "recover":
        _json(recover_run(_run_dir(args.run_id, args.workspace)))
        return 0
    if command == "unlock":
        workspace = _workspace(args.workspace)
        _json(
            unlock_workspace(
                workspace,
                expect_run=args.expect_run,
                confirm_origin_stopped=args.confirm_origin_stopped,
            )
        )
        return 0
    if command == "context":
        run_dir, runtime = _env_turn()
        _json(
            {
                "run_id": run_dir.name,
                "run_dir": str(run_dir),
                "turn_id": runtime["turn_id"],
                "role_id": runtime["role_id"],
                "input_path": str(run_dir / "turns" / runtime["turn_id"] / "input.md"),
                "request_path": str(run_dir / "REQUEST.md"),
                "protocol_path": str(run_dir / "PROTOCOL.md"),
            }
        )
        return 0
    if command in {"handoff", "complete", "block"}:
        run_dir, runtime = _env_turn()
        from .state import locked_run

        with locked_run(run_dir, exclusive=True):
            team = load_team(run_dir)
            runtime = load_runtime(
                run_dir / "turns" / runtime["turn_id"],
                team=team,
            )
            result = stage_external_action_locked(
                run_dir,
                runtime=runtime,
                action=command,
                source_file=args.file,
                to_role=getattr(args, "to", None),
            )
        _json(result)
        return 0
    if command == "wait-origin":
        _json(
            wait_origin(
                _run_dir(args.run, args.workspace),
                timeout=args.timeout,
                claim=args.claim,
            )
        )
        return 0
    if command == "origin-context":
        run_dir = _run_dir(args.run, args.workspace)
        _json(
            origin_context(
                run_dir,
                event_id=args.event,
                claim=args.claim,
            )
        )
        return 0
    if command in {"origin-handoff", "origin-complete", "origin-block"}:
        action = command.removeprefix("origin-")
        _json(
            origin_action(
                _run_dir(args.run, args.workspace),
                action=action,
                turn_id=args.turn,
                claim=args.claim,
                from_role=args.from_role,
                source_file=args.file,
                to_role=getattr(args, "to", None),
                wait_timeout=getattr(args, "wait_timeout", 90),
            )
        )
        return 0
    if command == "origin-resume":
        _json(
            origin_resume(
                _run_dir(args.run, args.workspace),
                claim=args.claim,
                to_role=args.to,
                source_file=args.file,
                wait_timeout=args.wait_timeout,
            )
        )
        return 0
    if command == "_worker":
        return run_worker(args.run_dir.resolve(strict=True), args.role)
    if command == "_turn-supervisor":
        return run_supervisor(
            args.run_dir.resolve(strict=True),
            args.turn,
            args.nonce,
            args.launch_sha256,
        )
    if command == "_harness-runner":
        return run_harness_runner(
            run_dir=args.run_dir.resolve(strict=True),
            turn_id=args.turn,
            nonce=args.nonce,
            launch_sha256=args.launch_sha256,
            supervisor_pid=args.supervisor_pid,
            supervisor_start_id=args.supervisor_start_id,
            status_fd=args.status_fd,
        )
    raise InvalidArgument(f"unknown command: {command}")


def main(argv: Sequence[str] | None = None) -> None:
    parser = build_parser()
    raw = list(argv) if argv is not None else sys.argv[1:]
    structured = "--json" in raw or "--jsonl" in raw
    try:
        args = parser.parse_args(raw)
        code = dispatch(args)
    except AgentTeamError as exc:
        command = raw[0] if raw and raw[0] in KNOWN_COMMANDS else "agent-team"
        if structured:
            _json(envelope(command, error=exc))
        else:
            _json(
                {
                    "result": "error",
                    "error": {
                        "code": exc.code,
                        "message": exc.message,
                        "evidence_paths": list(exc.evidence_paths),
                    },
                }
            )
        code = exc.exit_code
    except OSError as exc:
        command = raw[0] if raw and raw[0] in OBSERVATION_COMMANDS else None
        if command is None:
            raise
        error = ObservationIOError(f"unable to read observation data: {exc}")
        if structured:
            _json(envelope(command, error=error))
        else:
            _json(
                {
                    "result": "error",
                    "error": {
                        "code": error.code,
                        "message": error.message,
                        "evidence_paths": [],
                    },
                }
            )
        code = error.exit_code
    except Exception as exc:
        command = raw[0] if raw and raw[0] in OBSERVATION_COMMANDS else None
        if command is None:
            raise
        error = ObservationInternalError(f"{type(exc).__name__}: {exc}")
        if structured:
            _json(envelope(command, error=error))
        else:
            _json(
                {
                    "result": "error",
                    "error": {
                        "code": error.code,
                        "message": error.message,
                        "evidence_paths": [],
                    },
                }
            )
        code = error.exit_code
    except KeyboardInterrupt:
        code = 130
    raise SystemExit(code)


if __name__ == "__main__":
    main()
