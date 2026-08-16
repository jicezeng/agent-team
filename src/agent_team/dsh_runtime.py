from __future__ import annotations

import contextlib
import json
import os
import secrets
import shutil
import stat
import subprocess
from pathlib import Path
from typing import Any

from .errors import AgentTeamError, IntegrityError
from .state import fixed_state_dir
from .util import (
    atomic_json,
    atomic_write,
    ensure_dir,
    fsync_dir,
    path_entry_exists,
    read_json,
    read_regular,
)

DSH_NPM_PACKAGE = "@deepseek-ai/dsh"
DSH_NPM_VERSION = "0.1.0-rc.6"
DSH_NPM_INTEGRITY = (
    "sha512-brpZfED7ieRa2PQ5tUxMhHrM1pb2CmKFVM/f6yMULBDMicahk+Z2OsHgTwTDnoi"
    "Zm23Ftu9rQz0NN4pflaoJcg=="
)
_RUNTIME_SCHEMA_VERSION = 1


def managed_dsh_runtime() -> Path:
    return fixed_state_dir() / "installed" / "deepseek-harness-runtime"


def _runtime_marker(root: Path) -> Path:
    return root / "agent-team-runtime.json"


def _runtime_bin(root: Path) -> Path:
    return root / "node_modules" / "@deepseek-ai" / "dsh" / "lib" / "bin.js"


def _expected_marker(version_output: str) -> dict[str, Any]:
    return {
        "schema_version": _RUNTIME_SCHEMA_VERSION,
        "package": DSH_NPM_PACKAGE,
        "version": DSH_NPM_VERSION,
        "integrity": DSH_NPM_INTEGRITY,
        "version_output": version_output,
    }


def _read_package_version(root: Path) -> str | None:
    manifest = root / "node_modules" / "@deepseek-ai" / "dsh" / "package.json"
    try:
        value = json.loads(read_regular(manifest))
    except (OSError, json.JSONDecodeError, IntegrityError):
        return None
    if not isinstance(value, dict):
        return None
    version = value.get("version")
    return version if isinstance(version, str) else None


def _validate_runtime_root(root: Path) -> dict[str, Any]:
    try:
        info = root.lstat()
    except OSError as exc:
        raise AgentTeamError(
            "DEEPSEEK_HARNESS_RUNTIME_NOT_INSTALLED",
            "DeepSeek Harness runtime is not installed; run `agent-team install`",
        ) from exc
    if root.is_symlink() or not stat.S_ISDIR(info.st_mode):
        raise IntegrityError(f"DeepSeek Harness runtime root is unsafe: {root}")
    marker_path = _runtime_marker(root)
    try:
        marker = read_json(marker_path)
    except (OSError, IntegrityError) as exc:
        raise IntegrityError(
            f"DeepSeek Harness runtime marker is unavailable: {marker_path}"
        ) from exc
    expected_fields = {
        "schema_version",
        "package",
        "version",
        "integrity",
        "version_output",
    }
    if (
        set(marker) != expected_fields
        or marker.get("schema_version") != _RUNTIME_SCHEMA_VERSION
        or marker.get("package") != DSH_NPM_PACKAGE
        or marker.get("version") != DSH_NPM_VERSION
        or marker.get("integrity") != DSH_NPM_INTEGRITY
        or not isinstance(marker.get("version_output"), str)
        or not marker["version_output"]
    ):
        raise IntegrityError(
            "DeepSeek Harness managed runtime does not match this Agent-Team version"
        )
    if _read_package_version(root) != DSH_NPM_VERSION:
        raise IntegrityError("DeepSeek Harness managed package version is invalid")
    try:
        lock_bytes = read_regular(root / "pnpm-lock.yaml")
    except (OSError, IntegrityError) as exc:
        raise IntegrityError("DeepSeek Harness managed lockfile is unavailable") from exc
    if DSH_NPM_INTEGRITY.encode() not in lock_bytes:
        raise IntegrityError("DeepSeek Harness managed lockfile integrity is invalid")
    executable = _runtime_bin(root)
    try:
        executable_info = executable.lstat()
        resolved = executable.resolve(strict=True)
        resolved.relative_to(root.resolve(strict=True))
    except (OSError, RuntimeError, ValueError) as exc:
        raise IntegrityError(
            "DeepSeek Harness managed executable is unavailable or escapes its runtime"
        ) from exc
    if executable.is_symlink() or not stat.S_ISREG(executable_info.st_mode):
        raise IntegrityError("DeepSeek Harness managed executable is unsafe")
    if not os.access(executable, os.X_OK):
        raise IntegrityError("DeepSeek Harness managed executable is not executable")
    return marker


def managed_dsh_executable() -> Path:
    root = managed_dsh_runtime()
    _validate_runtime_root(root)
    return _runtime_bin(root).resolve(strict=True)


def managed_dsh_version() -> str:
    marker = _validate_runtime_root(managed_dsh_runtime())
    return str(marker["version_output"])


def managed_dsh_runtime_report() -> dict[str, Any]:
    root = managed_dsh_runtime()
    marker = _validate_runtime_root(root)
    return {
        "root": str(root),
        "executable": str(_runtime_bin(root).resolve(strict=True)),
        "package": marker["package"],
        "version": marker["version"],
        "integrity": marker["integrity"],
        "version_output": marker["version_output"],
    }


def _known_runtime_target(root: Path) -> bool:
    if not path_entry_exists(root):
        return True
    if root.is_symlink() or not root.is_dir():
        return False
    marker = _runtime_marker(root)
    if not path_entry_exists(marker):
        return False
    try:
        value = read_json(marker)
    except (OSError, IntegrityError):
        return False
    return (
        value.get("schema_version") == _RUNTIME_SCHEMA_VERSION
        and value.get("package") == DSH_NPM_PACKAGE
    )


