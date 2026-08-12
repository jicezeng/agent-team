from __future__ import annotations

import hashlib
import os
import stat
import subprocess
from pathlib import Path
from typing import Any

from .errors import AgentTeamError, IntegrityError, InvalidArgument
from .state import validate_git_boundaries, validate_state_root
from .util import (
    atomic_json,
    canonical_json_bytes,
    read_json,
    require_keys,
    require_schema_version,
    rfc3339,
    sha256_bytes,
)


FACTS_REQUIRED = {
    "schema_version",
    "turn_id",
    "boundary",
    "snapshot_scope",
    "captured_at",
    "workspace_realpath",
    "git_head",
    "git_status_sha256",
    "business_tree_sha256",
    "workspace_state_sha256",
    "tracked_path_count",
    "untracked_path_count",
    "diff_stat",
}


def validate_runtime_git_boundaries(workspace: Path) -> None:
    try:
        validate_git_boundaries(workspace)
    except InvalidArgument as exc:
        raise IntegrityError(
            f"Git workspace boundary changed during the run: {exc.message}"
        ) from exc


def _run_git(
    workspace: Path,
    *args: str,
    allow_failure: bool = False,
) -> subprocess.CompletedProcess[bytes]:
    result = subprocess.run(
        ["git", "-C", str(workspace), *args],
        capture_output=True,
        check=False,
    )
    if result.returncode != 0 and not allow_failure:
        raise AgentTeamError(
            "WORKSPACE_SNAPSHOT_FAILED",
            f"git {' '.join(args)} failed: "
            f"{result.stderr.decode(errors='replace').strip()}",
        )
    return result


def _nul_paths(workspace: Path, *args: str) -> list[bytes]:
    output = _run_git(workspace, *args).stdout
    return [item for item in output.split(b"\0") if item]


def _git_path_is_ignored(workspace: Path, raw_path: bytes) -> bool:
    result = subprocess.run(
        [
            "git",
            "-C",
            str(workspace),
            "check-ignore",
            "-q",
            "-z",
            "--stdin",
            "--no-index",
        ],
        input=raw_path + b"\0",
        capture_output=True,
        check=False,
    )
    if result.returncode == 0:
        return True
    if result.returncode == 1:
        return False
    raise AgentTeamError(
        "WORKSPACE_SNAPSHOT_FAILED",
        "git check-ignore failed while validating filesystem entry "
        f"{os.fsdecode(raw_path)}: "
        f"{result.stderr.decode(errors='replace').strip()}",
    )


def _reject_unsupported_untracked_entries(workspace: Path) -> None:
    """Reject Git-unignored special files that Git's own listing omits."""
    root = os.fsencode(str(workspace))
    pending: list[tuple[bytes, bytes]] = [(root, b"")]
    while pending:
        absolute_dir, relative_dir = pending.pop()
        try:
            with os.scandir(absolute_dir) as iterator:
                entries = list(iterator)
        except OSError as exc:
            raise AgentTeamError(
                "WORKSPACE_SNAPSHOT_FAILED",
                "cannot scan workspace directory "
                f"{os.fsdecode(relative_dir) or '.'}: {exc}",
            ) from exc
        for entry in entries:
            raw_name = entry.name
            if not isinstance(raw_name, bytes):
                raw_name = os.fsencode(raw_name)
            raw_path = (
                raw_name
                if not relative_dir
                else relative_dir + b"/" + raw_name
            )
            if not relative_dir and raw_name in {b".git", b".agent-team"}:
                continue
            try:
                info = entry.stat(follow_symlinks=False)
            except OSError as exc:
                raise AgentTeamError(
                    "WORKSPACE_SNAPSHOT_FAILED",
                    f"filesystem entry changed during snapshot: "
                    f"{os.fsdecode(raw_path)}: {exc}",
                ) from exc
            if stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode):
                continue
            ignored = _git_path_is_ignored(workspace, raw_path)
            if stat.S_ISDIR(info.st_mode):
                if ignored:
                    continue
                if raw_name == b".git":
                    raise AgentTeamError(
                        "WORKSPACE_SNAPSHOT_FAILED",
                        "nested Git repository is not supported: "
                        f"{os.fsdecode(os.path.dirname(entry.path))}",
                    )
                pending.append((entry.path, raw_path))
                continue
            if not ignored:
                raise AgentTeamError(
                    "WORKSPACE_SNAPSHOT_FAILED",
                    "unsupported filesystem entry in Git-visible snapshot: "
                    f"{os.fsdecode(raw_path)}",
                )


