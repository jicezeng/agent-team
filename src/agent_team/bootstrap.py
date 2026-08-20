from __future__ import annotations

import os
import shutil
import stat
from pathlib import Path
from typing import Any

from .adapters import get_adapter
from .adapters.base import HarnessLaunchOptions
from .config import (
    DEFAULT_EXTERNAL_LAUNCH_PROFILE,
    EXTERNAL_ADAPTER_IDS,
    Role,
    Team,
    load_team,
)
from .errors import (
    AgentTeamError,
    FullAccessConfirmationRequired,
    IntegrityError,
    InvalidArgument,
)
from .gitfacts import capture_workspace_facts
from .journal import commit_event, scan_journal
from .processes import current_identity
from .state import (
    acquire_owner,
    assert_filesystem_capabilities,
    fixed_state_dir,
    initialize_state_root,
    locked_run,
    read_owner,
    release_owner,
    runs_dir,
    validate_git_boundaries,
    validate_state_root,
    workspace_lock,
)
from .tmux_runtime import ensure_workers, signal_change, tmux_executable
from .util import (
    atomic_write,
    create_empty_regular,
    ensure_dir,
    fsync_dir,
    path_entry_exists,
    read_regular,
    rfc3339,
    safe_relative,
    sha256_bytes,
)


def _assert_external_capability(role: Role) -> None:
    adapter = get_adapter(role.adapter or "")
    report = adapter.probe()
    if report.authenticated is False:
        raise AgentTeamError(
            "HARNESS_NOT_AUTHENTICATED",
            f"{role.adapter} is not authenticated",
        )
    if not report.launcher_stays_in_process_group:
        raise AgentTeamError(
            "HARNESS_PROCESS_MODEL_UNSUPPORTED",
            f"{role.adapter} launcher cannot be proven to stay in the managed "
            "Runner process group",
        )
    adapter.assert_profile(
        role.launch_profile or "",
        role.session_policy or "",
        role.launch_profile_sha256 or "",
        role.launch_mode or "headless",
    )
    adapter.assert_launch_options(
        HarnessLaunchOptions(
            model=role.model,
            reasoning_effort=role.reasoning_effort,
            fast_mode=role.fast_mode,
        )
    )


def parse_role_spec(
    spec: str,
    *,
    model: str | None = None,
    reasoning_effort: str | None = None,
    fast_mode: bool | None = None,
    launch_mode: str | None = None,
    dsh_plugin: str | None = None,
    workspace: Path | None = None,
) -> tuple[str, Role]:
    if "=" not in spec:
        raise InvalidArgument(
            "role must be ROLE=origin or "
            f"ROLE=ADAPTER:POLICY[:PROFILE]: {spec!r}"
        )
    role_id, binding = spec.split("=", 1)
    from .config import validate_role_id

    validate_role_id(role_id)
    if binding == "origin":
        if (
            model is not None
            or reasoning_effort is not None
            or fast_mode is not None
            or launch_mode is not None
            or dsh_plugin is not None
        ):
            raise InvalidArgument(
                f"Harness launch options require an External role: {role_id}"
            )
        return role_id, Role(role_id, "origin")
    parts = binding.split(":")
    if len(parts) not in {2, 3}:
        raise InvalidArgument(
            "external role must be ROLE=ADAPTER:POLICY[:PROFILE]: "
            f"{spec!r}"
        )
    adapter_id, session_policy = parts[:2]
    profile = parts[2] if len(parts) == 3 else DEFAULT_EXTERNAL_LAUNCH_PROFILE
    if adapter_id not in EXTERNAL_ADAPTER_IDS:
        raise InvalidArgument(f"unsupported adapter: {adapter_id}")
    if session_policy not in {"resume", "fresh"}:
        raise InvalidArgument(f"invalid session policy: {session_policy}")
    adapter = get_adapter(adapter_id)
    plugin_relative: str | None = None
    if dsh_plugin is not None:
        if adapter_id != "deepseek-harness":
            raise InvalidArgument(
                f"--role-dsh-plugin requires a deepseek-harness role: {role_id}"
            )
        if workspace is None:
            raise InvalidArgument("DSH plugin validation requires a workspace")
        supplied = Path(dsh_plugin)
        if not supplied.is_absolute():
            supplied = workspace / supplied
        plugin_relative = safe_relative(supplied, workspace)
        if not supplied.resolve(strict=True).is_dir() or supplied.is_symlink():
            raise InvalidArgument(
                f"DSH plugin must be a real directory inside the workspace: {supplied}"
            )
    effective_launch_mode = launch_mode or "interactive"
    adapter.assert_launch_mode(effective_launch_mode)
    option_values: dict[str, object] = {
        "model": model,
        "reasoning_effort": reasoning_effort,
        "fast_mode": fast_mode,
    }
    if workspace is not None:
        option_values["workspace"] = workspace
    options = adapter.resolve_launch_options(
        **option_values,
    )
    fingerprint = adapter.profile_fingerprint(
        profile,
        session_policy,
        effective_launch_mode,
    )
    return role_id, Role(
        role_id,
        "external",
        adapter_id,
        session_policy,
        profile,
        fingerprint,
        options.model,
        options.reasoning_effort,
        options.fast_mode,
        effective_launch_mode,
        plugin_relative,
    )


