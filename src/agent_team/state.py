from __future__ import annotations

import contextlib
import fcntl
import os
import pwd
import shutil
import stat
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from .errors import AgentTeamError, IntegrityError, InvalidArgument, RunNotFound
from .util import (
    atomic_json,
    committed_directory_entries,
    ensure_dir,
    fsync_dir,
    read_json,
    parse_rfc3339,
    path_entry_exists,
    require_keys,
    require_schema_version,
    sha256_bytes,
)


ROOT_REQUIRED = {
    "schema_version",
    "workspace_realpath",
    "workspace_sha256",
    "state_dir_realpath",
    "state_dir_sha256",
}
OWNER_REQUIRED = {
    "schema_version",
    "run_id",
    "workspace_realpath",
    "workspace_sha256",
    "acquired_at",
}


def account_home() -> Path:
    return Path(pwd.getpwuid(os.getuid()).pw_dir).resolve(strict=True)


def fixed_state_dir() -> Path:
    home = account_home()
    if sys.platform == "darwin":
        return (home / "Library" / "Application Support" / "agent-team").resolve(
            strict=False
        )
    if sys.platform.startswith("linux"):
        return (home / ".local" / "state" / "agent-team").resolve(strict=False)
    raise AgentTeamError(
        "UNSUPPORTED_PLATFORM",
        "Stage 1 supports macOS and Linux only",
    )


def workspace_hash(workspace: Path) -> str:
    return sha256_bytes(os.fsencode(str(workspace.resolve(strict=True))))


def state_paths(workspace: Path) -> tuple[Path, Path, Path]:
    state_dir = fixed_state_dir()
    digest = workspace_hash(workspace)
    lock_path = state_dir / "workspace-locks" / f"{digest}.lock"
    owner_path = state_dir / "workspaces" / f"{digest}.json"
    return state_dir, lock_path, owner_path


def run_root(workspace: Path) -> Path:
    return workspace / ".agent-team"


def runs_dir(workspace: Path) -> Path:
    return run_root(workspace) / "runs"


def get_run_dir(workspace: Path, run_id: str) -> Path:
    candidate = runs_dir(workspace) / run_id
    if not path_entry_exists(candidate):
        raise RunNotFound(f"run {run_id!r} not found in {workspace}")
    if candidate.is_symlink() or not candidate.is_dir():
        raise IntegrityError(f"run directory is not a regular directory: {candidate}")
    return candidate


def discover_workspace(path: Path | str = ".") -> Path:
    supplied = Path(path).expanduser()
    try:
        start = supplied.resolve(strict=True)
    except FileNotFoundError as exc:
        raise InvalidArgument(f"workspace does not exist: {supplied}") from exc
    command = ["git", "-C", str(start), "rev-parse", "--show-toplevel"]
    result = subprocess.run(command, capture_output=True, check=False)
    if result.returncode != 0:
        raise InvalidArgument(
            f"workspace must be a Git worktree: {result.stderr.decode(errors='replace').strip()}"
        )
    try:
        root = Path(os.fsdecode(result.stdout).strip()).resolve(strict=True)
    except (FileNotFoundError, OSError) as exc:
        raise InvalidArgument("Git worktree root cannot be resolved") from exc
    if start != root:
        raise InvalidArgument(
            f"workspace must be the Git worktree root ({root}), not {start}"
        )
    return root


