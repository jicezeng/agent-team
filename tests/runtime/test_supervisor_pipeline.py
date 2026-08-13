from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

import pytest

from agent_team.adapters.base import LaunchSpec
from agent_team.adapters.codex import CodexAdapter
from agent_team.bootstrap import start_run
from agent_team.errors import IntegrityError
from agent_team.journal import scan_journal
from agent_team.management import cancel_run, recover_run
from agent_team.state import locked_run, state_paths
from agent_team.supervisor import (
    StreamRecorder,
    _fresh_interactive_session_candidate,
    supervise_turn,
    validate_supervisor,
)
from agent_team.turns import (
    load_runtime,
    save_runtime,
    stage_external_action_locked,
)
from agent_team.util import atomic_json, atomic_write, read_json
from agent_team.worker import (
    _authorize_launch_locked,
    finalize_external_turn_locked,
)

from ._support import (
    NONCE,
    PROFILE,
    PROFILE_HASH,
    RUNNER_PID,
    SUPERVISOR_PID,
    _BootstrapAdapter,
    _configure_supervisor_child_state,
    _external_run,
    _persist_process_chain,
)


def test_supervisor_runner_pipeline_reaches_verified_normal_completion(
    workspace: Path,
    request_protocol: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import agent_team.supervisor as supervisor_module

    stream_closed = False
    original_close = StreamRecorder.close
    original_save_snapshot = supervisor_module._save_snapshot

    async def tracked_close(recorder: StreamRecorder) -> None:
        nonlocal stream_closed
        await original_close(recorder)
        stream_closed = True

    def tracked_save_snapshot(*args: Any, **kwargs: Any) -> None:
        snapshot = args[2]
        if snapshot["state"] == "finished":
            assert stream_closed
        original_save_snapshot(*args, **kwargs)

    monkeypatch.setattr(StreamRecorder, "close", tracked_close)
    monkeypatch.setattr(
        supervisor_module,
        "_save_snapshot",
        tracked_save_snapshot,
    )
    run_dir, runtime = _external_run(
        workspace,
        request_protocol,
        monkeypatch,
        run_id="at-worker-supervisor-runner-pipeline",
    )
    turn_dir = run_dir / "turns" / runtime["turn_id"]
    process_dir = turn_dir / "process"
    process_dir.mkdir(mode=0o700)
    harness = (
        "import json,sys;"
        "sys.stdin.read();"
        "print(json.dumps({'type':'thread.started',"
        "'thread_id':'pipeline-thread'}),flush=True);"
        "print(json.dumps({'type':'turn.completed'}),flush=True)"
    )
    launch = LaunchSpec(
        adapter_id="codex",
        argv=(sys.executable, "-c", harness),
        cwd=str(workspace),
        env={
            "AGENT_TEAM_RUN_ID": run_dir.name,
            "AGENT_TEAM_ROLE_ID": "developer",
            "AGENT_TEAM_TURN_ID": runtime["turn_id"],
            "AGENT_TEAM_RUN_DIR": str(run_dir),
            "AGENT_TEAM_TURN_DIR": str(turn_dir),
            "AGENT_TEAM_CLI": "/bin/true",
        },
        stdin="exercise the supervised pipeline\n",
        launch_profile=PROFILE,
        launch_profile_sha256=PROFILE_HASH,
        starts_new_session=True,
    )
    option_like_nonce = "-option-like-launch-nonce"
    with locked_run(run_dir, exclusive=True):
        runtime["launch_nonce"] = option_like_nonce
        save_runtime(
            turn_dir,
            runtime,
            team=scan_journal(run_dir).team,
        )
        atomic_json(
            process_dir / "launch.json",
            launch.to_json(),
            immutable=True,
        )
    monkeypatch.setattr(
        "agent_team.worker.get_adapter",
        lambda _adapter: _BootstrapAdapter(),
    )
    _configure_supervisor_child_state(
        workspace,
        monkeypatch,
        directory_name="child-bootstrap",
    )

    async def run_pipeline() -> int:
        task = asyncio.create_task(
            supervise_turn(
                run_dir,
                runtime["turn_id"],
                option_like_nonce,
                launch.content_sha256(),
            )
        )
        for _ in range(400):
            if task.done():
                result = await task
                raise AssertionError(
                    f"Supervisor exited before launch authorization: {result}"
                )
            snapshot_path = process_dir / "supervisor.json"
            if snapshot_path.exists():
                with locked_run(run_dir, exclusive=True):
                    snapshot = validate_supervisor(read_json(snapshot_path))
                    if snapshot["state"] == "waiting_authorization":
                        current = load_runtime(
                            turn_dir,
                            team=scan_journal(run_dir).team,
                        )
                        assert _authorize_launch_locked(
                            run_dir,
                            current,
                            snapshot,
                            expected_launch=launch,
                        )
                        break
            await asyncio.sleep(0.025)
        else:
            task.cancel()
            raise AssertionError("Supervisor never reached launch authorization")
        return await asyncio.wait_for(task, timeout=15)

    assert asyncio.run(run_pipeline()) == 0
    final_snapshot = validate_supervisor(read_json(process_dir / "supervisor.json"))
    assert final_snapshot["state"] == "finished"
    assert final_snapshot["agent_execution_started"] is True
    assert final_snapshot["adapter_completed"] is True
    assert final_snapshot["observed_session_ref"] == "pipeline-thread"
    assert final_snapshot["group_quiescent"] is True

    monkeypatch.setattr(
        "agent_team.worker.get_adapter",
        lambda _adapter: CodexAdapter(),
    )
    monkeypatch.setattr(
        "agent_team.worker.process_identity_state",
        lambda *_args, **_kwargs: "gone",
    )
    with locked_run(run_dir, exclusive=True):
        current = load_runtime(
            turn_dir,
            team=scan_journal(run_dir).team,
        )
        event = finalize_external_turn_locked(
            run_dir,
            current,
            allow_after_capture=True,
        )

    assert event is not None
    assert event["block_reason"] == "no_action"
    finalized = load_runtime(
        turn_dir,
        team=scan_journal(run_dir).team,
    )
    assert finalized["phase"] == "finalized"
    assert finalized["outcome"] == "stalled"
    assert finalized["workspace_facts_after_sha256"] is not None


def test_interactive_supervisor_uses_pty_and_stops_after_durable_action(
    workspace: Path,
    request_protocol: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_dir, runtime = _external_run(
        workspace,
        request_protocol,
        monkeypatch,
        run_id="at-worker-interactive-pipeline",
        launch_mode="interactive",
    )
    turn_dir = run_dir / "turns" / runtime["turn_id"]
    process_dir = turn_dir / "process"
    process_dir.mkdir(mode=0o700)
    prompt_path = process_dir / "prompt.md"
    prompt = "exercise the native interactive pipeline\n"
    harness = (
        "import os,sys,time;"
        "assert all(os.isatty(fd) for fd in (0,1,2));"
        f"assert {str(prompt_path)!r} in sys.argv[-1];"
        "assert 'authoritative prompt' in sys.argv[-1];"
        "print('\\x1b[32mNATIVE_TUI_READY\\x1b[0m',flush=True);"
        "time.sleep(60)"
    )
    launch = LaunchSpec(
        adapter_id="codex",
        argv=(sys.executable, "-c", harness),
        cwd=str(workspace),
        env={
            "AGENT_TEAM_RUN_ID": run_dir.name,
            "AGENT_TEAM_ROLE_ID": "developer",
            "AGENT_TEAM_TURN_ID": runtime["turn_id"],
            "AGENT_TEAM_RUN_DIR": str(run_dir),
            "AGENT_TEAM_TURN_DIR": str(turn_dir),
            "AGENT_TEAM_CLI": "/bin/true",
        },
        stdin=prompt,
        launch_profile=PROFILE,
        launch_profile_sha256=PROFILE_HASH,
        starts_new_session=True,
        launch_mode="interactive",
        prompt_file=str(prompt_path),
        expected_session_ref="interactive-thread",
    )
    option_like_nonce = "-interactive-launch-nonce"
    with locked_run(run_dir, exclusive=True):
        runtime["launch_nonce"] = option_like_nonce
        save_runtime(
            turn_dir,
            runtime,
            team=scan_journal(run_dir).team,
        )
        atomic_write(prompt_path, prompt.encode("utf-8"), immutable=True)
        atomic_json(process_dir / "launch.json", launch.to_json(), immutable=True)
    monkeypatch.setattr(
        "agent_team.worker.get_adapter",
        lambda _adapter: _BootstrapAdapter("interactive"),
    )
    _configure_supervisor_child_state(
        workspace,
        monkeypatch,
        directory_name="interactive-child-bootstrap",
    )

    async def run_pipeline() -> int:
        task = asyncio.create_task(
            supervise_turn(
                run_dir,
                runtime["turn_id"],
                option_like_nonce,
                launch.content_sha256(),
            )
        )
        authorized = False
        for _ in range(600):
            if task.done():
                result = await task
                raise AssertionError(
                    f"interactive Supervisor exited before action staging: {result}"
                )
            snapshot_path = process_dir / "supervisor.json"
            if snapshot_path.exists():
                running = False
                with locked_run(run_dir, exclusive=True):
                    snapshot = validate_supervisor(read_json(snapshot_path))
                    if snapshot["state"] == "waiting_authorization" and not authorized:
                        current = load_runtime(
                            turn_dir,
                            team=scan_journal(run_dir).team,
                        )
                        assert _authorize_launch_locked(
                            run_dir,
                            current,
                            snapshot,
                            expected_launch=launch,
                        )
                        authorized = True
                    elif snapshot["state"] == "running":
                        running = True
                if running:
                    payload = turn_dir / "completion.md"
                    payload.write_text(
                        "# Completion\n\nNative interactive action staged.\n",
                        encoding="utf-8",
                    )
                    with locked_run(run_dir, exclusive=True):
                        current = load_runtime(
                            turn_dir,
                            team=scan_journal(run_dir).team,
                        )
                        staged = stage_external_action_locked(
                            run_dir,
                            runtime=current,
                            action="complete",
                            source_file=payload,
                            to_role=None,
                        )
                    assert staged["code"] == "ACTION_ACCEPTED"
                    break
            await asyncio.sleep(0.025)
        else:
            task.cancel()
            raise AssertionError("interactive Supervisor never reached running")
        return await asyncio.wait_for(task, timeout=15)

    assert asyncio.run(run_pipeline()) == 0
    final_snapshot = validate_supervisor(read_json(process_dir / "supervisor.json"))
    assert final_snapshot["state"] == "finished"
    assert final_snapshot["agent_execution_started"] is True
    assert final_snapshot["adapter_completed"] is True
    assert final_snapshot["observed_session_ref"] == "interactive-thread"
    assert final_snapshot["termination_kind"] == "action"
    assert final_snapshot["process_exit_code"] != 0
    assert final_snapshot["group_quiescent"] is True
    stream = [
        json.loads(line)
        for line in (process_dir / "stream.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert any(item["source"] == "terminal" for item in stream)
    assert any("NATIVE_TUI_READY" in item["data"] for item in stream)

    monkeypatch.setattr(
        "agent_team.worker.get_adapter",
        lambda _adapter: CodexAdapter(),
    )
    monkeypatch.setattr(
        CodexAdapter,
        "finalize_run_state",
        lambda _self, **_kwargs: None,
    )
    monkeypatch.setattr(
        "agent_team.worker.process_identity_state",
        lambda *_args, **_kwargs: "gone",
    )
    with locked_run(run_dir, exclusive=True):
        current = load_runtime(
            turn_dir,
            team=scan_journal(run_dir).team,
        )
        event = finalize_external_turn_locked(
            run_dir,
            current,
            allow_after_capture=True,
        )

    assert event is not None
    assert event["event_type"] == "complete"
    assert scan_journal(run_dir).status == "COMPLETED"


def test_fresh_interactive_session_candidate_fails_closed() -> None:
    class SessionAdapter:
        refs: set[str] = {"prior", "current"}

        def interactive_session_refs(self, _launch: LaunchSpec) -> set[str]:
            return self.refs

    adapter = SessionAdapter()
    launch = LaunchSpec(
        adapter_id="codex",
        argv=("/bin/codex",),
        cwd="/worktree",
        env={},
        stdin="prompt",
        launch_profile="default",
        launch_profile_sha256="0" * 64,
        starts_new_session=True,
        launch_mode="interactive",
        prompt_file="/run/turn/process/prompt.md",
    )

    assert (
        _fresh_interactive_session_candidate(
            adapter,
            launch,
            baseline={"prior"},
            observed=None,
        )
        == "current"
    )

    adapter.refs = {"prior", "current", "ambiguous"}
    with pytest.raises(IntegrityError, match="multiple candidate"):
        _fresh_interactive_session_candidate(
            adapter,
            launch,
            baseline={"prior"},
            observed="current",
        )

    adapter.refs = {"prior"}
    with pytest.raises(IntegrityError, match="disappeared"):
        _fresh_interactive_session_candidate(
            adapter,
            launch,
            baseline={"prior"},
            observed="current",
        )


@pytest.mark.parametrize(
    ("trigger", "max_wall_time_seconds"),
    [
        ("cancel", 300),
        ("deadline", 2),
    ],
)
def test_supervisor_terminates_active_runner_after_cancel_or_deadline(
    workspace: Path,
    request_protocol: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    trigger: str,
    max_wall_time_seconds: int,
) -> None:
    run_dir, runtime = _external_run(
        workspace,
        request_protocol,
        monkeypatch,
        run_id=f"at-worker-supervisor-{trigger}",
        max_wall_time_seconds=max_wall_time_seconds,
    )
    turn_dir = run_dir / "turns" / runtime["turn_id"]
    process_dir = turn_dir / "process"
    process_dir.mkdir(mode=0o700)
    harness = (
        "import json,sys,time;"
        "sys.stdin.read();"
        "print(json.dumps({'type':'thread.started',"
        "'thread_id':'cancel-thread'}),flush=True);"
        "time.sleep(60)"
    )
    launch = LaunchSpec(
        adapter_id="codex",
        argv=(sys.executable, "-c", harness),
        cwd=str(workspace),
        env={
            "AGENT_TEAM_RUN_ID": run_dir.name,
            "AGENT_TEAM_ROLE_ID": "developer",
            "AGENT_TEAM_TURN_ID": runtime["turn_id"],
            "AGENT_TEAM_RUN_DIR": str(run_dir),
            "AGENT_TEAM_TURN_DIR": str(turn_dir),
            "AGENT_TEAM_CLI": "/bin/true",
        },
        stdin="wait until cancelled\n",
        launch_profile=PROFILE,
        launch_profile_sha256=PROFILE_HASH,
        starts_new_session=True,
    )
    with locked_run(run_dir, exclusive=True):
        runtime["launch_nonce"] = NONCE
        save_runtime(
            turn_dir,
            runtime,
            team=scan_journal(run_dir).team,
        )
        atomic_json(
            process_dir / "launch.json",
            launch.to_json(),
            immutable=True,
        )
    monkeypatch.setattr(
        "agent_team.worker.get_adapter",
        lambda _adapter: _BootstrapAdapter(),
    )
    _configure_supervisor_child_state(
        workspace,
        monkeypatch,
        directory_name="cancel-child-bootstrap",
    )

    async def run_and_cancel() -> tuple[int, dict[str, Any]]:
        task = asyncio.create_task(
            supervise_turn(
                run_dir,
                runtime["turn_id"],
                NONCE,
                launch.content_sha256(),
            )
        )
        authorized = False
        for _ in range(400):
            if task.done():
                result = await task
                raise AssertionError(f"Supervisor exited before cancellation: {result}")
            snapshot_path = process_dir / "supervisor.json"
            if snapshot_path.exists():
                running = False
                with locked_run(run_dir, exclusive=True):
                    snapshot = validate_supervisor(read_json(snapshot_path))
                    if snapshot["state"] == "waiting_authorization" and not authorized:
                        current = load_runtime(
                            turn_dir,
                            team=scan_journal(run_dir).team,
                        )
                        assert _authorize_launch_locked(
                            run_dir,
                            current,
                            snapshot,
                            expected_launch=launch,
                        )
                        authorized = True
                    elif snapshot["state"] == "running":
                        running = True
                if running:
                    if trigger == "cancel":
                        terminal = cancel_run(run_dir)
                    else:
                        result = await asyncio.wait_for(task, timeout=15)
                        terminal = scan_journal(run_dir).tail
                        assert terminal is not None
                        return result, terminal
                    return await asyncio.wait_for(task, timeout=15), terminal
            await asyncio.sleep(0.025)
        task.cancel()
        raise AssertionError("Supervisor never observed active Harness execution")

    result, terminal = asyncio.run(run_and_cancel())
    assert result == 0
    final_snapshot = validate_supervisor(read_json(process_dir / "supervisor.json"))
    assert final_snapshot["state"] == "finished"
    assert final_snapshot["agent_execution_started"] is True
    expected_termination = "cancelled" if trigger == "cancel" else "deadline"
    assert final_snapshot["termination_kind"] == expected_termination
    assert final_snapshot["group_quiescent"] is True
    if trigger == "cancel":
        assert terminal["event_type"] == "cancel"
    else:
        assert terminal["event_type"] == "block"
        assert terminal["block_reason"] == "limit"
        assert terminal["limit_reason"] == "deadline"

    monkeypatch.setattr(
        "agent_team.worker.process_identity_state",
        lambda *_args, **_kwargs: "gone",
    )
    with locked_run(run_dir, exclusive=True):
        current = load_runtime(
            turn_dir,
            team=scan_journal(run_dir).team,
        )
        event = finalize_external_turn_locked(run_dir, current)

    assert event is not None
    assert event["event_id"] == terminal["event_id"]
    finalized = load_runtime(
        turn_dir,
        team=scan_journal(run_dir).team,
    )
    assert finalized["phase"] == "finalized"
    assert finalized["outcome"] == "cancelled"
    assert finalized["termination_kind"] == expected_termination


def test_corrupted_team_only_terminates_verified_managed_processes(
    workspace: Path,
    request_protocol: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_dir, runtime = _external_run(
        workspace,
        request_protocol,
        monkeypatch,
        run_id="at-worker-corrupted-cleanup",
    )
    with locked_run(run_dir, exclusive=True):
        _persist_process_chain(run_dir, runtime)
    event_names_before = sorted(path.name for path in (run_dir / "events").iterdir())
    team_value = read_json(run_dir / "team.json")
    team_value["workspace"] = str(workspace.parent)
    atomic_json(run_dir / "team.json", team_value)

    group_calls: list[tuple[int, int, str]] = []
    process_calls: list[tuple[int, str]] = []
    monkeypatch.setattr(
        "agent_team.management.process_group_exists",
        lambda pgid: pgid == RUNNER_PID,
    )
    monkeypatch.setattr(
        "agent_team.management.process_identity_state",
        lambda *_args, **_kwargs: "match",
    )

    def terminate_group(
        *,
        runner_pid: int,
        runner_pgid: int,
        runner_start_id: str,
        **_kwargs: Any,
    ) -> bool:
        group_calls.append((runner_pid, runner_pgid, runner_start_id))
        return True

    def terminate_process(
        *,
        pid: int,
        start_id: str,
        **_kwargs: Any,
    ) -> bool:
        process_calls.append((pid, start_id))
        return True

    monkeypatch.setattr(
        "agent_team.management.terminate_verified_group",
        terminate_group,
    )
    monkeypatch.setattr(
        "agent_team.management.terminate_verified_process",
        terminate_process,
    )

    with pytest.raises(IntegrityError, match="verified cleanup actions"):
        recover_run(run_dir)

    assert group_calls == [(RUNNER_PID, RUNNER_PID, "runner-start")]
    assert process_calls == [(SUPERVISOR_PID, "supervisor-start")]
    assert sorted(path.name for path in (run_dir / "events").iterdir()) == (
        event_names_before
    )
    assert not (
        run_dir / "turns" / runtime["turn_id"] / "workspace-facts-after.json"
    ).exists()


def test_corrupted_cleanup_retains_supervisor_when_runner_group_is_ambiguous(
    workspace: Path,
    request_protocol: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_dir, runtime = _external_run(
        workspace,
        request_protocol,
        monkeypatch,
        run_id="at-worker-corrupted-ambiguous-group",
    )
    with locked_run(run_dir, exclusive=True):
        _persist_process_chain(run_dir, runtime)
    team_value = read_json(run_dir / "team.json")
    team_value["workspace"] = str(workspace.parent)
    atomic_json(run_dir / "team.json", team_value)

    process_calls: list[tuple[int, str]] = []
    monkeypatch.setattr(
        "agent_team.management.process_group_exists",
        lambda pgid: pgid == RUNNER_PID,
    )
    monkeypatch.setattr(
        "agent_team.management.process_identity_state",
        lambda pid, _start_id, **_kwargs: "gone" if pid == RUNNER_PID else "match",
    )
    monkeypatch.setattr(
        "agent_team.management.terminate_verified_group",
        lambda **_kwargs: pytest.fail(
            "an unverifiable Runner leader must not authorize killpg"
        ),
    )

    def terminate_process(
        *,
        pid: int,
        start_id: str,
        **_kwargs: Any,
    ) -> bool:
        process_calls.append((pid, start_id))
        return True

    monkeypatch.setattr(
        "agent_team.management.terminate_verified_process",
        terminate_process,
    )

    with pytest.raises(
        IntegrityError,
        match="retained-for-runner-cleanup",
    ):
        recover_run(run_dir)

    assert (SUPERVISOR_PID, "supervisor-start") not in process_calls


def test_repeated_start_with_mismatched_owner_cleans_without_rebuilding_workers(
    workspace: Path,
    request_protocol: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_dir, _runtime = _external_run(
        workspace,
        request_protocol,
        monkeypatch,
        run_id="at-worker-owner-corrupted-cleanup",
    )
    _, _, owner_path = state_paths(workspace)
    owner = read_json(owner_path)
    owner["run_id"] = "at-other-owner"
    atomic_json(owner_path, owner)
    event_names_before = sorted(path.name for path in (run_dir / "events").iterdir())
    monkeypatch.setattr(
        "agent_team.management.ensure_workers",
        lambda *_args, **_kwargs: pytest.fail(
            "corrupted ownership must not rebuild Workers"
        ),
    )

    with pytest.raises(IntegrityError, match="exact ownership"):
        start_run(run_dir)

    assert read_json(owner_path)["run_id"] == "at-other-owner"
    assert sorted(path.name for path in (run_dir / "events").iterdir()) == (
        event_names_before
    )
