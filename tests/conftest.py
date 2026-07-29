from __future__ import annotations

import subprocess
from pathlib import Path

import pytest


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "workspace"
    root.mkdir()
    subprocess.run(["git", "init", "-b", "main", str(root)], check=True, capture_output=True)
    (root / "tracked.txt").write_text("initial\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "add", "tracked.txt"], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(root),
            "-c",
            "user.name=Agent Team Tests",
            "-c",
            "user.email=agent-team@example.invalid",
            "commit",
            "-m",
            "initial",
        ],
        check=True,
        capture_output=True,
    )
    state_dir = tmp_path / "state"
    monkeypatch.setattr("agent_team.state.fixed_state_dir", lambda: state_dir)
    monkeypatch.setattr("agent_team.bootstrap.fixed_state_dir", lambda: state_dir)
    return root


@pytest.fixture
def request_protocol(tmp_path: Path) -> tuple[Path, Path]:
    request = tmp_path / "REQUEST.md"
    protocol = tmp_path / "PROTOCOL.md"
    request.write_text("# Request\n\nImplement the task.\n", encoding="utf-8")
    protocol.write_text(
        """# Agent Team Protocol

## Team roles

### reviewer

- Binding: origin
- Review and complete the task.

## Initial role

reviewer

## Completion condition

The reviewer confirms completion.
""",
        encoding="utf-8",
    )
    return request, protocol
