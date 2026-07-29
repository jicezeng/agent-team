from __future__ import annotations

import asyncio
import base64
import json
from pathlib import Path

from agent_team.supervisor import StreamRecorder, _base_snapshot
from agent_team.util import read_json


class RecordingAdapter:
    def __init__(self) -> None:
        self.records = []

    def parse_stream_record(self, record):
        self.records.append(record)
        return None


def _decode_outer(value: dict) -> bytes:
    if value["encoding"] == "utf-8":
        return value["data"].encode("utf-8")
    return base64.b64decode(value["data"])


def test_stream_recorder_preserves_chunks_and_frames_complete_lines(
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "run"
    recorder = StreamRecorder(
        run_dir=run_dir,
        turn_id="turn-0001",
        adapter_id="codex",
        snapshot=_base_snapshot("turn-0001", "nonce"),
    )
    adapter = RecordingAdapter()
    recorder.adapter = adapter

    async def exercise() -> None:
        await recorder.record("stdout", b'{"one":')
        await recorder.record("stderr", b"\xffdiagnostic\n")
        await recorder.record(
            "stdout",
            b'1}\n{"two":2}\nunterminated',
        )
        await recorder.close()

    asyncio.run(exercise())

    stream_path = (
        run_dir / "turns" / "turn-0001" / "process" / "stream.jsonl"
    )
    outer = [
        json.loads(line)
        for line in stream_path.read_text(encoding="utf-8").splitlines()
    ]
    assert [item["seq"] for item in outer] == [1, 2, 3]
    assert b"".join(
        _decode_outer(item) for item in outer if item["source"] == "stdout"
    ) == b'{"one":1}\n{"two":2}\nunterminated'
    assert b"".join(
        _decode_outer(item) for item in outer if item["source"] == "stderr"
    ) == b"\xffdiagnostic\n"
    assert (
        run_dir / "turns" / "turn-0001" / "process" / "stderr.log"
    ).read_bytes() == b"\xffdiagnostic\n"

    assert len(adapter.records) == 3
    assert adapter.records[0].source == "stderr"
    assert adapter.records[0].encoding == "base64"
    assert adapter.records[0].first_seq == 2
    assert adapter.records[0].last_seq == 2
    assert [record.data for record in adapter.records[1:]] == [
        '{"one":1}\n',
        '{"two":2}\n',
    ]
    assert adapter.records[1].first_seq == 1
    assert adapter.records[1].last_seq == 3
    assert adapter.records[2].first_seq == 3
    assert adapter.records[2].last_seq == 3


def test_stream_recorder_detects_path_replacement_without_redirecting_fd(
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "run"
    recorder = StreamRecorder(
        run_dir=run_dir,
        turn_id="turn-0001",
        adapter_id="codex",
        snapshot=_base_snapshot("turn-0001", "nonce"),
    )
    recorder.adapter = RecordingAdapter()
    stream_path = recorder.stream_path
    held_inode_path = stream_path.with_name("held-stream.jsonl")
    target = tmp_path / "redirect-target"
    target.write_bytes(b"do not modify\n")
    stream_path.rename(held_inode_path)
    stream_path.symlink_to(target)

    async def exercise() -> None:
        await recorder.record("stdout", b"raw output\n")
        assert not recorder.stream_path_is_original()
        await recorder.close()

    asyncio.run(exercise())

    assert target.read_bytes() == b"do not modify\n"
    assert b"raw output" in held_inode_path.read_bytes()


def test_stream_recorder_caps_retained_bytes_and_records_capture_counts(
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "run"
    recorder = StreamRecorder(
        run_dir=run_dir,
        turn_id="turn-0001",
        adapter_id="codex",
        snapshot=_base_snapshot("turn-0001", "nonce"),
        max_bytes=8,
    )
    recorder.adapter = RecordingAdapter()

    async def exercise() -> None:
        await recorder.record("stdout", b"123456")
        await recorder.record("stderr", b"7890")
        await recorder.close()

    asyncio.run(exercise())

    process_dir = run_dir / "turns" / "turn-0001" / "process"
    outer = [
        json.loads(line)
        for line in (process_dir / "stream.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert b"".join(_decode_outer(item) for item in outer) == b"12345678"
    assert (process_dir / "stderr.log").read_bytes() == b"78"
    capture = read_json(process_dir / "capture.json")
    assert capture["source_bytes"] == 10
    assert capture["stored_source_bytes"] == 8
    assert capture["dropped_source_bytes"] == 2
    assert capture["chunks_observed"] == 2
    assert capture["chunks_stored"] == 2
    assert capture["truncated"] is True
