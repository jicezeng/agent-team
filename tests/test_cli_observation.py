from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_team.bootstrap import start_run
from agent_team.cli import _role_flags, _role_value_options, build_parser, main
from agent_team.management import cancel_run
from agent_team.observation import _journal_tail, corrupted_observation, diagnose
from agent_team.origin import origin_context, wait_origin
from agent_team.state import state_paths
from agent_team.turns import iter_runtimes
from agent_team.util import atomic_json, atomic_write, canonical_json_bytes, rfc3339

from test_origin_flow import make_origin_run
from test_worker_lifecycle import _external_run


def _tree_mtimes(root: Path) -> dict[str, int]:
    return {
        path.relative_to(root).as_posix(): path.stat().st_mtime_ns
        for path in root.rglob("*")
        if path.is_file()
    }


def test_init_parser_accepts_role_scoped_harness_options() -> None:
    args = build_parser().parse_args(
        [
            "init",
            "--request",
            "REQUEST.md",
            "--protocol",
            "PROTOCOL.md",
            "--role",
            "developer=claude-code:resume:default",
            "--role",
            "reviewer=codex:resume:default",
            "--role-model",
            "developer=opus",
            "--role-reasoning-effort",
            "reviewer=max",
            "--role-fast",
            "reviewer",
            "--role-launch-mode",
            "developer=headless",
            "--initial-role",
            "developer",
        ]
    )

    assert _role_value_options(args.role_model, option="--role-model") == {
        "developer": "opus"
    }
    assert _role_value_options(
        args.role_reasoning_effort,
        option="--role-reasoning-effort",
    ) == {"reviewer": "max"}
    assert _role_flags(args.role_fast, option="--role-fast") == {"reviewer"}
    assert _role_value_options(
        args.role_launch_mode,
        option="--role-launch-mode",
    ) == {"developer": "headless"}


def test_start_parser_accepts_one_time_full_access_confirmation() -> None:
    args = build_parser().parse_args(
        ["start", "at-example", "--confirm-full-access"]
    )

    assert args.confirm_full_access is True


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


