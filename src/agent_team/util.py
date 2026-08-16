from __future__ import annotations

import contextlib
import datetime as dt
import errno
import hashlib
import json
import os
import re
import secrets
import stat
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from .errors import AgentTeamError, IntegrityError, InvalidArgument

UTC = dt.timezone.utc
ATOMIC_TEMP_RE = re.compile(
    r"^\..+\.tmp-[1-9][0-9]*-[0-9a-f]{16}$"
)


def set_private_umask() -> None:
    """Keep files created by managed descendants private by default."""
    os.umask(0o077)


def utc_now() -> dt.datetime:
    return dt.datetime.now(UTC)


def rfc3339(value: dt.datetime | None = None) -> str:
    current = value or utc_now()
    return current.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def parse_rfc3339(value: str) -> dt.datetime:
    if not isinstance(value, str) or not value:
        raise IntegrityError(f"invalid RFC 3339 timestamp: {value!r}")
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise IntegrityError(f"invalid RFC 3339 timestamp: {value!r}") from exc
    if parsed.tzinfo is None:
        raise IntegrityError(f"timestamp has no timezone: {value!r}")
    return parsed.astimezone(UTC)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(read_regular(path))


def canonical_json_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"duplicate JSON object key: {key!r}")
        value[key] = item
    return value


def ensure_dir(path: Path, mode: int = 0o700) -> None:
    existed = path_entry_exists(path)
    path.mkdir(mode=mode, parents=True, exist_ok=True)
    current = path
    if current.is_symlink() or not current.is_dir():
        raise IntegrityError(f"expected non-symlink directory: {path}")
    if not existed:
        with contextlib.suppress(PermissionError):
            current.chmod(mode)


def is_uncommitted_atomic_temporary(path: Path) -> bool:
    if not ATOMIC_TEMP_RE.fullmatch(path.name):
        return False
    try:
        info = path.lstat()
    except OSError:
        return False
    if path.is_symlink():
        return False
    if stat.S_ISREG(info.st_mode):
        return info.st_nlink == 1
    return stat.S_ISDIR(info.st_mode)


def committed_directory_entries(path: Path) -> list[Path]:
    try:
        info = path.lstat()
    except OSError as exc:
        raise IntegrityError(f"required directory is unavailable: {path}") from exc
    if path.is_symlink() or not stat.S_ISDIR(info.st_mode):
        raise IntegrityError(f"expected non-symlink directory: {path}")
    return [
        item
        for item in path.iterdir()
        if not is_uncommitted_atomic_temporary(item)
    ]


def path_entry_exists(path: Path) -> bool:
    """Return whether a directory entry exists, including a dangling symlink."""
    try:
        path.lstat()
    except FileNotFoundError:
        return False
    return True


def open_regular(
    path: Path,
    flags: int = os.O_RDONLY,
    mode: int = 0o600,
) -> int:
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    cloexec = getattr(os, "O_CLOEXEC", 0)
    try:
        fd = os.open(path, flags | nofollow | cloexec, mode)
    except OSError as exc:
        if exc.errno in {errno.ELOOP, errno.ENOTDIR}:
            raise IntegrityError(f"refusing non-regular path: {path}") from exc
        raise
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise IntegrityError(f"expected regular file: {path}")
    except BaseException:
        os.close(fd)
        raise
    return fd


def read_regular(path: Path) -> bytes:
    fd = open_regular(path)
    try:
        return _read_open_regular(fd)
    finally:
        os.close(fd)


def read_private_regular(path: Path) -> bytes:
    """Read a Run-owned source while atomically removing shared permission bits."""
    fd = open_regular(path)
    try:
        info = os.fstat(fd)
        if info.st_nlink != 1:
            raise IntegrityError(f"private Run file has multiple hard links: {path}")
        os.fchmod(fd, 0o600)
        return _read_open_regular(fd)
    finally:
        os.close(fd)


def _read_open_regular(fd: int) -> bytes:
    chunks: list[bytes] = []
    while True:
        chunk = os.read(fd, 1024 * 1024)
        if not chunk:
            return b"".join(chunks)
        chunks.append(chunk)


def read_json(path: Path) -> dict[str, Any]:
    try:
        raw = read_regular(path)
        value = json.loads(raw, object_pairs_hook=_unique_json_object)
    except FileNotFoundError:
        raise
    except (UnicodeDecodeError, ValueError) as exc:
        raise IntegrityError(f"invalid JSON file: {path}") from exc
    if not isinstance(value, dict):
        raise IntegrityError(f"expected JSON object: {path}")
    return value


def parse_json_object(raw: bytes, *, subject: str) -> dict[str, Any]:
    try:
        value = json.loads(raw, object_pairs_hook=_unique_json_object)
    except (UnicodeDecodeError, ValueError) as exc:
        raise IntegrityError(f"invalid {subject}") from exc
    if not isinstance(value, dict):
        raise IntegrityError(f"{subject} must be an object")
    return value


