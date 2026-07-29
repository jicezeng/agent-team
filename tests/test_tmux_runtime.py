from __future__ import annotations

from pathlib import Path

import pytest

from agent_team import tmux_runtime
from agent_team.config import Role, make_team
from agent_team.errors import AgentTeamError, IntegrityError
from agent_team.util import read_json


def test_has_session_uses_a_deterministic_per_run_server(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commands: list[list[str]] = []

    def run(command: list[str], **_kwargs):
        commands.append(command)
        return type(
            "Result",
            (),
            {"returncode": 1, "stdout": "", "stderr": "no server running"},
        )()

    monkeypatch.setattr(tmux_runtime, "tmux_executable", lambda: "/usr/bin/tmux")
    monkeypatch.setattr(tmux_runtime.subprocess, "run", run)

    assert tmux_runtime.has_session("at-first-run") is False
    assert commands == [
        [
            "/usr/bin/tmux",
            "-L",
            tmux_runtime.server_name("at-first-run"),
            "has-session",
            "-t",
            tmux_runtime.session_name("at-first-run"),
        ]
    ]
    assert tmux_runtime.server_name("at-first-run") != tmux_runtime.server_name(
        "at-second-run"
    )


def _external_team(workspace: Path):
    return make_team(
        run_id="at-test-tmux-runtime",
        workspace=workspace,
        origin_harness="codex",
        roles={
            "developer": Role(
                "developer",
                "external",
                "codex",
                "resume",
                "default",
                "0" * 64,
            )
        },
        initial_role="developer",
        max_turns=2,
        max_wall_time_seconds=300,
    )


def test_ensure_workers_refuses_duplicate_when_tmux_is_missing(
    tmp_path: Path,
    workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_dir = tmp_path / "run"
    (run_dir / "roles").mkdir(parents=True)
    (run_dir / "roles" / "developer.json").write_text(
        """{
  "schema_version": 1,
  "role_id": "developer",
  "worker_pid": 1234,
  "worker_start_id": "stable-start",
  "tmux_session": "agent-team-at-test-tmux-runtime",
  "tmux_pane_id": "%1",
  "updated_at": "2026-07-28T00:00:00.000Z"
}
""",
        encoding="utf-8",
    )
    monkeypatch.setattr(tmux_runtime, "tmux_executable", lambda: "/usr/bin/tmux")
    monkeypatch.setattr(tmux_runtime, "has_session", lambda run_id: False)
    monkeypatch.setattr(
        tmux_runtime,
        "process_identity_state",
        lambda pid, start_id: "match",
    )
    monkeypatch.setattr(
        tmux_runtime,
        "_run",
        lambda *args, **kwargs: pytest.fail("must not create a duplicate Worker"),
    )

    with pytest.raises(AgentTeamError) as rejected:
        tmux_runtime.ensure_workers(run_dir, _external_team(workspace))

    assert rejected.value.code == "LIVE_WORKER_WITHOUT_TMUX"


def test_list_windows_rejects_duplicate_names(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = type(
        "Result",
        (),
        {
            "stdout": (
                "developer\t1\t%1\t101\t0\n"
                "developer\t1\t%2\t102\t0\n"
            ),
            "returncode": 0,
        },
    )()
    monkeypatch.setattr(tmux_runtime, "has_session", lambda run_id: True)
    monkeypatch.setattr(tmux_runtime, "_run", lambda *args, **kwargs: result)

    with pytest.raises(IntegrityError, match="ambiguous"):
        tmux_runtime.list_windows("at-test-tmux-runtime")


def test_list_windows_rejects_multiple_panes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = type(
        "Result",
        (),
        {
            "stdout": "developer\t2\t%1\t101\t0\n",
            "returncode": 0,
        },
    )()
    monkeypatch.setattr(tmux_runtime, "has_session", lambda _run_id: True)
    monkeypatch.setattr(tmux_runtime, "_run", lambda *args, **kwargs: result)

    with pytest.raises(IntegrityError, match="exactly one pane"):
        tmux_runtime.list_windows("at-test-tmux-runtime")


def test_ensure_workers_persists_new_pane_identity_before_returning(
    tmp_path: Path,
    workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_dir = tmp_path / "run"
    (run_dir / "roles").mkdir(parents=True)
    calls: list[tuple[tuple[str, ...], dict[str, object]]] = []
    monkeypatch.setattr(tmux_runtime, "tmux_executable", lambda: "/usr/bin/tmux")
    monkeypatch.setattr(tmux_runtime, "has_session", lambda _run_id: False)
    monkeypatch.setattr(
        tmux_runtime,
        "list_windows",
        lambda _run_id: {
            "developer": {
                "tmux_pane_id": "%1",
                "pane_pid": 1234,
                "pane_dead": False,
            }
        },
    )
    monkeypatch.setattr(
        tmux_runtime,
        "process_start_id",
        lambda pid: "stable-start" if pid == 1234 else None,
    )
    monkeypatch.setattr(
        tmux_runtime,
        "process_identity_state",
        lambda pid, start_id: (
            "match"
            if (pid, start_id) == (1234, "stable-start")
            else "unknown"
        ),
    )
    monkeypatch.setattr(
        tmux_runtime,
        "_run",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )

    result = tmux_runtime.ensure_workers(run_dir, _external_team(workspace))

    assert result["created"] == ["developer"]
    assert calls[0][0][0] == "new-session"
    assert calls[0][1]["server"] == tmux_runtime.server_name(
        "at-test-tmux-runtime"
    )
    worker = read_json(run_dir / "roles" / "developer.json")
    assert worker["worker_pid"] == 1234
    assert worker["worker_start_id"] == "stable-start"
    assert worker["tmux_pane_id"] == "%1"


def test_ensure_workers_refuses_unattributed_existing_window(
    tmp_path: Path,
    workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_dir = tmp_path / "run"
    (run_dir / "roles").mkdir(parents=True)
    monkeypatch.setattr(tmux_runtime, "tmux_executable", lambda: "/usr/bin/tmux")
    monkeypatch.setattr(tmux_runtime, "has_session", lambda _run_id: True)
    monkeypatch.setattr(
        tmux_runtime,
        "list_windows",
        lambda _run_id: {
            "developer": {
                "tmux_pane_id": "%1",
                "pane_pid": 1234,
                "pane_dead": False,
            }
        },
    )

    with pytest.raises(AgentTeamError) as rejected:
        tmux_runtime.ensure_workers(run_dir, _external_team(workspace))

    assert rejected.value.code == "WORKER_IDENTITY_MISSING"


def test_ensure_workers_respawns_only_a_verified_dead_pane(
    tmp_path: Path,
    workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_dir = tmp_path / "run"
    (run_dir / "roles").mkdir(parents=True)
    (run_dir / "roles" / "developer.json").write_text(
        """{
  "schema_version": 1,
  "role_id": "developer",
  "worker_pid": 1234,
  "worker_start_id": "old-start",
  "tmux_session": "agent-team-at-test-tmux-runtime",
  "tmux_pane_id": "%1",
  "updated_at": "2026-07-28T00:00:00.000Z"
}
""",
        encoding="utf-8",
    )
    respawned = False

    def windows(_run_id: str):
        return {
            "developer": {
                "tmux_pane_id": "%1",
                "pane_pid": 5678 if respawned else 1234,
                "pane_dead": not respawned,
            }
        }

    def run(*args: str, **_kwargs):
        nonlocal respawned
        assert args[0] == "respawn-pane"
        respawned = True

    monkeypatch.setattr(tmux_runtime, "tmux_executable", lambda: "/usr/bin/tmux")
    monkeypatch.setattr(tmux_runtime, "has_session", lambda _run_id: True)
    monkeypatch.setattr(tmux_runtime, "list_windows", windows)
    monkeypatch.setattr(
        tmux_runtime,
        "process_identity_state",
        lambda pid, _start_id: "gone" if pid == 1234 else "match",
    )
    monkeypatch.setattr(
        tmux_runtime,
        "process_start_id",
        lambda pid: "new-start" if pid == 5678 else None,
    )
    monkeypatch.setattr(tmux_runtime, "_run", run)

    result = tmux_runtime.ensure_workers(run_dir, _external_team(workspace))

    assert result["created"] == ["developer"]
    worker = read_json(run_dir / "roles" / "developer.json")
    assert worker["worker_pid"] == 5678
    assert worker["worker_start_id"] == "new-start"


def test_signal_change_is_best_effort(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        tmux_runtime,
        "_run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            OSError("tmux server unavailable")
        ),
    )

    assert tmux_runtime.signal_change("at-test", "developer") is False
