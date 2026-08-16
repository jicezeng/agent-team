from __future__ import annotations

import json
import stat
import subprocess
from pathlib import Path

import pytest

from agent_team import dsh_runtime
from agent_team.errors import IntegrityError


def _fake_install_run(calls: list[tuple[str, ...]]):
    def run(
        argv: list[str],
        *,
        cwd: Path,
        env: dict[str, str],
        capture_output: bool,
        text: bool,
        check: bool,
        timeout: int,
    ) -> subprocess.CompletedProcess[str]:
        del env, capture_output, text, check, timeout
        calls.append(tuple(argv))
        root = Path(cwd)
        if argv[-1] == "--version":
            return subprocess.CompletedProcess(
                argv,
                0,
                stdout=f"{dsh_runtime.DSH_NPM_VERSION}\n",
                stderr="",
            )
        package = root / "node_modules" / "@deepseek-ai" / "dsh"
        executable = package / "lib" / "bin.js"
        executable.parent.mkdir(parents=True)
        executable.write_text("#!/usr/bin/env node\n", encoding="utf-8")
        executable.chmod(0o755)
        (package / "package.json").write_text(
            json.dumps({"version": dsh_runtime.DSH_NPM_VERSION}),
            encoding="utf-8",
        )
        (root / "pnpm-lock.yaml").write_text(
            f"integrity: {dsh_runtime.DSH_NPM_INTEGRITY}\n",
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(argv, 0, stdout="installed\n", stderr="")

    return run


def test_managed_dsh_runtime_installs_atomically_and_reuses_exact_tree(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = tmp_path / "state"
    calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(dsh_runtime, "fixed_state_dir", lambda: state)
    monkeypatch.setattr(
        dsh_runtime.shutil,
        "which",
        lambda name: f"/tools/{name}" if name in {"node", "pnpm"} else None,
    )
    monkeypatch.setattr(dsh_runtime.subprocess, "run", _fake_install_run(calls))

    installed = dsh_runtime.install_managed_dsh_runtime()
    reused = dsh_runtime.install_managed_dsh_runtime()

    root = state / "installed" / "deepseek-harness-runtime"
    marker = json.loads(
        (root / "agent-team-runtime.json").read_text(encoding="utf-8")
    )
    assert installed["installed"] is True
    assert reused["installed"] is False
    assert marker["version"] == dsh_runtime.DSH_NPM_VERSION
    assert marker["integrity"] == dsh_runtime.DSH_NPM_INTEGRITY
    assert len(calls) == 2
    assert calls[0][0:2] == ("/tools/pnpm", "install")
    assert "--ignore-scripts" in calls[0]
    assert calls[1][-1] == "--version"
    assert stat.S_IMODE(root.stat().st_mode) == 0o700
    assert stat.S_IMODE(dsh_runtime.managed_dsh_executable().stat().st_mode) == 0o700


def test_managed_dsh_runtime_refuses_to_replace_unowned_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = tmp_path / "state"
    target = state / "installed" / "deepseek-harness-runtime"
    target.mkdir(parents=True)
    (target / "unowned.txt").write_text("keep\n", encoding="utf-8")
    monkeypatch.setattr(dsh_runtime, "fixed_state_dir", lambda: state)

    with pytest.raises(IntegrityError, match="unowned"):
        dsh_runtime.install_managed_dsh_runtime()

    assert (target / "unowned.txt").read_text(encoding="utf-8") == "keep\n"
