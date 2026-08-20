from __future__ import annotations

from pathlib import Path

from agent_team import assets

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


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


def test_shared_integration_references_are_byte_identical() -> None:
    codex_root = REPOSITORY_ROOT / "skills" / "codex" / "agent-team"
    claude_root = (
        REPOSITORY_ROOT
        / "plugins"
        / "claude-code"
        / "agent-team"
        / "skills"
        / "agent-team"
    )
    opencode_root = REPOSITORY_ROOT / "skills" / "opencode" / "agent-team"

    for relative in (
        "references/coordination.md",
        "references/protocol-template.md",
    ):
        assert (codex_root / relative).read_bytes() == (
            claude_root / relative
        ).read_bytes()
        assert (codex_root / relative).read_bytes() == (
            opencode_root / relative
        ).read_bytes()


def test_dsh_origin_bundle_exposes_only_the_trusted_agent_team_cli() -> None:
    root = (
        REPOSITORY_ROOT
        / "plugins"
        / "deepseek-harness"
        / "agent-team-origin"
    )
    source = (root / "lib" / "index.js").read_text(encoding="utf-8")
    manifest = (root / "package.json").read_text(encoding="utf-8")
    patch = (root / "cordis.patch.yml").read_text(encoding="utf-8")

    assert '"name": "@agent-team/dsh-origin"' in manifest
    assert "name: '@agent-team/dsh-origin'" in patch
    assert "name: 'agent_team_cli'" in source
    assert "ctx.credentials.resolve" in source
    assert "ctx.credentials.resolve('DEEPSEEK_API_KEY')" in source
    assert "argv: [executable, ...args]" in source
    assert "from '" not in source
    assert "child_process" not in source


def test_harness_skill_variants_have_only_the_intended_origin_differences() -> None:
    codex_skill = (
        REPOSITORY_ROOT / "skills" / "codex" / "agent-team" / "SKILL.md"
    ).read_text(encoding="utf-8")
    claude_skill = (
        REPOSITORY_ROOT
        / "plugins"
        / "claude-code"
        / "agent-team"
        / "skills"
        / "agent-team"
        / "SKILL.md"
    ).read_text(encoding="utf-8")

    assert codex_skill.count("shell invocation") == 1
    assert "Bash invocation" not in codex_skill
    expected = codex_skill.replace(
        "shell invocation",
        "Bash invocation",
        1,
    )
    expected = expected.replace(
        """   select the explicit Origin metadata from the managed shell, then run:

```bash
if [ "${DSH_SHELL:-}" = "1" ]; then
  origin_harness=deepseek-harness
else
  origin_harness=codex
fi
""",
        """   then run:

```bash
""",
        1,
    )
    expected = expected.replace(
        """  --origin-harness "$origin_harness"
"<absolute-agent-team-cli>" start <run-id> --confirm-full-access
```

`DSH_SHELL=1` selects `deepseek-harness`; every other value selects `codex`.
This branch records Origin metadata only and grants no permission.
""",
        """  --origin-harness claude-code
"<absolute-agent-team-cli>" start <run-id> --confirm-full-access
```
""",
        1,
    )

    assert claude_skill == expected
