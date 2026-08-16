from __future__ import annotations

import stat
from pathlib import Path

import pytest

from agent_team import cli
from agent_team.assets import installed_dsh_skill, resolved_dsh_home
from agent_team.errors import InvalidArgument


def test_install_replaces_exact_integration_trees_with_private_modes(
    tmp_path: Path,
    monkeypatch,
) -> None:
    codex_source = tmp_path / "source-codex"
    plugin_source = tmp_path / "source-plugin"
    opencode_source = tmp_path / "source-opencode"
    tui_source = tmp_path / "source-dsh-tui"
    codex_source.mkdir()
    plugin_source.mkdir()
    opencode_source.mkdir()
    tui_source.mkdir()
    (codex_source / "SKILL.md").write_text("codex skill\n", encoding="utf-8")
    (plugin_source / "skills").mkdir()
    (plugin_source / "skills" / "SKILL.md").write_text(
        "claude plugin\n",
        encoding="utf-8",
    )
    (opencode_source / "SKILL.md").write_text(
        "opencode skill\n",
        encoding="utf-8",
    )
    codex_target = tmp_path / "installed" / "codex"
    plugin_target = tmp_path / "installed" / "plugin"
    opencode_target = tmp_path / "installed" / "opencode"
    dsh_target = tmp_path / "installed" / "dsh"
    codex_target.mkdir(parents=True)
    plugin_target.mkdir()
    opencode_target.mkdir()
    dsh_target.mkdir()
    (codex_target / "stale.txt").write_text("stale\n", encoding="utf-8")
    (plugin_target / "stale.txt").write_text("stale\n", encoding="utf-8")
    (opencode_target / "stale.txt").write_text("stale\n", encoding="utf-8")
    (dsh_target / "stale.txt").write_text("stale\n", encoding="utf-8")
    monkeypatch.setattr(cli, "codex_skill_source", lambda: codex_source)
    monkeypatch.setattr(cli, "claude_plugin_source", lambda: plugin_source)
    monkeypatch.setattr(cli, "installed_codex_skill", lambda: codex_target)
    monkeypatch.setattr(cli, "installed_claude_plugin", lambda: plugin_target)
    monkeypatch.setattr(cli, "opencode_skill_source", lambda: opencode_source)
    monkeypatch.setattr(cli, "dsh_tui_source", lambda: tui_source)
    monkeypatch.setattr(cli, "installed_opencode_skill", lambda: opencode_target)
    monkeypatch.setattr(cli, "installed_dsh_skill", lambda: dsh_target)
    monkeypatch.setattr(
        cli,
        "install_managed_dsh_runtime",
        lambda: {
            "installed": False,
            "root": "/managed/dsh",
            "version": "test",
        },
    )

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
    assert sorted(
        path.relative_to(opencode_target).as_posix()
        for path in opencode_target.rglob("*")
        if path.is_file()
    ) == ["SKILL.md"]
    assert sorted(
        path.relative_to(dsh_target).as_posix()
        for path in dsh_target.rglob("*")
        if path.is_file()
    ) == ["SKILL.md"]
    assert (dsh_target / "SKILL.md").read_bytes() == (
        codex_target / "SKILL.md"
    ).read_bytes()
    assert result["deepseek_harness"] == {
        "source": str(codex_source),
        "target": str(dsh_target),
    }
    assert result["deepseek_harness_runtime"]["root"] == "/managed/dsh"
    assert result["deepseek_harness_tui"]["source"] == str(tui_source)
    for root in (codex_target, plugin_target, opencode_target, dsh_target):
        for path in [root, *root.rglob("*")]:
            expected = 0o700 if path.is_dir() else 0o600
            assert stat.S_IMODE(path.stat().st_mode) == expected


@pytest.mark.parametrize("value", [None, "", "  \t"])
def test_dsh_home_defaults_to_current_home(
    tmp_path: Path,
    monkeypatch,
    value: str | None,
) -> None:
    home = tmp_path / "current-home"
    monkeypatch.setenv("HOME", str(home))
    if value is None:
        monkeypatch.delenv("DSH_HOME", raising=False)
    else:
        monkeypatch.setenv("DSH_HOME", value)

    assert resolved_dsh_home() == home / ".dsh"
    assert installed_dsh_skill() == home / ".dsh" / "skills" / "agent-team"


@pytest.mark.parametrize(
    ("value", "suffix"),
    [
        ("~", "."),
        ("~/custom/../dsh-home", "dsh-home"),
    ],
)
def test_dsh_home_expands_only_current_user_tilde(
    tmp_path: Path,
    monkeypatch,
    value: str,
    suffix: str,
) -> None:
    home = tmp_path / "current-home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("DSH_HOME", value)

    expected = home if suffix == "." else home / suffix
    assert resolved_dsh_home() == expected


def test_dsh_home_accepts_and_lexically_normalizes_absolute_path(
    tmp_path: Path,
    monkeypatch,
) -> None:
    configured = tmp_path / "one" / ".." / "dsh-home"
    monkeypatch.setenv("DSH_HOME", str(configured))

    assert resolved_dsh_home() == tmp_path / "dsh-home"


def test_dsh_home_lexical_normalization_does_not_resolve_symlinks(
    tmp_path: Path,
    monkeypatch,
) -> None:
    actual = tmp_path / "actual"
    actual.mkdir()
    linked = tmp_path / "linked"
    linked.symlink_to(actual, target_is_directory=True)
    monkeypatch.setenv("DSH_HOME", str(linked / "child" / ".."))

    assert resolved_dsh_home() == linked


@pytest.mark.parametrize("value", ["relative/dsh", "~someone/.dsh"])
def test_dsh_home_rejects_non_absolute_values(
    monkeypatch,
    value: str,
) -> None:
    monkeypatch.setenv("DSH_HOME", value)

    with pytest.raises(InvalidArgument, match="DSH_HOME must resolve"):
        resolved_dsh_home()


def test_install_resolves_invalid_dsh_home_before_mutating_other_targets(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "SKILL.md").write_text("new\n", encoding="utf-8")
    targets = [tmp_path / name for name in ("codex", "claude", "opencode")]
    for target in targets:
        target.mkdir()
        (target / "keep.txt").write_text("keep\n", encoding="utf-8")
    monkeypatch.setattr(cli, "codex_skill_source", lambda: source)
    monkeypatch.setattr(cli, "claude_plugin_source", lambda: source)
    monkeypatch.setattr(cli, "opencode_skill_source", lambda: source)
    monkeypatch.setattr(cli, "installed_codex_skill", lambda: targets[0])
    monkeypatch.setattr(cli, "installed_claude_plugin", lambda: targets[1])
    monkeypatch.setattr(cli, "installed_opencode_skill", lambda: targets[2])
    monkeypatch.setenv("DSH_HOME", "relative/dsh")

    with pytest.raises(InvalidArgument, match="DSH_HOME must resolve"):
        cli._install_skill()

    for target in targets:
        assert (target / "keep.txt").read_text(encoding="utf-8") == "keep\n"