def fsync_dir(path: Path) -> None:
    directory_flag = getattr(os, "O_DIRECTORY", 0)
    fd = os.open(path, os.O_RDONLY | directory_flag)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def atomic_write(
    path: Path,
    data: bytes,
    *,
    mode: int = 0o600,
    immutable: bool = False,
) -> None:
    ensure_dir(path.parent)
    target_exists = path_entry_exists(path)
    if target_exists:
        target_info = path.lstat()
        if path.is_symlink() or not stat.S_ISREG(target_info.st_mode):
            raise IntegrityError(f"refusing non-regular target: {path}")
    if immutable and target_exists:
        existing = read_regular(path)
        if existing == data:
            return
        raise IntegrityError(f"immutable file already exists with different content: {path}")
    name = f".{path.name}.tmp-{os.getpid()}-{secrets.token_hex(8)}"
    temporary = path.parent / name
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    fd = open_regular(temporary, flags, mode)
    try:
        view = memoryview(data)
        while view:
            written = os.write(fd, view)
            view = view[written:]
        os.fsync(fd)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            temporary.unlink()
        raise
    finally:
        os.close(fd)
    try:
        target_exists = path_entry_exists(path)
        if target_exists:
            target_info = path.lstat()
            if path.is_symlink() or not stat.S_ISREG(target_info.st_mode):
                raise IntegrityError(f"refusing non-regular target: {path}")
        if immutable and target_exists:
            existing = read_regular(path)
            if existing != data:
                raise IntegrityError(
                    f"immutable file raced with different content: {path}"
                )
            temporary.unlink()
            return
        os.replace(temporary, path)
        fsync_dir(path.parent)
    finally:
        with contextlib.suppress(FileNotFoundError):
            temporary.unlink()


def atomic_json(
    path: Path,
    value: dict[str, Any],
    *,
    immutable: bool = False,
) -> None:
    atomic_write(path, canonical_json_bytes(value), immutable=immutable)


def safe_relative(path: Path, root: Path) -> str:
    resolved_root = root.resolve(strict=True)
    try:
        resolved = path.resolve(strict=True)
    except FileNotFoundError as exc:
        raise InvalidArgument(f"file does not exist: {path}") from exc
    try:
        relative = resolved.relative_to(resolved_root)
    except ValueError as exc:
        raise InvalidArgument(f"path must be inside run directory: {path}") from exc
    return relative.as_posix()


def resolve_run_path(run_dir: Path, relative: str) -> Path:
    if not relative or relative.startswith("/"):
        raise IntegrityError(f"invalid run-relative path: {relative!r}")
    candidate = run_dir / relative
    root = run_dir.resolve(strict=True)
    try:
        parent = candidate.parent.resolve(strict=True)
        parent.relative_to(root)
    except (FileNotFoundError, ValueError) as exc:
        raise IntegrityError(f"path escapes run directory: {relative!r}") from exc
    return candidate


def require_keys(
    value: dict[str, Any],
    *,
    required: set[str],
    optional: set[str] = frozenset(),
    subject: str,
) -> None:
    keys = set(value)
    missing = required - keys
    unknown = keys - required - optional
    if missing:
        raise IntegrityError(f"{subject} missing fields: {sorted(missing)}")
    if unknown:
        raise IntegrityError(f"{subject} has unknown fields: {sorted(unknown)}")


def require_schema_version(
    value: dict[str, Any],
    supported: int | set[int] | frozenset[int] | tuple[int, ...],
    *,
    subject: str,
) -> int:
    versions = {supported} if isinstance(supported, int) else set(supported)
    schema_version = value.get("schema_version")
    if (
        isinstance(schema_version, bool)
        or not isinstance(schema_version, int)
        or schema_version not in versions
    ):
        raise IntegrityError(f"unsupported {subject} schema")
    return schema_version


def random_token(bytes_count: int = 24) -> str:
    # ``token_urlsafe`` may begin with ``-``. These tokens are also returned as
    # Origin claims and used as internal CLI option values, so give every new
    # token a stable non-option prefix while preserving all random entropy.
    return f"t_{secrets.token_urlsafe(bytes_count)}"


def write_all(fd: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        count = os.write(fd, view)
        view = view[count:]


def create_empty_regular(path: Path) -> int:
    ensure_dir(path.parent)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_APPEND
    fd = open_regular(path, flags, 0o600)
    os.fsync(fd)
    fsync_dir(path.parent)
    return fd


@contextlib.contextmanager
def temporary_directory(parent: Path, prefix: str) -> Iterator[Path]:
    ensure_dir(parent)
    path = Path(tempfile.mkdtemp(prefix=prefix, dir=parent))
    try:
        yield path
    finally:
        if path.exists():
            for child in sorted(path.rglob("*"), reverse=True):
                with contextlib.suppress(OSError):
                    if child.is_dir() and not child.is_symlink():
                        child.rmdir()
                    else:
                        child.unlink()
            with contextlib.suppress(OSError):
                path.rmdir()


def envelope(
    command: str,
    *,
    data: dict[str, Any] | None = None,
    error: AgentTeamError | None = None,
    observed_at: str | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "schema_version": 1,
        "command": command,
        "result": "error" if error else "ok",
        "observed_at": observed_at or rfc3339(),
    }
    if error:
        result["error"] = {
            "code": error.code,
            "message": error.message,
            "evidence_paths": list(error.evidence_paths),
        }
    else:
        result["data"] = data or {}
    return result
