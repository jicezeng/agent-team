from __future__ import annotations

import os
import re
import secrets
import shutil
import stat
from pathlib import Path
from typing import Any

from agent_team.errors import AgentTeamError, IntegrityError
from agent_team.util import (
    ensure_dir,
    fsync_dir,
    path_entry_exists,
    read_regular,
    sha256_bytes,
)

_ENV_REFERENCE_PATTERNS = (
    re.compile(r"\{env:([A-Za-z_][A-Za-z0-9_]*)\}"),
    re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-[^}]*)?\}"),
    re.compile(r"\bprocess\.env\.([A-Za-z_][A-Za-z0-9_]*)\b"),
)


def environment_reference_names(value: object) -> tuple[str, ...]:
    """Collect environment names referenced by native Plugin/MCP config."""

    names: set[str] = set()

    def visit(item: object, *, key: str | None = None) -> None:
        if isinstance(item, dict):
            for child_key, child in item.items():
                if isinstance(child_key, str):
                    visit(child, key=child_key)
        elif isinstance(item, list):
            if key in {"env_vars", "environment_variables"}:
                names.update(
                    child
                    for child in item
                    if isinstance(child, str)
                    and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", child)
                )
            else:
                for child in item:
                    visit(child)
        elif isinstance(item, str):
            for pattern in _ENV_REFERENCE_PATTERNS:
                names.update(match.group(1) for match in pattern.finditer(item))

    visit(value)
    return tuple(sorted(names))


def _path_descriptor(path: Path) -> dict[str, object]:
    try:
        root_info = path.lstat()
    except OSError as exc:
        raise IntegrityError(f"capability snapshot is unavailable: {path}") from exc
    if path.is_symlink():
        raise IntegrityError(f"capability snapshot contains a symlink: {path}")

    entries: list[bytes] = []
    file_count = 0
    if stat.S_ISREG(root_info.st_mode):
        digest = sha256_bytes(read_regular(path))
        entries.append(
            b"file\0\0"
            + (b"1" if stat.S_IMODE(root_info.st_mode) & 0o111 else b"0")
            + b"\0"
            + digest.encode("ascii")
        )
        kind = "file"
        file_count = 1
    elif stat.S_ISDIR(root_info.st_mode):
        kind = "directory"

        def walk_error(exc: OSError) -> None:
            raise exc

        for directory, child_dirs, files in os.walk(
            path,
            topdown=True,
            followlinks=False,
            onerror=walk_error,
        ):
            current = Path(directory)
            current_info = current.lstat()
            if current.is_symlink() or not stat.S_ISDIR(current_info.st_mode):
                raise IntegrityError(
                    f"capability snapshot contains an unsafe directory: {current}"
                )
            child_dirs.sort()
            files.sort()
            for name in child_dirs:
                child = current / name
                child_info = child.lstat()
                if child.is_symlink() or not stat.S_ISDIR(child_info.st_mode):
                    raise IntegrityError(
                        f"capability snapshot contains an unsafe directory: {child}"
                    )
                entries.append(
                    b"directory\0"
                    + child.relative_to(path).as_posix().encode("utf-8")
                )
            for name in files:
                child = current / name
                child_info = child.lstat()
                if child.is_symlink() or not stat.S_ISREG(child_info.st_mode):
                    raise IntegrityError(
                        f"capability snapshot contains an unsafe entry: {child}"
                    )
                relative = child.relative_to(path).as_posix().encode("utf-8")
                digest = sha256_bytes(read_regular(child)).encode("ascii")
                entries.append(
                    b"file\0"
                    + relative
                    + b"\0"
                    + (b"1" if stat.S_IMODE(child_info.st_mode) & 0o111 else b"0")
                    + b"\0"
                    + digest
                )
                file_count += 1
    else:
        raise IntegrityError(f"capability source is not a file or directory: {path}")

    framed = b"".join(len(entry).to_bytes(8, "big") + entry for entry in entries)
    return {
        "kind": kind,
        "file_count": file_count,
        "content_sha256": sha256_bytes(framed),
    }