def _make_private_tree(root: Path) -> None:
    resolved_root = root.resolve(strict=True)
    for directory, child_dirs, files in os.walk(
        root,
        topdown=False,
        followlinks=False,
    ):
        current = Path(directory)
        for name in (*child_dirs, *files):
            path = current / name
            info = path.lstat()
            if stat.S_ISLNK(info.st_mode):
                try:
                    path.resolve(strict=True).relative_to(resolved_root)
                except (OSError, RuntimeError, ValueError) as exc:
                    raise IntegrityError(
                        f"managed DeepSeek Harness symlink escapes its runtime: {path}"
                    ) from exc
                continue
            if stat.S_ISDIR(info.st_mode):
                path.chmod(0o700)
            elif stat.S_ISREG(info.st_mode):
                path.chmod(0o700 if stat.S_IMODE(info.st_mode) & 0o111 else 0o600)
            else:
                raise IntegrityError(
                    f"managed DeepSeek Harness contains an unsafe entry: {path}"
                )
        current.chmod(0o700)


def install_managed_dsh_runtime() -> dict[str, Any]:
    target = managed_dsh_runtime()
    if path_entry_exists(target):
        try:
            return {"installed": False, **managed_dsh_runtime_report()}
        except (AgentTeamError, IntegrityError):
            if not _known_runtime_target(target):
                raise IntegrityError(
                    f"refusing to replace an unowned DeepSeek Harness runtime: {target}"
                )

    pnpm = shutil.which("pnpm")
    node = shutil.which("node")
    if pnpm is None or node is None:
        missing = "pnpm" if pnpm is None else "node"
        raise AgentTeamError(
            "DEEPSEEK_HARNESS_INSTALL_PREREQUISITE_MISSING",
            f"{missing} is required to install the DeepSeek Harness runtime",
        )

    parent = target.parent
    ensure_dir(fixed_state_dir())
    ensure_dir(parent)
    fixed_state_dir().chmod(0o700)
    parent.chmod(0o700)
    suffix = f"{os.getpid()}-{secrets.token_hex(8)}"
    temporary = parent / f".tmp-{target.name}-{suffix}"
    backup = parent / f".old-{target.name}-{suffix}"
    temporary.mkdir(mode=0o700)
    try:
        atomic_json(
            temporary / "package.json",
            {
                "name": "agent-team-deepseek-harness-runtime",
                "private": True,
                "dependencies": {DSH_NPM_PACKAGE: DSH_NPM_VERSION},
            },
            immutable=True,
        )
        atomic_write(
            temporary / "pnpm-workspace.yaml",
            b"packages:\n  - .\n\nnodeLinker: hoisted\n",
            immutable=True,
        )
        install = subprocess.run(
            [
                pnpm,
                "install",
                "--prod",
                "--frozen-lockfile=false",
                "--node-linker=hoisted",
                "--package-import-method=copy",
                "--ignore-scripts",
            ],
            cwd=temporary,
            env={**os.environ, "CI": "1"},
            capture_output=True,
            text=True,
            check=False,
            timeout=300,
        )
        if install.returncode != 0:
            detail = (install.stderr or install.stdout).strip()[-2000:]
            raise AgentTeamError(
                "DEEPSEEK_HARNESS_INSTALL_FAILED",
                f"pnpm could not install {DSH_NPM_PACKAGE}@{DSH_NPM_VERSION}: {detail}",
            )
        if _read_package_version(temporary) != DSH_NPM_VERSION:
            raise IntegrityError("pnpm installed an unexpected DeepSeek Harness version")
        if DSH_NPM_INTEGRITY.encode() not in read_regular(
            temporary / "pnpm-lock.yaml"
        ):
            raise IntegrityError(
                "pnpm resolved an unexpected DeepSeek Harness package integrity"
            )
        executable = _runtime_bin(temporary)
        probe_home = temporary / "probe-home"
        probe = subprocess.run(
            [str(executable), "--version"],
            cwd=temporary,
            env={**os.environ, "DSH_HOME": str(probe_home)},
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
        if probe.returncode != 0:
            detail = (probe.stderr or probe.stdout).strip()[-2000:]
            raise AgentTeamError(
                "DEEPSEEK_HARNESS_INSTALL_FAILED",
                f"installed dsh --version failed: {detail}",
            )
        version_output = (probe.stdout or probe.stderr).strip()
        if DSH_NPM_VERSION not in version_output:
            raise IntegrityError(
                "installed DeepSeek Harness reported an unexpected version"
            )
        if path_entry_exists(probe_home):
            shutil.rmtree(probe_home)
        atomic_json(
            _runtime_marker(temporary),
            _expected_marker(version_output),
            immutable=True,
        )
        _make_private_tree(temporary)

        moved_old = False
        if path_entry_exists(target):
            os.rename(target, backup)
            moved_old = True
        try:
            os.rename(temporary, target)
            fsync_dir(parent)
        except BaseException:
            if moved_old and not path_entry_exists(target) and path_entry_exists(backup):
                os.rename(backup, target)
                fsync_dir(parent)
            raise
        if path_entry_exists(backup):
            shutil.rmtree(backup)
            fsync_dir(parent)
    finally:
        if path_entry_exists(temporary):
            shutil.rmtree(temporary)
        if path_entry_exists(backup) and path_entry_exists(target):
            with contextlib.suppress(OSError):
                shutil.rmtree(backup)
                fsync_dir(parent)

    return {"installed": True, **managed_dsh_runtime_report()}
