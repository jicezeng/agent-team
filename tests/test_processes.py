from __future__ import annotations

import os
import signal
import sys

import pytest

from agent_team import processes
from agent_team.processes import _linux_proc_start_time


def test_linux_proc_start_time_handles_spaces_and_parentheses_in_comm() -> None:
    fields_3_through_22 = [
        b"S",
        b"1",
        b"2",
        b"3",
        b"4",
        b"5",
        b"6",
        b"7",
        b"8",
        b"9",
        b"10",
        b"11",
        b"12",
        b"13",
        b"14",
        b"15",
        b"16",
        b"17",
        b"18",
        b"987654",
    ]
    raw = b"123 (worker name) with paren) " + b" ".join(fields_3_through_22)

    assert _linux_proc_start_time(raw) == "987654"


@pytest.mark.skipif(sys.platform != "darwin", reason="Darwin process API")
def test_darwin_process_identity_is_microsecond_precision_and_legacy_compatible() -> None:
    current = processes.process_start_id(os.getpid())
    legacy = processes._darwin_legacy_start_id(os.getpid())

    assert current is not None
    assert current.startswith("darwin-v2:")
    assert len(current.rsplit(":", 1)[1]) == 6
    assert legacy is not None
    assert processes.process_identity_state(os.getpid(), legacy) == "match"


def test_terminate_verified_process_stops_only_a_matching_identity(
    monkeypatch,
) -> None:
    states = iter(["match", "match", "gone"])
    sent: list[tuple[int, signal.Signals]] = []
    monkeypatch.setattr(
        processes,
        "process_identity_state",
        lambda *_args, **_kwargs: next(states),
    )
    monkeypatch.setattr(
        processes.os,
        "kill",
        lambda pid, sig: sent.append((pid, sig)),
    )

    assert processes.terminate_verified_process(
        pid=1234,
        start_id="stable-start",
    )
    assert sent == [(1234, signal.SIGTERM)]


def test_terminate_verified_process_refuses_unknown_identity(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        processes,
        "process_identity_state",
        lambda *_args, **_kwargs: "unknown",
    )
    monkeypatch.setattr(
        processes.os,
        "kill",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("unknown identity must not be signalled")
        ),
    )

    assert not processes.terminate_verified_process(
        pid=1234,
        start_id="stable-start",
    )


def test_process_identity_rejects_out_of_range_os_ids() -> None:
    invalid = processes.MAX_PROCESS_ID + 1

    assert processes.process_start_id(invalid) is None
    assert processes.process_identity_state(invalid, "stable-start") == "invalid"
    assert processes.pid_exists(invalid) is False
    assert processes.process_group_exists(invalid) is True