def _make_private(path: Path) -> None:
    info = path.lstat()
    if stat.S_ISREG(info.st_mode):
        path.chmod(0o700 if stat.S_IMODE(info.st_mode) & 0o111 else 0o600)
        return
    if not stat.S_ISDIR(info.st_mode) or path.is_symlink():
        raise IntegrityError(f"copied capability contains an unsafe entry: {path}")
    for directory, child_dirs, files in os.walk(
        path,
        topdown=False,
        followlinks=False,
    ):
        current = Path(directory)
        for name in (*child_dirs, *files):
            child = current / name
            child_info = child.lstat()
            if child.is_symlink():
                raise IntegrityError(
                    f"copied capability contains a symlink: {child}"
                )
            if stat.S_ISDIR(child_info.st_mode):
                child.chmod(0o700)
            elif stat.S_ISREG(child_info.st_mode):
                child.chmod(
                    0o700 if stat.S_IMODE(child_info.st_mode) & 0o111 else 0o600
                )
            else:
                raise IntegrityError(
                    f"copied capability contains an unsafe entry: {child}"
                )
        current.chmod(0o700)


def copy_capability_path(source: Path, target: Path) -> dict[str, object]:
    """Materialize one native capability path into immutable Run-owned state."""

    if path_entry_exists(target):
        return _path_descriptor(target)
    try:
        resolved_source = source.expanduser().resolve(strict=True)
        source_info = resolved_source.lstat()
    except OSError as exc:
        raise AgentTeamError(
            "HARNESS_CAPABILITY_UNAVAILABLE",
            f"Plugin capability path is unavailable: {source}",
        ) from exc
    if resolved_source.is_symlink() or not (
        stat.S_ISREG(source_info.st_mode) or stat.S_ISDIR(source_info.st_mode)
    ):
        raise AgentTeamError(
            "HARNESS_CAPABILITY_UNSAFE",
            f"Plugin capability path is not a regular file or directory: {source}",
        )

    ensure_dir(target.parent)
    temporary = target.parent / (
        f".{target.name}.tmp-{os.getpid()}-{secrets.token_hex(8)}"
    )
    try:
        if stat.S_ISDIR(source_info.st_mode):
            # Materialize links instead of retaining references to mutable user
            # state. A cyclic or broken source link fails the copy atomically.
            shutil.copytree(resolved_source, temporary, symlinks=False)
        else:
            shutil.copy2(resolved_source, temporary, follow_symlinks=True)
        _make_private(temporary)
        descriptor = _path_descriptor(temporary)
        os.rename(temporary, target)
        fsync_dir(target.parent)
        return descriptor
    except (OSError, shutil.Error) as exc:
        raise AgentTeamError(
            "HARNESS_CAPABILITY_COPY_FAILED",
            f"cannot snapshot Plugin capability path: {source}",
        ) from exc
    finally:
        if path_entry_exists(temporary):
            info = temporary.lstat()
            if stat.S_ISDIR(info.st_mode) and not temporary.is_symlink():
                shutil.rmtree(temporary)
            else:
                temporary.unlink()


def assert_capability_path(
    path: Path,
    expected: dict[str, Any],
    *,
    subject: str,
) -> None:
    required = {"kind", "file_count", "content_sha256"}
    if (
        set(expected) != required
        or expected.get("kind") not in {"file", "directory"}
        or isinstance(expected.get("file_count"), bool)
        or not isinstance(expected.get("file_count"), int)
        or expected["file_count"] < 0
        or not isinstance(expected.get("content_sha256"), str)
        or len(expected["content_sha256"]) != 64
    ):
        raise IntegrityError(f"{subject} descriptor is invalid")
    if _path_descriptor(path) != expected:
        raise IntegrityError(f"{subject} differs from its frozen snapshot")
