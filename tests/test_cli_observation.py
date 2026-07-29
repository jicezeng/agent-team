from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_team.bootstrap import start_run
from agent_team.cli import main
from agent_team.management import cancel_run
from agent_team.observation import diagnose
from agent_team.origin import origin_context, wait_origin
from agent_team.state import state_paths
from agent_team.turns import iter_runtimes

from test_origin_flow import make_origin_run


def _tree_mtimes(root: Path) -> dict[str, int]:
    return {
        path.relative_to(root).as_posix(): path.stat().st_mtime_ns
        for path in root.rglob("*")
        if path.is_file()
    }


def test_status_resolves_active_owner_without_run_id_and_is_read_only(
    workspace: Path,
    request_protocol: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    run_dir = make_origin_run(
        workspace,
        request_protocol,
        run_id="at-test-owner-resolution",
    )
    start_run(run_dir)
    before = _tree_mtimes(run_dir)
    monkeypatch.chdir(workspace)

    with pytest.raises(SystemExit) as stopped:
        main(["status", "--json"])

    assert stopped.value.code == 0
    result = json.loads(capsys.readouterr().out)
    assert result["result"] == "ok"
    assert result["data"]["run_id"] == run_dir.name
    assert _tree_mtimes(run_dir) == before


def test_watch_jsonl_emits_complete_monotonic_snapshots_until_terminal(
    workspace: Path,
    request_protocol: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    run_dir = make_origin_run(
        workspace,
        request_protocol,
        run_id="at-test-watch-snapshots",
    )
    start_run(run_dir)
    wait_origin(run_dir, timeout=0)
    sleep_calls = 0

    def cancel_on_first_poll(_seconds: float) -> None:
        nonlocal sleep_calls
        sleep_calls += 1
        assert sleep_calls == 1
        cancel_run(run_dir)

    monkeypatch.setattr("agent_team.cli.time.sleep", cancel_on_first_poll)
    with pytest.raises(SystemExit) as stopped:
        main(
            [
                "watch",
                run_dir.name,
                "--workspace",
                str(workspace),
                "--jsonl",
            ]
        )

    assert stopped.value.code == 0
    snapshots = [
        json.loads(line)
        for line in capsys.readouterr().out.splitlines()
        if line.strip()
    ]
    assert [item["watch_seq"] for item in snapshots] == [1, 2]
    assert all(item["command"] == "watch" for item in snapshots)
    assert [item["data"]["run_status"] for item in snapshots] == [
        "RUNNING",
        "CANCELLED",
    ]
    expected_fields = {
        "run_id",
        "run_status",
        "health",
        "journal_tail",
        "current_role",
        "active_turn",
        "roles",
        "workspace_owner",
        "origin",
        "limits",
        "block",
        "recovery_required",
        "recommended_action",
        "details",
        "evidence_paths",
    }
    assert all(set(item["data"]) == expected_fields for item in snapshots)


def test_structured_argument_error_uses_error_envelope(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as stopped:
        main(["status", "--unknown-option", "--json"])

    captured = capsys.readouterr()
    assert stopped.value.code == 2
    assert "usage:" in captured.err
    result = json.loads(captured.out)
    assert result["result"] == "error"
    assert result["error"]["code"] == "INVALID_ARGUMENT"


def test_origin_context_never_claims_an_unclaimed_event(
    workspace: Path,
    request_protocol: tuple[Path, Path],
) -> None:
    run_dir = make_origin_run(
        workspace,
        request_protocol,
        run_id="at-test-origin-context",
    )
    started = start_run(run_dir)
    event_id = started["kickoff_event"]["event_id"]

    with pytest.raises(Exception) as rejected:
        origin_context(run_dir, event_id=event_id, claim=None)
    assert getattr(rejected.value, "code", None) == "ORIGIN_EVENT_NOT_CLAIMED"
    assert iter_runtimes(run_dir) == []

    claimed = wait_origin(run_dir, timeout=0)
    before = _tree_mtimes(run_dir)
    context = origin_context(
        run_dir,
        event_id=event_id,
        claim=claimed["claim"],
    )
    assert context["turn_id"] == claimed["turn_id"]
    assert context["code"] == "ORIGIN_KICKOFF"
    assert _tree_mtimes(run_dir) == before


def test_explicit_status_reports_missing_workspace_lock_without_recreating_it(
    workspace: Path,
    request_protocol: tuple[Path, Path],
    capsys: pytest.CaptureFixture[str],
) -> None:
    run_dir = make_origin_run(
        workspace,
        request_protocol,
        run_id="at-test-missing-observation-lock",
    )
    _, lock_path, _ = state_paths(workspace)
    lock_path.unlink()

    with pytest.raises(SystemExit) as stopped:
        main(
            [
                "status",
                run_dir.name,
                "--workspace",
                str(workspace),
                "--json",
            ]
        )

    assert stopped.value.code == 0
    result = json.loads(capsys.readouterr().out)
    assert result["result"] == "ok"
    assert result["data"]["run_status"] == "CORRUPTED"
    assert result["data"]["health"] == "corrupted"
    assert not lock_path.exists()

    with pytest.raises(SystemExit) as diagnosed:
        main(
            [
                "diagnose",
                run_dir.name,
                "--workspace",
                str(workspace),
                "--json",
            ]
        )
    assert diagnosed.value.code == 0
    diagnosis = json.loads(capsys.readouterr().out)["data"]
    assert diagnosis["checks"]
    workspace_lock_check = next(
        item for item in diagnosis["checks"] if item["check"] == "workspace_lock"
    )
    assert workspace_lock_check["status"] == "fail"
    assert diagnosis["attachments"] == {
        "paths": [],
        "pane_excerpt": None,
    }
    assert not lock_path.exists()


def test_diagnose_exposes_stable_subject_paths_and_origin_not_applicable_checks(
    workspace: Path,
    request_protocol: tuple[Path, Path],
) -> None:
    run_dir = make_origin_run(
        workspace,
        request_protocol,
        run_id="at-test-diagnostic-subjects",
    )
    start_run(run_dir)
    claim = wait_origin(run_dir, timeout=0)

    report = diagnose(run_dir)
    checks = {item["check"]: item for item in report["checks"]}

    assert checks["run_lock"]["subject_path"] == "journal.lock"
    assert checks["config"]["subject_path"] == "team.json"
    assert checks["active_turn"]["subject_path"] == (
        f"turns/{claim['turn_id']}/runtime.json"
    )
    assert checks["worker"]["status"] == "not_applicable"
    assert checks["session"]["status"] == "not_applicable"
    assert checks["tmux_runtime"]["status"] == "not_applicable"


def test_attach_pure_origin_run_returns_no_tmux_without_probing_tmux(
    workspace: Path,
    request_protocol: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    run_dir = make_origin_run(
        workspace,
        request_protocol,
        run_id="at-test-origin-attach",
    )
    start_run(run_dir)
    monkeypatch.setattr(
        "agent_team.cli.attach_tmux",
        lambda *_args, **_kwargs: pytest.fail("tmux must not be probed"),
    )

    with pytest.raises(SystemExit) as stopped:
        main(
            [
                "attach",
                run_dir.name,
                "--workspace",
                str(workspace),
            ]
        )

    assert stopped.value.code == 1
    result = json.loads(capsys.readouterr().out)
    assert result["error"]["code"] == "NO_TMUX_RUNTIME"
