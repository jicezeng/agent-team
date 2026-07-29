from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import Path

import pytest

from agent_team.bootstrap import initialize_run, start_run
from agent_team.config import Role, make_team
from agent_team.errors import AgentTeamError
from agent_team.gitfacts import (
    capture_workspace_facts,
    validate_workspace_facts,
)
from agent_team.journal import scan_journal
from agent_team.state import read_owner


def _initialize(
    workspace: Path,
    request_protocol: tuple[Path, Path],
) -> Path:
    request, protocol = request_protocol
    team = make_team(
        run_id="at-test-facts",
        workspace=workspace,
        origin_harness="codex",
        roles={"reviewer": Role("reviewer", "origin")},
        initial_role="reviewer",
        max_turns=2,
        max_wall_time_seconds=120,
    )
    return initialize_run(team=team, request_path=request, protocol_path=protocol)


def test_run_store_and_ignored_files_do_not_change_business_fingerprint(
    workspace: Path,
    request_protocol: tuple[Path, Path],
) -> None:
    (workspace / ".gitignore").write_text("cache/\n", encoding="utf-8")
    _initialize(workspace, request_protocol)
    before = capture_workspace_facts(workspace, turn_id="turn-a", boundary="before")
    expected_state = hashlib.sha256(
        (before["git_head"] or "-").encode("ascii")
        + before["git_status_sha256"].encode("ascii")
        + before["business_tree_sha256"].encode("ascii")
    ).hexdigest()
    cache = workspace / "cache"
    cache.mkdir()
    (cache / "result.bin").write_bytes(b"\x00\xfftemporary")
    os.mkfifo(cache / "ignored.fifo")
    (workspace / ".agent-team" / "diagnostic.tmp").write_text("runtime change")
    after = capture_workspace_facts(workspace, turn_id="turn-b", boundary="after")

    assert before["workspace_state_sha256"] == after["workspace_state_sha256"]
    assert before["workspace_state_sha256"] == expected_state
    assert before["snapshot_scope"] == "git_visible"


def test_workspace_facts_reject_internally_inconsistent_state_hash(
    workspace: Path,
    request_protocol: tuple[Path, Path],
) -> None:
    _initialize(workspace, request_protocol)
    facts = capture_workspace_facts(
        workspace,
        turn_id="turn-a",
        boundary="before",
    )
    facts["workspace_state_sha256"] = "0" * 64

    with pytest.raises(AgentTeamError, match="internally inconsistent"):
        validate_workspace_facts(facts)


def test_workspace_facts_reject_unhashable_boundary(
    workspace: Path,
    request_protocol: tuple[Path, Path],
) -> None:
    _initialize(workspace, request_protocol)
    facts = capture_workspace_facts(
        workspace,
        turn_id="turn-a",
        boundary="before",
    )
    facts["boundary"] = []

    with pytest.raises(AgentTeamError, match="invalid boundary"):
        validate_workspace_facts(facts)


def test_tracked_deletion_and_file_to_directory_are_stable_missing_records(
    workspace: Path,
    request_protocol: tuple[Path, Path],
) -> None:
    _initialize(workspace, request_protocol)
    (workspace / "tracked.txt").unlink()
    deleted = capture_workspace_facts(workspace, turn_id="turn-a", boundary="before")
    (workspace / "tracked.txt").mkdir()
    (workspace / "tracked.txt" / "new.txt").write_text("new\n", encoding="utf-8")
    replaced = capture_workspace_facts(workspace, turn_id="turn-b", boundary="after")

    assert deleted["tracked_path_count"] == 1
    assert replaced["tracked_path_count"] == 1
    assert replaced["untracked_path_count"] == 1
    assert deleted["business_tree_sha256"] != replaced["business_tree_sha256"]


def test_unsupported_untracked_entry_fails_without_partial_facts(
    workspace: Path,
    request_protocol: tuple[Path, Path],
) -> None:
    _initialize(workspace, request_protocol)
    fifo = workspace / "unsupported.fifo"
    os.mkfifo(fifo)

    with pytest.raises(AgentTeamError) as captured:
        capture_workspace_facts(
            workspace,
            turn_id="turn-a",
            boundary="before",
        )

    assert captured.value.code == "WORKSPACE_SNAPSHOT_FAILED"
    assert "unsupported filesystem entry" in captured.value.message


def test_nested_untracked_repository_fails_snapshot(
    workspace: Path,
    request_protocol: tuple[Path, Path],
) -> None:
    _initialize(workspace, request_protocol)
    nested = workspace / "nested"
    nested.mkdir()
    subprocess.run(
        ["git", "-C", str(nested), "init", "-q"],
        check=True,
    )
    (nested / "file.txt").write_text("nested\n", encoding="utf-8")

    with pytest.raises(AgentTeamError) as captured:
        capture_workspace_facts(
            workspace,
            turn_id="turn-a",
            boundary="before",
        )

    assert captured.value.code == "WORKSPACE_SNAPSHOT_FAILED"


def test_failed_initial_snapshot_releases_owner_and_same_run_can_retry(
    workspace: Path,
    request_protocol: tuple[Path, Path],
) -> None:
    run_dir = _initialize(workspace, request_protocol)
    fifo = workspace / "unsupported.fifo"
    os.mkfifo(fifo)

    with pytest.raises(AgentTeamError) as captured:
        start_run(run_dir)

    assert captured.value.code == "WORKSPACE_SNAPSHOT_FAILED"
    assert read_owner(workspace) is None
    assert scan_journal(run_dir).status == "UNSTARTED"

    fifo.unlink()
    result = start_run(run_dir)

    assert result["status"] == "RUNNING"
    assert read_owner(workspace)["run_id"] == "at-test-facts"
    assert scan_journal(run_dir).status == "RUNNING"


def test_unborn_repository_uses_null_head_without_masking_git_errors(
    workspace: Path,
    request_protocol: tuple[Path, Path],
) -> None:
    _initialize(workspace, request_protocol)
    subprocess.run(
        ["git", "-C", str(workspace), "update-ref", "-d", "refs/heads/main"],
        check=True,
    )

    facts = capture_workspace_facts(
        workspace,
        turn_id="turn-a",
        boundary="before",
    )

    assert facts["git_head"] is None
    assert facts["workspace_state_sha256"] == hashlib.sha256(
        b"-"
        + facts["git_status_sha256"].encode("ascii")
        + facts["business_tree_sha256"].encode("ascii")
    ).hexdigest()