def _fsync_tree(path: Path) -> None:
    for directory, child_dirs, files in os.walk(path, topdown=False):
        current = Path(directory)
        for name in files:
            fd = os.open(current / name, os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        fsync_dir(current)


def _read_bootstrap_input(path: Path, subject: str) -> bytes:
    supplied = path.expanduser()
    try:
        info = supplied.lstat()
    except FileNotFoundError as exc:
        raise InvalidArgument(f"{subject} does not exist: {supplied}") from exc
    if supplied.is_symlink() or not stat.S_ISREG(info.st_mode):
        raise InvalidArgument(
            f"{subject} must be a regular non-symlink file: {supplied}"
        )
    return read_regular(supplied)


def initialize_run(
    *,
    team: Team,
    request_path: Path,
    protocol_path: Path,
) -> Path:
    workspace = team.workspace
    validate_git_boundaries(workspace)
    request = _read_bootstrap_input(request_path, "request")
    protocol = _read_bootstrap_input(protocol_path, "protocol")
    if not request.strip() or not protocol.strip():
        raise InvalidArgument("REQUEST.md and PROTOCOL.md must not be empty")
    for role in team.roles.values():
        if role.binding == "external":
            _assert_external_capability(role)
    ensure_dir(fixed_state_dir())
    assert_filesystem_capabilities(fixed_state_dir())
    assert_filesystem_capabilities(workspace)
    initialize_state_root(workspace)
    with workspace_lock(workspace, exclusive=True):
        validate_state_root(workspace)
        owner = read_owner(workspace)
        if owner is not None:
            raise AgentTeamError(
                "WORKSPACE_OWNED",
                f"workspace is owned by run {owner['run_id']}",
            )
        target = runs_dir(workspace) / team.run_id
        if path_entry_exists(target):
            raise InvalidArgument(f"run already exists: {team.run_id}")
        temporary = runs_dir(workspace) / (
            f".{team.run_id}.tmp-{os.getpid()}-{os.urandom(8).hex()}"
        )
        temporary.mkdir(mode=0o700)
        try:
            for directory in (
                "roles",
                "events",
                "handoffs",
                "resumes",
                "turns",
                "sessions",
                "artifacts",
                "completion",
                "logs",
            ):
                (temporary / directory).mkdir(mode=0o700)
            atomic_write(temporary / "REQUEST.md", request, immutable=True)
            atomic_write(temporary / "PROTOCOL.md", protocol, immutable=True)
            atomic_write(temporary / "team.json", team.canonical_bytes(), immutable=True)
            journal_fd = create_empty_regular(temporary / "journal.lock")
            os.close(journal_fd)
            _fsync_tree(temporary)
            os.rename(temporary, target)
            fsync_dir(target.parent)
        finally:
            if path_entry_exists(temporary):
                shutil.rmtree(temporary)
    return target


def _preflight_start(run_dir: Path) -> Team:
    projection = scan_journal(run_dir)
    team = projection.team
    if projection.status != "UNSTARTED":
        return team
    for role in team.roles.values():
        if role.binding == "external":
            current_identity()
            tmux_executable()
            adapter = get_adapter(role.adapter or "")
            report = adapter.probe()
            if report.authenticated is False:
                raise AgentTeamError(
                    "HARNESS_NOT_AUTHENTICATED",
                    f"{role.adapter} is not authenticated",
                )
            if not report.launcher_stays_in_process_group:
                raise AgentTeamError(
                    "HARNESS_PROCESS_MODEL_UNSUPPORTED",
                    f"{role.adapter} launcher cannot be proven to stay in the "
                    "managed Runner process group",
                )
            adapter.assert_profile(
                role.launch_profile or "",
                role.session_policy or "",
                role.launch_profile_sha256 or "",
                role.launch_mode or "headless",
            )
            adapter.assert_launch_options(
                HarnessLaunchOptions(
                    model=role.model,
                    reasoning_effort=role.reasoning_effort,
                    fast_mode=role.fast_mode,
                )
            )
    initial = team.roles[team.initial_role]
    if initial.binding == "external":
        get_adapter(initial.adapter or "").prepare_run_state(
            run_dir=run_dir,
            role_id=initial.role_id,
            launch_mode=initial.launch_mode or "headless",
        )
    return team


def _full_access_roles(team: Team) -> tuple[str, ...]:
    return tuple(
        sorted(
            role.role_id
            for role in team.roles.values()
            if role.binding == "external"
            and role.launch_profile == "full-access"
        )
    )


def start_run(
    run_dir: Path,
    *,
    confirm_full_access: bool = False,
) -> dict[str, Any]:
    try:
        initial_projection = scan_journal(run_dir)
        full_access_roles = _full_access_roles(initial_projection.team)
        if (
            initial_projection.status == "UNSTARTED"
            and full_access_roles
            and not confirm_full_access
        ):
            raise FullAccessConfirmationRequired(full_access_roles)
        team = _preflight_start(run_dir)
        initial_projection = scan_journal(run_dir)
    except IntegrityError:
        # Started runs share Recover's deterministic process-safety and
        # uniquely-identifiable Turn-damage convergence path.
        from .management import recover_run

        recovered = recover_run(run_dir)
        team = load_team(run_dir)
        return {
            "run_id": team.run_id,
            "run_dir": str(run_dir),
            "status": recovered["status"],
            "kickoff_event": None,
            "tmux": recovered["tmux"]
            or {"session": None, "created": [], "existing": []},
            "initial_binding": team.roles[team.initial_role].binding,
            "recovery_actions": recovered["actions"],
            "owner_released": recovered["owner_released"],
        }
    if initial_projection.status != "UNSTARTED":
        # Repeated start has the same deterministic convergence behavior as
        # recover and must not require a terminal run's old Harness install.
        from .management import recover_run

        recovered = recover_run(run_dir)
        return {
            "run_id": team.run_id,
            "run_dir": str(run_dir),
            "status": recovered["status"],
            "kickoff_event": None,
            "tmux": recovered["tmux"]
            or {"session": None, "created": [], "existing": []},
            "initial_binding": team.roles[team.initial_role].binding,
            "recovery_actions": recovered["actions"],
            "owner_released": recovered["owner_released"],
        }
    started_event: dict[str, Any] | None = None
    runtime: dict[str, Any]
    with locked_run(run_dir, exclusive=True):
        projection = scan_journal(run_dir)
        owner = read_owner(team.workspace)
        if projection.status == "UNSTARTED":
            if owner is not None and owner["run_id"] != team.run_id:
                raise AgentTeamError(
                    "WORKSPACE_OWNED",
                    f"workspace is owned by run {owner['run_id']}",
                )
            acquire_owner(team.workspace, team.run_id, rfc3339())
            try:
                capture_workspace_facts(
                    team.workspace,
                    turn_id="kickoff-preflight",
                    boundary="before",
                    pre_kickoff=True,
                )
                request = read_regular(run_dir / "REQUEST.md")
                protocol = read_regular(run_dir / "PROTOCOL.md")
                team_bytes = read_regular(run_dir / "team.json")
                full_access_confirmation = (
                    "Full-access confirmation: the user confirmed once "
                    "before this Run started that External roles "
                    f"`{'`, `'.join(full_access_roles)}` may access the "
                    "host filesystem and network without per-command "
                    "approvals.\n\n"
                    if full_access_roles
                    else ""
                )
                kickoff = (
                    "# Agent-Team Kickoff\n\n"
                    f"Initial role: `{team.initial_role}`.\n\n"
                    f"{full_access_confirmation}"
                    "Read REQUEST.md and PROTOCOL.md, then execute the initial role.\n"
                ).encode()
                started_event = commit_event(
                    run_dir,
                    event_type="kickoff",
                    payload_relative=(
                        f"handoffs/0001-kickoff-to-{team.initial_role}.md"
                    ),
                    payload_bytes=kickoff,
                    from_role=None,
                    to_role=team.initial_role,
                    turn_id=None,
                    extra={
                        "request_sha256": sha256_bytes(request),
                        "protocol_sha256": sha256_bytes(protocol),
                        "team_sha256": sha256_bytes(team_bytes),
                    },
                )
            except BaseException:
                # A failed fsync can be reported after the Event rename became
                # visible. Never release Ownership unless a fresh Journal scan
                # proves that Kickoff was not committed.
                try:
                    after_failure = scan_journal(run_dir)
                except (IntegrityError, OSError):
                    after_failure = None
                if (
                    after_failure is not None
                    and after_failure.status == "UNSTARTED"
                ):
                    release_owner(team.workspace, team.run_id)
                raise
        else:
            if owner is None or owner["run_id"] != team.run_id:
                raise IntegrityError("started run has missing or mismatched ownership")
        current_status = scan_journal(run_dir).status
        runtime = (
            ensure_workers(
                run_dir,
                team,
                role_ids=(
                    (team.initial_role,)
                    if team.roles[team.initial_role].binding == "external"
                    else ()
                ),
            )
            if current_status in {"RUNNING", "BLOCKED"}
            else {"session": None, "created": [], "existing": []}
        )
    target = team.roles[team.initial_role]
    if started_event and target.binding == "external":
        signal_change(team.run_id, target.role_id)
    return {
        "run_id": team.run_id,
        "run_dir": str(run_dir),
        "status": current_status,
        "kickoff_event": started_event,
        "tmux": runtime,
        "initial_binding": target.binding,
    }
