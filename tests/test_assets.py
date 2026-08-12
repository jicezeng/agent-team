from __future__ import annotations

from pathlib import Path

from agent_team import assets


def test_effective_cli_prefers_current_interpreter_sibling_over_path(
    tmp_path: Path,
    monkeypatch,
) -> None:
    environment_bin = tmp_path / "environment" / "bin"
    environment_bin.mkdir(parents=True)
    interpreter = environment_bin / "python"
    interpreter.write_text("", encoding="utf-8")
    sibling_cli = environment_bin / "agent-team"
    sibling_cli.write_text("", encoding="utf-8")
    path_cli = tmp_path / "user-tools" / "bin" / "agent-team"
    path_cli.parent.mkdir(parents=True)
    path_cli.write_text("", encoding="utf-8")
    monkeypatch.setattr(assets.sys, "executable", str(interpreter))
    monkeypatch.setattr(assets.shutil, "which", lambda _name: str(path_cli))

    assert assets.effective_agent_team_cli() == sibling_cli.resolve()


def test_effective_cli_falls_back_to_path_without_interpreter_sibling(
    tmp_path: Path,
    monkeypatch,
) -> None:
    environment_bin = tmp_path / "environment" / "bin"
    environment_bin.mkdir(parents=True)
    interpreter = environment_bin / "python"
    interpreter.write_text("", encoding="utf-8")
    path_cli = tmp_path / "user-tools" / "bin" / "agent-team"
    path_cli.parent.mkdir(parents=True)
    path_cli.write_text("", encoding="utf-8")
    monkeypatch.setattr(assets.sys, "executable", str(interpreter))
    monkeypatch.setattr(assets.shutil, "which", lambda _name: str(path_cli))

    assert assets.effective_agent_team_cli() == path_cli.resolve()
