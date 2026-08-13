from __future__ import annotations

import asyncio
import fcntl
import os
import pty
import termios
import tty
from types import SimpleNamespace

import pytest

from agent_team.supervisor import (
    _relay_terminal_input,
)


def test_interactive_terminal_input_is_raw_and_tty_state_is_restored(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_master, source_slave = pty.openpty()
    destination_master, destination_slave = pty.openpty()
    original_termios = termios.tcgetattr(source_slave)
    original_flags = fcntl.fcntl(source_slave, fcntl.F_GETFL)
    tty.setraw(destination_slave, when=termios.TCSANOW)
    os.set_blocking(destination_slave, False)
    monkeypatch.setattr(
        "agent_team.supervisor.sys",
        SimpleNamespace(stdin=SimpleNamespace(fileno=lambda: source_slave)),
    )

    async def exercise() -> bytes:
        task = asyncio.create_task(_relay_terminal_input(destination_master))
        try:
            for _ in range(100):
                current = termios.tcgetattr(source_slave)
                if not current[3] & termios.ICANON and not current[3] & termios.ECHO:
                    break
                await asyncio.sleep(0.01)
            else:
                raise AssertionError("terminal input relay did not enter raw mode")
            payload = b"\r\x1b[A\x03"
            os.write(source_master, payload)
            received = b""
            for _ in range(100):
                try:
                    received += os.read(destination_slave, 4096)
                except BlockingIOError:
                    pass
                if len(received) >= len(payload):
                    return received
                await asyncio.sleep(0.01)
            raise AssertionError(f"terminal input relay returned {received!r}")
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    try:
        assert asyncio.run(exercise()) == b"\r\x1b[A\x03"
        restored_termios = termios.tcgetattr(source_slave)
        # Darwin may add the transient PENDIN state bit when tcsetattr restores
        # canonical input. It is not a configured terminal mode.
        pendin = getattr(termios, "PENDIN", 0)
        restored_termios[3] &= ~pendin
        original_termios[3] &= ~pendin
        assert restored_termios == original_termios
        assert fcntl.fcntl(source_slave, fcntl.F_GETFL) == original_flags
    finally:
        for fd in (
            source_master,
            source_slave,
            destination_master,
            destination_slave,
        ):
            os.close(fd)
