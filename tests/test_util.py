from __future__ import annotations

import stat
from pathlib import Path

import pytest

from agent_team.errors import IntegrityError
from agent_team.util import ensure_dir, parse_rfc3339, read_json, set_private_umask


def permission_bits(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def test_ensure_dir_does_not_rechmod_existing_directory(tmp_path: Path) -> None:
    existing = tmp_path / "workspace"
    existing.mkdir(mode=0o755)
    existing.chmod(0o755)

    ensure_dir(existing)

    assert permission_bits(existing) == 0o755


def test_ensure_dir_sets_mode_on_new_directory(tmp_path: Path) -> None:
    created = tmp_path / "private"

    ensure_dir(created, 0o700)

    assert permission_bits(created) == 0o700


def test_managed_process_umask_is_private(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: list[int] = []
    monkeypatch.setattr("agent_team.util.os.umask", observed.append)

    set_private_umask()

    assert observed == [0o077]


@pytest.mark.parametrize("value", [None, 1, True, [], {}])
def test_parse_rfc3339_rejects_non_string_values(value: object) -> None:
    with pytest.raises(IntegrityError, match="invalid RFC 3339 timestamp"):
        parse_rfc3339(value)  # type: ignore[arg-type]


def test_read_json_rejects_duplicate_object_keys(tmp_path: Path) -> None:
    path = tmp_path / "duplicate.json"
    path.write_text('{"phase":"running","phase":"finalized"}', encoding="utf-8")

    with pytest.raises(IntegrityError, match="invalid JSON file"):
        read_json(path)
