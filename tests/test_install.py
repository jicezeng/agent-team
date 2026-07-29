from __future__ import annotations

import stat
from pathlib import Path

from agent_team import cli


def test_install_replaces_exact_integration_trees_with_private_modes(
    tmp_path: Path,
    monkeypatch,
) -> None:
    codex_source = tmp_path / "source-codex"
    plugin_source = tmp_path / "source-plugin"
    codex_source.mkdir()
    plugin_source.mkdir()
    (codex_source / "SKILL.md").write_text("codex skill\n", encoding="utf-8")
    (plugin_source / "skills").mkdir()
    (plugin_source / "skills" / "SKILL.md").write_text(
        "claude plugin\n",
        encoding="utf-8",
    )
    codex_target = tmp_path / "installed" / "codex"
    plugin_target = tmp_path / "installed" / "plugin"
    codex_target.mkdir(parents=True)
    plugin_target.mkdir()
    (codex_target / "stale.txt").write_text("stale\n", encoding="utf-8")
    (plugin_target / "stale.txt").write_text("stale\n", encoding="utf-8")
    monkeypatch.setattr(cli, "codex_skill_source", lambda: codex_source)
    monkeypatch.setattr(cli, "claude_plugin_source", lambda: plugin_source)
    monkeypatch.setattr(cli, "installed_codex_skill", lambda: codex_target)
    monkeypatch.setattr(cli, "installed_claude_plugin", lambda: plugin_target)

    result = cli._install_skill()

    assert result["code"] == "INTEGRATIONS_INSTALLED"
    assert sorted(
        path.relative_to(codex_target).as_posix()
        for path in codex_target.rglob("*")
        if path.is_file()
    ) == ["SKILL.md"]
    assert sorted(
        path.relative_to(plugin_target).as_posix()
        for path in plugin_target.rglob("*")
        if path.is_file()
    ) == ["skills/SKILL.md"]
    for root in (codex_target, plugin_target):
        for path in [root, *root.rglob("*")]:
            expected = 0o700 if path.is_dir() else 0o600
            assert stat.S_IMODE(path.stat().st_mode) == expected