def _git_head(workspace: Path) -> str | None:
    result = _run_git(
        workspace,
        "rev-parse",
        "--verify",
        "HEAD",
        allow_failure=True,
    )
    if result.returncode == 0:
        raw = result.stdout.strip()
        if (
            len(raw) not in {40, 64}
            or any(byte not in b"0123456789abcdef" for byte in raw)
        ):
            raise AgentTeamError(
                "WORKSPACE_SNAPSHOT_FAILED",
                "git rev-parse returned an invalid HEAD object ID",
            )
        return raw.decode("ascii")
    symbolic = _run_git(
        workspace,
        "symbolic-ref",
        "--quiet",
        "HEAD",
        allow_failure=True,
    )
    if symbolic.returncode == 0:
        reference = symbolic.stdout.strip()
        referenced = _run_git(
            workspace,
            "for-each-ref",
            "--format=%(objectname)",
            os.fsdecode(reference),
            allow_failure=True,
        )
        if referenced.returncode == 0 and not referenced.stdout.strip():
            return None
    detail = result.stderr.decode(errors="replace").strip()
    raise AgentTeamError(
        "WORKSPACE_SNAPSHOT_FAILED",
        f"unable to resolve Git HEAD: {detail or 'invalid repository HEAD'}",
    )


def _length_prefix(*fields: bytes) -> bytes:
    result = bytearray()
    for field in fields:
        result.extend(len(field).to_bytes(8, "big"))
        result.extend(field)
    return bytes(result)


def _check_nested_repository(workspace: Path, raw_path: bytes) -> None:
    parts = raw_path.split(b"/")
    if len(parts) < 2:
        return
    current = os.fsencode(str(workspace))
    for part in parts[:-1]:
        current = os.path.join(current, part)
        marker = os.path.join(current, b".git")
        if os.path.lexists(marker):
            raise AgentTeamError(
                "WORKSPACE_SNAPSHOT_FAILED",
                f"nested Git repository is not supported: {os.fsdecode(current)}",
            )


