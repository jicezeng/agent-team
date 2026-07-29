from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .processes import current_identity
from .util import ensure_dir, rfc3339, write_all


class WorkerLogger:
    def __init__(self, path: Path, *, run_id: str, role_id: str) -> None:
        ensure_dir(path.parent)
        flags = (
            os.O_WRONLY
            | os.O_CREAT
            | os.O_APPEND
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0)
        )
        self.fd = os.open(path, flags, 0o600)
        self.run_id = run_id
        self.role_id = role_id
        self.identity = current_identity()
        self.seq = 0

    def close(self) -> None:
        os.fsync(self.fd)
        os.close(self.fd)

    def write(
        self,
        level: str,
        message_code: str,
        message: str,
        *,
        turn_id: str | None = None,
        event_id: str | None = None,
    ) -> None:
        self.seq += 1
        record: dict[str, Any] = {
            "schema_version": 1,
            "observed_at": rfc3339(),
            "producer_seq": self.seq,
            "level": level,
            "component": "worker",
            "message_code": message_code,
            "run_id": self.run_id,
            "role_id": self.role_id,
            "turn_id": turn_id,
            "event_id": event_id,
            "pid": self.identity.pid,
            "process_start_id": self.identity.start_id,
            "message": message,
        }
        line = (
            json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
        ).encode("utf-8")
        write_all(self.fd, line)
        os.fsync(self.fd)
        visible = "".join(
            char
            if char in "\n\t" or (ord(char) >= 0x20 and not 0x7F <= ord(char) <= 0x9F)
            else f"\\x{ord(char):02x}"
            for char in message
        )
        print(f"[{level}] {message_code}: {visible}", flush=True)