def _git_bytes(workspace: Path, *args: str) -> bytes:
    result = subprocess.run(
        ["git", "-C", str(workspace), *args],
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise IntegrityError(
            f"git {' '.join(args)} failed: "
            f"{result.stderr.decode(errors='replace').strip()}"
        )
    return result.stdout


def validate_git_boundaries(workspace: Path) -> None:
    current = discover_workspace(workspace)
    if current != workspace.resolve(strict=True):
        raise IntegrityError("workspace no longer resolves to its Git worktree root")
    sparse_result = subprocess.run(
        [
            "git",
            "-C",
            str(workspace),
            "config",
            "--bool",
            "core.sparseCheckout",
        ],
        capture_output=True,
        check=False,
    )
    if sparse_result.returncode not in {0, 1}:
        raise IntegrityError(
            "unable to inspect core.sparseCheckout: "
            + sparse_result.stderr.decode(errors="replace").strip()
        )
    sparse = sparse_result.stdout.strip()
    if sparse.lower() == b"true":
        raise InvalidArgument("sparse checkout is not supported in Stage 1")
    index = _git_bytes(workspace, "ls-files", "--stage", "-z")
    for record in index.split(b"\0"):
        if not record:
            continue
        metadata, _, raw_path = record.partition(b"\t")
        mode = metadata.split(b" ", 1)[0]
        if mode == b"160000":
            raise InvalidArgument(
                f"Gitlink/submodule is not supported: {os.fsdecode(raw_path)}"
            )
        if raw_path == b".agent-team" or raw_path.startswith(b".agent-team/"):
            raise InvalidArgument(".agent-team must not be tracked by Git")


def _validate_directory(path: Path, subject: str) -> None:
    try:
        info = path.lstat()
    except FileNotFoundError as exc:
        raise IntegrityError(f"{subject} is missing: {path}") from exc
    if not stat.S_ISDIR(info.st_mode) or path.is_symlink():
        raise IntegrityError(f"{subject} must be a non-symlink directory: {path}")


def validate_state_root(workspace: Path) -> dict[str, Any]:
    root_dir = run_root(workspace)
    _validate_directory(root_dir, "state root directory")
    marker = root_dir / "root.json"
    value = read_json(marker)
    require_keys(value, required=ROOT_REQUIRED, subject="root.json")
    require_schema_version(value, 1, subject="root.json")
    state_dir = fixed_state_dir()
    expected_workspace = str(workspace.resolve(strict=True))
    expected_state = str(state_dir)
    expected = {
        "schema_version": 1,
        "workspace_realpath": expected_workspace,
        "workspace_sha256": sha256_bytes(os.fsencode(expected_workspace)),
        "state_dir_realpath": expected_state,
        "state_dir_sha256": sha256_bytes(os.fsencode(expected_state)),
    }
    if value != expected:
        raise IntegrityError(
            "root.json does not match workspace and fixed state directory"
        )
    return value


def _new_root_value(workspace: Path) -> dict[str, Any]:
    state_dir = fixed_state_dir()
    workspace_text = str(workspace.resolve(strict=True))
    state_text = str(state_dir)
    return {
        "schema_version": 1,
        "workspace_realpath": workspace_text,
        "workspace_sha256": sha256_bytes(os.fsencode(workspace_text)),
        "state_dir_realpath": state_text,
        "state_dir_sha256": sha256_bytes(os.fsencode(state_text)),
    }


def ensure_state_directories() -> Path:
    state_dir = fixed_state_dir()
    ensure_dir(state_dir)
    ensure_dir(state_dir / "workspace-locks")
    ensure_dir(state_dir / "workspaces")
    return state_dir


def ensure_workspace_lock(workspace: Path) -> Path:
    _, lock_path, _ = state_paths(workspace)
    ensure_dir(lock_path.parent)
    if not path_entry_exists(lock_path):
        try:
            fd = os.open(
                lock_path,
                os.O_WRONLY
                | os.O_CREAT
                | os.O_EXCL
                | getattr(os, "O_CLOEXEC", 0),
                0o600,
            )
        except FileExistsError:
            # A concurrent initializer committed the one stable lock name.
            # The common validation below still rejects links/hardlinks/types.
            pass
        else:
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
            fsync_dir(lock_path.parent)
    info = lock_path.lstat()
    if not stat.S_ISREG(info.st_mode) or lock_path.is_symlink() or info.st_nlink != 1:
        raise IntegrityError(f"workspace operation lock is invalid: {lock_path}")
    return lock_path


@contextmanager
def file_lock(
    path: Path,
    *,
    exclusive: bool,
    create: bool = False,
    read_only: bool = False,
) -> Iterator[int]:
    if create and read_only:
        raise ValueError("a read-only lock cannot be created")
    access = os.O_RDONLY if read_only else os.O_RDWR
    flags = access | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    if create:
        flags |= os.O_CREAT
    try:
        fd = os.open(path, flags, 0o600)
    except FileNotFoundError as exc:
        raise IntegrityError(f"required lock file is missing: {path}") from exc
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise IntegrityError(f"lock path is not a regular file: {path}")
        fcntl.flock(fd, fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
        try:
            current = path.lstat()
        except FileNotFoundError as exc:
            raise IntegrityError(
                f"lock path disappeared while opening: {path}"
            ) from exc
        if (
            not stat.S_ISREG(current.st_mode)
            or current.st_nlink != 1
            or (current.st_dev, current.st_ino) != (info.st_dev, info.st_ino)
        ):
            raise IntegrityError(f"lock path was replaced while opening: {path}")
        yield fd
    finally:
        with contextlib.suppress(OSError):
            fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


@contextmanager
def workspace_lock(
    workspace: Path,
    *,
    exclusive: bool,
    allow_create: bool = False,
) -> Iterator[None]:
    _, path, _ = state_paths(workspace)
    if allow_create:
        ensure_state_directories()
        path = ensure_workspace_lock(workspace)
    # Existing Run commands only need to acquire the stable operation lock;
    # they never write its contents.  Opening it read-only lets a sandboxed
    # Harness perform a formal action without granting write access to the
    # shared user-state lock directory (and therefore to other Workspaces).
    with file_lock(path, exclusive=exclusive, create=False, read_only=True):
        yield


@contextmanager
def locked_run(
    run_dir: Path,
    *,
    exclusive: bool,
    verify_team_context: bool = True,
) -> Iterator[None]:
    try:
        run_info = run_dir.lstat()
        workspace = run_dir.parent.parent.parent.resolve(strict=True)
    except (FileNotFoundError, OSError, RuntimeError) as exc:
        raise IntegrityError("run path cannot be resolved safely") from exc
    expected = workspace / ".agent-team" / "runs" / run_dir.name
    if (
        not stat.S_ISDIR(run_info.st_mode)
        or run_dir.is_symlink()
        or run_dir.resolve(strict=True) != expected
    ):
        raise IntegrityError("run directory is outside the canonical workspace path")
    with workspace_lock(workspace, exclusive=exclusive):
        with file_lock(run_dir / "journal.lock", exclusive=exclusive, create=False):
            if verify_team_context:
                team = read_json(run_dir / "team.json")
                if (
                    team.get("workspace") != str(workspace)
                    or team.get("run_id") != run_dir.name
                ):
                    raise IntegrityError(
                        "team.json does not match the locked workspace/run path",
                        "team.json",
                    )
            yield


def initialize_state_root(workspace: Path) -> None:
    ensure_state_directories()
    ensure_workspace_lock(workspace)
    _, _, owner_path = state_paths(workspace)
    with workspace_lock(workspace, exclusive=True):
        root_dir = run_root(workspace)
        if path_entry_exists(root_dir):
            _validate_directory(root_dir, "state root directory")
            children = [
                item.name for item in committed_directory_entries(root_dir)
            ]
            allowed = {"root.json", "runs"}
            unexpected = sorted(set(children) - allowed)
            if unexpected:
                raise InvalidArgument(
                    f".agent-team is non-empty with unsupported entries: {unexpected}"
                )
            if path_entry_exists(root_dir / "root.json"):
                validate_state_root(workspace)
            elif path_entry_exists(owner_path):
                raise IntegrityError(
                    "workspace owner exists while state root marker is missing"
                )
            elif children:
                raise InvalidArgument(
                    ".agent-team is non-empty but root.json is missing; "
                    "refusing to bind existing audit data to a new state namespace"
                )
            else:
                atomic_json(
                    root_dir / "root.json", _new_root_value(workspace), immutable=True
                )
        else:
            if path_entry_exists(owner_path):
                raise IntegrityError(
                    "workspace owner exists while .agent-team is missing; diagnose and unlock first"
                )
            ensure_dir(root_dir)
            atomic_json(
                root_dir / "root.json", _new_root_value(workspace), immutable=True
            )
        ensure_dir(runs_dir(workspace))


def validate_owner_file(workspace: Path, value: dict[str, Any]) -> dict[str, Any]:
    from .config import RUN_ID_RE

    require_keys(value, required=OWNER_REQUIRED, subject="workspace owner")
    require_schema_version(value, 1, subject="workspace owner")
    expected_path_hash = workspace_hash(workspace)
    if value["workspace_realpath"] != str(workspace.resolve(strict=True)):
        raise IntegrityError("workspace owner points to a different workspace")
    if value["workspace_sha256"] != expected_path_hash:
        raise IntegrityError("workspace owner hash mismatch")
    if not isinstance(value["run_id"], str) or not RUN_ID_RE.fullmatch(value["run_id"]):
        raise IntegrityError("workspace owner run_id is invalid")
    if not isinstance(value["acquired_at"], str) or not value["acquired_at"]:
        raise IntegrityError("workspace owner acquired_at is invalid")
    parse_rfc3339(value["acquired_at"])
    return value


def read_owner(workspace: Path) -> dict[str, Any] | None:
    _, _, owner_path = state_paths(workspace)
    if not path_entry_exists(owner_path):
        return None
    return validate_owner_file(workspace, read_json(owner_path))


def acquire_owner(workspace: Path, run_id: str, acquired_at: str) -> dict[str, Any]:
    _, _, owner_path = state_paths(workspace)
    existing = read_owner(workspace)
    if existing is not None:
        if existing["run_id"] == run_id:
            return existing
        raise AgentTeamError(
            "WORKSPACE_OWNED",
            f"workspace is owned by run {existing['run_id']}",
        )
    value = {
        "schema_version": 1,
        "run_id": run_id,
        "workspace_realpath": str(workspace.resolve(strict=True)),
        "workspace_sha256": workspace_hash(workspace),
        "acquired_at": acquired_at,
    }
    atomic_json(owner_path, value, immutable=True)
    return value


def release_owner(workspace: Path, run_id: str) -> bool:
    _, _, owner_path = state_paths(workspace)
    value = read_owner(workspace)
    if value is None:
        return False
    if value["run_id"] != run_id:
        raise IntegrityError(
            f"refusing to release owner for {value['run_id']} as {run_id}"
        )
    owner_path.unlink()
    fsync_dir(owner_path.parent)
    return True


def probe_filesystem(directory: Path) -> dict[str, bool]:
    ensure_dir(directory)
    prefix = f".agent-team-probe-{os.getpid()}-"
    source = directory / f"{prefix}source"
    target = directory / f"{prefix}target"
    lock = directory / f"{prefix}lock"
    result = {"flock": False, "atomic_rename": False, "fsync": False}
    try:
        fd = os.open(source, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            os.write(fd, b"probe")
            os.fsync(fd)
            result["fsync"] = True
        finally:
            os.close(fd)
        lock_fd = os.open(lock, os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            result["flock"] = True
        finally:
            os.close(lock_fd)
        os.replace(source, target)
        result["atomic_rename"] = target.read_bytes() == b"probe"
        fsync_dir(directory)
    finally:
        for item in (source, target, lock):
            with contextlib.suppress(FileNotFoundError):
                item.unlink()
    return result


def assert_filesystem_capabilities(directory: Path) -> None:
    report = probe_filesystem(directory)
    missing = [name for name, available in report.items() if not available]
    if missing:
        raise AgentTeamError(
            "FILESYSTEM_UNSUPPORTED",
            f"filesystem lacks required semantics at {directory}: {', '.join(missing)}",
        )


def remove_tree(path: Path) -> None:
    """Remove only an explicitly-created temporary directory."""
    if ".tmp-" not in path.name:
        raise InvalidArgument(f"refusing to remove non-temporary directory: {path}")
    shutil.rmtree(path)