def test_status_exposes_frozen_external_role_launch_configuration(
    workspace: Path,
    request_protocol: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    run_dir, _runtime = _external_run(
        workspace,
        request_protocol,
        monkeypatch,
        run_id="at-test-status-role-config",
        launch_mode="headless",
    )

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
    role = json.loads(capsys.readouterr().out)["data"]["roles"][0]
    assert role["role_id"] == "developer"
    assert role["adapter"] == "codex"
    assert role["session_policy"] == "fresh"
    assert role["launch_mode"] == "headless"
    assert role["launch_profile"] == "test-noninteractive"
    assert role["launch_profile_sha256"] == "0" * 64
    assert role["model"] is None
    assert role["reasoning_effort"] is None
    assert role["fast_mode"] is None


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


def test_corrupted_observation_preserves_fixed_details_and_only_real_evidence(
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "at-corrupted-observation"
    run_dir.mkdir()
    (run_dir / "team.json").write_text("{}\n", encoding="utf-8")

    observation = corrupted_observation(
        run_dir.name,
        "team snapshot is damaged",
        run_dir=run_dir,
        evidence_paths=["team.json", "missing.json", "../outside.json"],
    )

    assert set(observation["details"]) == {
        "supervisor_pid",
        "supervisor_start_id",
        "runner_pid",
        "runner_pgid",
        "runner_start_id",
        "owner_run_id",
    }
    assert observation["evidence_paths"] == ["team.json"]


def test_journal_tail_normalizes_event_time_to_utc() -> None:
    tail = _journal_tail(
        {
            "event_id": "kickoff-0001",
            "event_seq": 1,
            "event_type": "kickoff",
            "from_role": None,
            "to_role": "developer",
            "turn_id": None,
            "payload_path": "REQUEST.md",
            "created_at": "2026-08-12T20:00:00+08:00",
        }
    )

    assert tail is not None
    assert tail["created_at"] == "2026-08-12T12:00:00.000Z"


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


def test_attach_resolves_active_owner_without_run_id(
    workspace: Path,
    request_protocol: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_dir, _runtime = _external_run(
        workspace,
        request_protocol,
        monkeypatch,
        run_id="at-test-attach-owner-resolution",
    )
    attached: list[tuple[str, str | None]] = []
    monkeypatch.setattr(
        "agent_team.cli.attach_tmux",
        lambda run_id, role_id: attached.append((run_id, role_id)) or 0,
    )
    monkeypatch.chdir(workspace)

    with pytest.raises(SystemExit) as stopped:
        main(["attach", "--role", "developer"])

    assert stopped.value.code == 0
    assert attached == [(run_dir.name, "developer")]


def test_attach_without_run_id_does_not_guess_an_unstarted_run(
    workspace: Path,
    request_protocol: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    make_origin_run(
        workspace,
        request_protocol,
        run_id="at-test-attach-no-owner",
    )
    monkeypatch.setattr(
        "agent_team.cli.attach_tmux",
        lambda *_args, **_kwargs: pytest.fail("tmux must not be probed"),
    )
    monkeypatch.chdir(workspace)

    with pytest.raises(SystemExit) as stopped:
        main(["attach"])

    assert stopped.value.code == 3
    result = json.loads(capsys.readouterr().out)
    assert result["error"]["code"] == "RUN_NOT_FOUND"
    assert "provide an explicit run id" in result["error"]["message"]


def test_transcript_and_tail_expose_normalized_role_filtered_events(
    workspace: Path,
    request_protocol: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    run_dir, runtime = _external_run(
        workspace,
        request_protocol,
        monkeypatch,
        run_id="at-test-transcript-tail",
    )
    turn_dir = run_dir / "turns" / runtime["turn_id"]
    process_dir = turn_dir / "process"
    process_dir.mkdir(mode=0o700)
    inner = [
        {"type": "thread.started", "thread_id": "thread-1"},
        {
            "type": "item.completed",
            "item": {
                "id": "message-1",
                "type": "agent_message",
                "text": "Review complete.",
            },
        },
        {
            "type": "turn.completed",
            "usage": {"input_tokens": 20, "output_tokens": 5},
        },
    ]
    stream = b"".join(
        canonical_json_bytes(
            {
                "schema_version": 1,
                "seq": seq,
                "observed_at": rfc3339(),
                "source": "stdout",
                "encoding": "utf-8",
                "data": json.dumps(value) + "\n",
            }
        )
        for seq, value in enumerate(inner, start=1)
    )
    atomic_write(process_dir / "stream.jsonl", stream)
    atomic_json(
        process_dir / "launch.json",
        {
            "adapter_id": "codex",
            "argv": ["codex", "exec"],
            "cwd": str(workspace),
            "env": {},
            "stdin": "Review the current tree.",
            "launch_profile": "default",
            "launch_profile_sha256": "0" * 64,
            "starts_new_session": True,
        },
    )

    with pytest.raises(SystemExit) as transcript_exit:
        main(
            [
                "transcript",
                run_dir.name,
                "--workspace",
                str(workspace),
                "--role",
                "developer",
                "--json",
            ]
        )

    assert transcript_exit.value.code == 0
    transcript = json.loads(capsys.readouterr().out)["data"]
    assert transcript["turn_count"] == 1
    assert transcript["turns"][0]["role_id"] == "developer"
    assert transcript["turns"][0]["prompt"] == "Review the current tree."
    assert [
        event["event_type"] for event in transcript["turns"][0]["events"]
    ] == ["session", "agent_message", "usage"]
    assert transcript["summary"]["usage"]["input_tokens"] == 20

    with pytest.raises(SystemExit) as tail_exit:
        main(
            [
                "tail",
                run_dir.name,
                "--workspace",
                str(workspace),
                "--role",
                "developer",
                "--lines",
                "1",
                "--jsonl",
            ]
        )

    assert tail_exit.value.code == 0
    tailed = json.loads(capsys.readouterr().out)
    assert tailed["event_type"] == "usage"
    assert tailed["role_id"] == "developer"

    launch = json.loads((process_dir / "launch.json").read_text(encoding="utf-8"))
    launch["unexpected"] = True
    atomic_json(process_dir / "launch.json", launch)
    with pytest.raises(SystemExit) as damaged_launch:
        main(
            [
                "transcript",
                run_dir.name,
                "--workspace",
                str(workspace),
                "--json",
            ]
        )
    assert damaged_launch.value.code == 1
    error = json.loads(capsys.readouterr().out)
    assert error["error"]["code"] == "TEAM_CORRUPTED"


@pytest.mark.parametrize(
    ("failure", "expected_code"),
    [
        (OSError("read failed"), "OBSERVATION_IO_ERROR"),
        (RuntimeError("unexpected failure"), "OBSERVATION_INTERNAL_ERROR"),
    ],
)
def test_transcript_structures_interface_failures(
    workspace: Path,
    request_protocol: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    failure: Exception,
    expected_code: str,
) -> None:
    run_dir = make_origin_run(
        workspace,
        request_protocol,
        run_id="at-test-transcript-interface-error",
    )
    monkeypatch.setattr(
        "agent_team.cli.build_transcript",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(failure),
    )

    with pytest.raises(SystemExit) as exited:
        main(
            [
                "transcript",
                run_dir.name,
                "--workspace",
                str(workspace),
                "--json",
            ]
        )

    assert exited.value.code == 4
    envelope = json.loads(capsys.readouterr().out)
    assert envelope["command"] == "transcript"
    assert envelope["result"] == "error"
    assert envelope["error"]["code"] == expected_code


def test_non_observation_argument_is_not_mistaken_for_observation_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    failure = OSError("attach failed")
    monkeypatch.setattr(
        "agent_team.cli.dispatch",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(failure),
    )

    with pytest.raises(OSError, match="attach failed"):
        main(["attach", "transcript"])