def _path_record(
    workspace: Path,
    raw_path: bytes,
    classification: bytes,
) -> bytes:
    _check_nested_repository(workspace, raw_path)
    absolute = os.path.join(os.fsencode(str(workspace)), raw_path)
    try:
        info = os.lstat(absolute)
    except FileNotFoundError:
        if classification == b"tracked":
            return _length_prefix(
                classification,
                raw_path,
                b"missing",
                b"000000",
                b"-",
            )
        raise AgentTeamError(
            "WORKSPACE_SNAPSHOT_FAILED",
            f"untracked path disappeared during snapshot: {os.fsdecode(raw_path)}",
        )
    if stat.S_ISREG(info.st_mode):
        mode = b"100755" if info.st_mode & stat.S_IXUSR else b"100644"
        digest = hashlib.sha256()
        fd = os.open(
            absolute,
            os.O_RDONLY
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0),
        )
        try:
            opened = os.fstat(fd)
            identity_before = (
                info.st_dev,
                info.st_ino,
                stat.S_IFMT(info.st_mode),
                info.st_size,
                info.st_mtime_ns,
            )
            opened_before = (
                opened.st_dev,
                opened.st_ino,
                stat.S_IFMT(opened.st_mode),
                opened.st_size,
                opened.st_mtime_ns,
            )
            if not stat.S_ISREG(opened.st_mode) or opened_before != identity_before:
                raise AgentTeamError(
                    "WORKSPACE_SNAPSHOT_FAILED",
                    f"path changed type during snapshot: {os.fsdecode(raw_path)}",
                )
            while True:
                chunk = os.read(fd, 1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
            opened_after = os.fstat(fd)
        finally:
            os.close(fd)
        try:
            path_after = os.lstat(absolute)
        except FileNotFoundError as exc:
            raise AgentTeamError(
                "WORKSPACE_SNAPSHOT_FAILED",
                f"path disappeared during snapshot: {os.fsdecode(raw_path)}",
            ) from exc
        stable_after = (
            opened_after.st_dev,
            opened_after.st_ino,
            stat.S_IFMT(opened_after.st_mode),
            opened_after.st_size,
            opened_after.st_mtime_ns,
        )
        path_identity_after = (
            path_after.st_dev,
            path_after.st_ino,
            stat.S_IFMT(path_after.st_mode),
            path_after.st_size,
            path_after.st_mtime_ns,
        )
        if stable_after != identity_before or path_identity_after != identity_before:
            raise AgentTeamError(
                "WORKSPACE_SNAPSHOT_FAILED",
                f"path changed during snapshot: {os.fsdecode(raw_path)}",
            )
        return _length_prefix(
            classification,
            raw_path,
            b"regular",
            mode,
            digest.hexdigest().encode("ascii"),
        )
    if stat.S_ISLNK(info.st_mode):
        target = os.readlink(absolute)
        if isinstance(target, str):
            target = os.fsencode(target)
        try:
            after = os.lstat(absolute)
        except FileNotFoundError as exc:
            raise AgentTeamError(
                "WORKSPACE_SNAPSHOT_FAILED",
                f"symlink disappeared during snapshot: {os.fsdecode(raw_path)}",
            ) from exc
        if (
            after.st_dev,
            after.st_ino,
            stat.S_IFMT(after.st_mode),
            after.st_size,
            after.st_mtime_ns,
        ) != (
            info.st_dev,
            info.st_ino,
            stat.S_IFMT(info.st_mode),
            info.st_size,
            info.st_mtime_ns,
        ):
            raise AgentTeamError(
                "WORKSPACE_SNAPSHOT_FAILED",
                f"symlink changed during snapshot: {os.fsdecode(raw_path)}",
            )
        return _length_prefix(
            classification,
            raw_path,
            b"symlink",
            b"120000",
            hashlib.sha256(target).hexdigest().encode("ascii"),
        )
    if stat.S_ISDIR(info.st_mode) and classification == b"tracked":
        return _length_prefix(
            classification,
            raw_path,
            b"missing",
            b"000000",
            b"-",
        )
    raise AgentTeamError(
        "WORKSPACE_SNAPSHOT_FAILED",
        f"unsupported filesystem entry in Git-visible snapshot: {os.fsdecode(raw_path)}",
    )


def capture_workspace_facts(
    workspace: Path,
    *,
    turn_id: str,
    boundary: str,
    pre_kickoff: bool = False,
) -> dict[str, Any]:
    if boundary not in {"before", "after"}:
        raise ValueError(f"invalid snapshot boundary: {boundary}")
    if pre_kickoff:
        validate_git_boundaries(workspace)
    else:
        validate_runtime_git_boundaries(workspace)
    validate_state_root(workspace)
    tracked = set(_nul_paths(workspace, "ls-files", "-z", "--cached"))
    untracked = set(
        _nul_paths(
            workspace,
            "ls-files",
            "-z",
            "--others",
            "--exclude-standard",
        )
    )
    tracked = {
        item
        for item in tracked
        if item != b".agent-team" and not item.startswith(b".agent-team/")
    }
    untracked = {
        item
        for item in untracked
        if item != b".agent-team" and not item.startswith(b".agent-team/")
    }
    _reject_unsupported_untracked_entries(workspace)
    if tracked & untracked:
        raise AgentTeamError(
            "WORKSPACE_SNAPSHOT_FAILED",
            "Git returned overlapping tracked and untracked paths",
        )
    records: list[bytes] = []
    for raw_path in sorted(tracked | untracked):
        classification = b"tracked" if raw_path in tracked else b"untracked"
        records.append(_path_record(workspace, raw_path, classification))
    business_tree = hashlib.sha256(b"".join(records)).hexdigest()
    status = _run_git(
        workspace,
        "status",
        "--porcelain=v2",
        "-z",
        "--untracked-files=all",
        "--",
        ".",
        ":(exclude).agent-team",
        ":(exclude).agent-team/**",
    ).stdout
    status_hash = sha256_bytes(status)
    head = _git_head(workspace)
    state_hash = sha256_bytes(
        (head or "-").encode("ascii")
        + status_hash.encode("ascii")
        + business_tree.encode("ascii")
    )
    diff_result = _run_git(
        workspace,
        "diff",
        "--stat",
        "--",
        ".",
        ":(exclude).agent-team",
        ":(exclude).agent-team/**",
        allow_failure=True,
    )
    diff_stat = diff_result.stdout.decode("utf-8", errors="replace").strip()
    if untracked:
        suffix = f"{len(untracked)} untracked path(s)"
        diff_stat = f"{diff_stat}; {suffix}" if diff_stat else suffix
    return {
        "schema_version": 1,
        "turn_id": turn_id,
        "boundary": boundary,
        "snapshot_scope": "git_visible",
        "captured_at": rfc3339(),
        "workspace_realpath": str(workspace.resolve(strict=True)),
        "git_head": head,
        "git_status_sha256": status_hash,
        "business_tree_sha256": business_tree,
        "workspace_state_sha256": state_hash,
        "tracked_path_count": len(tracked),
        "untracked_path_count": len(untracked),
        "diff_stat": diff_stat,
    }


def write_workspace_facts(path: Path, value: dict[str, Any]) -> str:
    validate_workspace_facts(value, expected_boundary=value.get("boundary"))
    atomic_json(path, value, immutable=True)
    return sha256_bytes(canonical_json_bytes(value))


def validate_workspace_facts(
    value: dict[str, Any],
    *,
    expected_turn_id: str | None = None,
    expected_boundary: str | None = None,
) -> dict[str, Any]:
    require_keys(value, required=FACTS_REQUIRED, subject="workspace facts")
    require_schema_version(value, 1, subject="workspace facts")
    if value["snapshot_scope"] != "git_visible":
        raise IntegrityError("workspace facts have invalid scope")
    if not isinstance(value["boundary"], str) or value["boundary"] not in {
        "before",
        "after",
    }:
        raise IntegrityError("workspace facts have invalid boundary")
    if expected_boundary is not None and value["boundary"] != expected_boundary:
        raise IntegrityError("workspace facts boundary mismatch")
    if expected_turn_id is not None and value["turn_id"] != expected_turn_id:
        raise IntegrityError("workspace facts turn mismatch")
    if not isinstance(value["turn_id"], str) or not value["turn_id"]:
        raise IntegrityError("workspace facts turn id is invalid")
    if (
        not isinstance(value["workspace_realpath"], str)
        or not value["workspace_realpath"].startswith("/")
    ):
        raise IntegrityError("workspace facts path is invalid")
    for field in {
        "git_status_sha256",
        "business_tree_sha256",
        "workspace_state_sha256",
    }:
        item = value[field]
        if (
            not isinstance(item, str)
            or len(item) != 64
            or any(char not in "0123456789abcdef" for char in item)
        ):
            raise IntegrityError(f"workspace facts {field} is invalid")
    if value["git_head"] is not None and (
        not isinstance(value["git_head"], str)
        or len(value["git_head"]) not in {40, 64}
        or any(char not in "0123456789abcdef" for char in value["git_head"])
    ):
        raise IntegrityError("workspace facts git_head is invalid")
    expected_state = sha256_bytes(
        (value["git_head"] or "-").encode("ascii")
        + value["git_status_sha256"].encode("ascii")
        + value["business_tree_sha256"].encode("ascii")
    )
    if value["workspace_state_sha256"] != expected_state:
        raise IntegrityError("workspace facts state hash is internally inconsistent")
    for field in {"tracked_path_count", "untracked_path_count"}:
        if (
            isinstance(value[field], bool)
            or not isinstance(value[field], int)
            or value[field] < 0
        ):
            raise IntegrityError(f"workspace facts {field} is invalid")
    if not isinstance(value["diff_stat"], str):
        raise IntegrityError("workspace facts diff stat is invalid")
    from .util import parse_rfc3339

    parse_rfc3339(value["captured_at"])
    return value


def load_workspace_facts(
    path: Path,
    *,
    expected_turn_id: str | None = None,
    expected_boundary: str | None = None,
) -> dict[str, Any]:
    return validate_workspace_facts(
        read_json(path),
        expected_turn_id=expected_turn_id,
        expected_boundary=expected_boundary,
    )


def same_workspace_state(left: dict[str, Any], right: dict[str, Any]) -> bool:
    return (
        left["workspace_realpath"] == right["workspace_realpath"]
        and left["workspace_state_sha256"] == right["workspace_state_sha256"]
    )
