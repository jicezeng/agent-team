from __future__ import annotations

import json
import os
import stat
import subprocess
import tempfile
import uuid
from pathlib import Path

from agent_team.assets import effective_agent_team_cli, effective_claude_plugin
from agent_team.config import (
    CLAUDE_MODEL_PROVIDERS,
    CLAUDE_PROVIDER_CREDENTIAL_ENVIRONMENTS,
    CLAUDE_REASONING_EFFORTS,
    claude_model_provider_config_error,
    valid_model_id,
)
from agent_team.errors import AgentTeamError, IntegrityError, InvalidArgument
from agent_team.state import fixed_state_dir
from agent_team.util import (
    atomic_json,
    atomic_write,
    canonical_json_bytes,
    ensure_dir,
    path_entry_exists,
    read_json,
    read_regular,
    sha256_bytes,
)

from .base import (
    AdapterEvidence,
    CapabilityReport,
    HarnessAdapter,
    HarnessLaunchOptions,
    LaunchSpec,
    NormalizedTraceEvent,
    StreamRecord,
    TurnLaunchContext,
    workspace_from_run_dir,
)

_ANTHROPIC_API_BASE_URL = "https://api.anthropic.com"
_CLAUDE_PROVIDER_FLAGS = {
    "bedrock": "CLAUDE_CODE_USE_BEDROCK",
    "vertex": "CLAUDE_CODE_USE_VERTEX",
    "foundry": "CLAUDE_CODE_USE_FOUNDRY",
}
_CLAUDE_ROUTE_FLAGS = (
    "CLAUDE_CODE_USE_BEDROCK",
    "CLAUDE_CODE_USE_VERTEX",
    "CLAUDE_CODE_USE_FOUNDRY",
    "CLAUDE_CODE_USE_MANTLE",
)


def claude_internal_tmpdir() -> Path:
    base = Path(os.environ.get("CLAUDE_CODE_TMPDIR", "/tmp")).expanduser()
    if not base.is_absolute():
        raise AgentTeamError(
            "CLAUDE_CODE_TMPDIR_INVALID",
            "CLAUDE_CODE_TMPDIR must be an absolute path",
        )
    if os.name == "nt":
        suffix = "claude"
    else:
        getuid = getattr(os, "getuid", lambda: 0)
        suffix = f"claude-{getuid()}"
    return base / suffix


class ClaudeCodeAdapter(HarnessAdapter):
    adapter_id = "claude-code"
    executable_name = "claude"

    def executable_version(self) -> str:
        # Claude Code writes debug bookkeeping under CLAUDE_CONFIG_DIR even for
        # ``--version``. Profile validation may run from another Harness's
        # sandbox while staging a cross-Harness handoff, where the user's real
        # ~/.claude directory is intentionally not writable. Keep this
        # non-authenticating probe isolated in a private, short-lived directory
        # so capability checks do not mutate user configuration or depend on
        # the sender Harness's filesystem permissions.
        with tempfile.TemporaryDirectory(
            prefix="agent-team-claude-version-"
        ) as config_dir:
            env = os.environ.copy()
            env["CLAUDE_CONFIG_DIR"] = config_dir
            result = subprocess.run(
                [str(self.executable()), "--version"],
                capture_output=True,
                text=True,
                check=False,
                timeout=10,
                env=env,
            )
        if result.returncode != 0:
            raise AgentTeamError(
                "HARNESS_PROBE_FAILED",
                f"{self.executable_name} --version failed: "
                f"{result.stderr.strip()}",
            )
        return (result.stdout or result.stderr).strip()

    @staticmethod
    def _permission_mapping(profile: str) -> list[str]:
        cli = str(effective_agent_team_cli())
        formal_commands = [
            f"{cli} handoff *",
            f"{cli} complete *",
            f"{cli} block *",
        ]
        if profile in {"default", "trusted-workspace"}:
            sandbox = {
                "enabled": True,
                "failIfUnavailable": True,
                "autoAllowBashIfSandboxed": True,
                "allowUnsandboxedCommands": False,
                "excludedCommands": formal_commands,
                "filesystem": {
                    "allowWrite": [str(claude_internal_tmpdir())],
                },
            }
            # Claude's OS sandbox applies only to Bash and its child processes.
            # Keep built-in Edit/Write tools inside the working-directory scope
            # enforced by acceptEdits for every workspace-contained profile.
            permission_args = ["--permission-mode", "acceptEdits"]
        elif profile == "full-access":
            sandbox = {"enabled": False}
            # Claude documents skipDangerousModePermissionPrompt for the
            # dedicated dangerous-mode flag (or a persistent defaultMode), not
            # for the generic --permission-mode override. Use the equivalent
            # one-shot flag so the Run-scoped confirmation actually suppresses
            # Claude's secondary interactive warning without persisting a
            # user-level acceptance bit.
            permission_args = ["--dangerously-skip-permissions"]
        else:
            raise AgentTeamError(
                "UNKNOWN_LAUNCH_PROFILE",
                f"claude-code profile {profile!r} is not supported",
            )
        settings_payload: dict[str, object] = {"sandbox": sandbox}
        if profile == "full-access":
            # Agent-Team has already obtained and immutably recorded the
            # Run-scoped YOLO confirmation before this mapping can launch.
            # Reuse that decision so Claude does not ask for a second
            # dangerous-mode confirmation. Workspace Trust is an independent
            # Claude prerequisite and remains fail-closed for interactive Runs.
            settings_payload["skipDangerousModePermissionPrompt"] = True
        settings = json.dumps(
            settings_payload,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        forbidden_commands = [
            f"Bash({cli} cancel *)",
            f"Bash({cli} recover *)",
            f"Bash({cli} unlock *)",
            f"Bash({cli} init *)",
            f"Bash({cli} start *)",
            f"Bash({cli} install *)",
            f"Bash({cli} origin-*)",
            f"Bash({cli} _*)",
        ]
        return [
            *permission_args,
            "--settings",
            settings,
            "--allowedTools",
            *(f"Bash({command})" for command in formal_commands),
            "--disallowedTools",
            *forbidden_commands,
        ]

    def profile_mappings(
        self,
        launch_mode: str = "headless",
    ) -> dict[str, dict[str, list[str]]]:
        self.assert_launch_mode(launch_mode)
        profiles: dict[str, dict[str, list[str]]] = {}
        for profile in ("default", "trusted-workspace", "full-access"):
            fixed = [
                *self._permission_mapping(profile),
                "--setting-sources",
                "",
                "--strict-mcp-config",
                "--tools",
                "default",
                "--plugin-dir",
                str(effective_claude_plugin()),
            ]
            profiles[profile] = {
                "start": fixed.copy(),
                "resume": fixed.copy(),
            }
        return profiles

    def probe(self) -> CapabilityReport:
        report = super().probe()
        details = dict(report.details)
        details["model_provider_routes"] = {
            "supported": sorted(CLAUDE_MODEL_PROVIDERS),
            "selection": "explicit role option or frozen native environment",
            "credentials": "environment names only; values remain ephemeral",
        }
        details["runtime_isolation"] = {
            "interactive_config": "private per Run and role",
            "user_state": (
                "private snapshot with only the exact pretrusted Workspace"
            ),
            "full_access_confirmation": (
                "private bypassPermissionsModeAccepted; user state unchanged"
            ),
            "credentials": (
                "macOS Keychain or private mode-0600 credential copy"
            ),
            "session_resume": "native within private CLAUDE_CONFIG_DIR",
        }
        return CapabilityReport(
            adapter_id=report.adapter_id,
            adapter_version=report.adapter_version,
            executable=report.executable,
            executable_version=report.executable_version,
            authenticated=report.authenticated,
            profiles=report.profiles,
            launcher_stays_in_process_group=report.launcher_stays_in_process_group,
            details=details,
        )

    def profile_fingerprint(
        self,
        profile: str,
        session_policy: str,
        launch_mode: str = "headless",
    ) -> str:
        base = super().profile_fingerprint(profile, session_policy, launch_mode)
        contract = {
            "interactive_runtime_state": (
                {
                    "schema_version": 1,
                    "CLAUDE_CONFIG_DIR": "private per Run and role",
                    "source": "private snapshot of user state",
                    "projects": "exact trusted Workspace only",
                    "run_confirmation": (
                        "private bypassPermissionsModeAccepted"
                        if profile == "full-access"
                        else "not recorded"
                    ),
                }
                if launch_mode == "interactive"
                else None
            )
        }
        components = [base.encode(), canonical_json_bytes(contract)]
        framed = b"".join(
            len(component).to_bytes(8, "big") + component
            for component in components
        )
        return sha256_bytes(framed)

    def authentication_status(self) -> bool | None:
        try:
            result = subprocess.run(
                [str(self.executable()), "auth", "status"],
                capture_output=True,
                text=True,
                check=False,
                timeout=5,
            )
        except subprocess.TimeoutExpired:
            return None
        return result.returncode == 0

    @staticmethod
    def _user_state_path() -> Path:
        configured = os.environ.get("CLAUDE_CONFIG_DIR")
        current_base = (
            Path(configured).expanduser()
            if configured
            else Path.home() / ".claude"
        )
        current = current_base / ".config.json"
        if current.exists():
            return current
        legacy_base = Path(configured).expanduser() if configured else Path.home()
        return legacy_base / ".claude.json"

    @staticmethod
    def _source_config_home() -> Path:
        configured = os.environ.get("CLAUDE_CONFIG_DIR")
        return (
            Path(configured).expanduser()
            if configured
            else Path.home() / ".claude"
        )

    @classmethod
    def _read_user_state(cls) -> dict[str, object]:
        state_path = cls._user_state_path()
        try:
            raw = state_path.read_bytes()
        except FileNotFoundError:
            return {}
        except OSError as exc:
            raise AgentTeamError(
                "HARNESS_USER_CONFIG_UNREADABLE",
                f"cannot read Claude Code user state {state_path}: {exc}",
            ) from exc
        try:
            state = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise AgentTeamError(
                "HARNESS_USER_CONFIG_INVALID",
                f"Claude Code user state is invalid: {state_path}",
            ) from exc
        if not isinstance(state, dict):
            raise AgentTeamError(
                "HARNESS_USER_CONFIG_INVALID",
                "Claude Code user state must be an object",
            )
        return state

    @classmethod
    def _workspace_is_trusted(cls, workspace: Path) -> bool:
        state = cls._read_user_state()
        projects = state.get("projects", {})
        if not isinstance(projects, dict):
            raise AgentTeamError(
                "HARNESS_USER_CONFIG_INVALID",
                "Claude Code user state projects must be an object",
            )
        try:
            resolved = workspace.resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise AgentTeamError(
                "HARNESS_WORKSPACE_UNREADABLE",
                f"cannot resolve Claude Code workspace: {workspace}",
            ) from exc
        for candidate in (resolved, *resolved.parents):
            project = projects.get(str(candidate))
            if (
                isinstance(project, dict)
                and project.get("hasTrustDialogAccepted") is True
            ):
                return True
        return False

    @classmethod
    def _assert_interactive_workspace_trusted(cls, workspace: Path) -> None:
        if cls._workspace_is_trusted(workspace):
            return
        resolved = workspace.resolve(strict=True)
        raise AgentTeamError(
            "HARNESS_WORKSPACE_TRUST_REQUIRED",
            "Claude Code has not trusted the interactive workspace "
            f"{resolved}. Run `cd {resolved} && claude`, accept the workspace "
            "trust prompt, exit Claude, then retry the Agent-Team command.",
        )

    @staticmethod
    def _runtime_home(run_dir: Path, role_id: str) -> Path:
        digest = sha256_bytes(os.fsencode(str(run_dir.resolve(strict=True))))
        return (
            fixed_state_dir()
            / "harness-homes"
            / "claude-code"
            / digest
            / role_id
        )

    @classmethod
    def _runtime_hierarchy(
        cls,
        run_dir: Path,
        role_id: str,
    ) -> tuple[Path, ...]:
        home = cls._runtime_home(run_dir, role_id)
        state = fixed_state_dir()
        return (
            state,
            state / "harness-homes",
            state / "harness-homes" / "claude-code",
            home.parent,
            home,
        )

    @classmethod
    def _runtime_marker(cls, run_dir: Path, role_id: str) -> dict[str, object]:
        return {
            "schema_version": 1,
            "adapter": "claude-code",
            "run_dir": str(run_dir.resolve(strict=True)),
            "role_id": role_id,
        }

    @staticmethod
    def _role_profile(run_dir: Path, role_id: str) -> str:
        team = read_json(run_dir / "team.json")
        roles = team.get("roles")
        role = roles.get(role_id) if isinstance(roles, dict) else None
        profile = role.get("launch_profile") if isinstance(role, dict) else None
        if profile not in {"default", "trusted-workspace", "full-access"}:
            raise IntegrityError(
                f"Claude runtime state has an invalid role Profile: {role_id}"
            )
        return profile

    @classmethod
    def _role_launch_options(
        cls,
        run_dir: Path,
        role_id: str,
    ) -> HarnessLaunchOptions:
        team = read_json(run_dir / "team.json")
        roles = team.get("roles")
        role = roles.get(role_id) if isinstance(roles, dict) else None
        adapter = role.get("adapter") if isinstance(role, dict) else None
        options = role.get("harness_options") if isinstance(role, dict) else None
        if adapter != cls.adapter_id:
            raise IntegrityError(
                f"Claude runtime state has an invalid role: {role_id}"
            )
        if not isinstance(options, dict):
            return HarnessLaunchOptions()
        launch_options = HarnessLaunchOptions(
            model=options.get("model"),
            reasoning_effort=options.get("reasoning_effort"),
            fast_mode=options.get("fast_mode"),
            model_provider=options.get("model_provider"),
            model_provider_config=options.get("model_provider_config"),
        )
        try:
            cls().assert_launch_options(launch_options)
        except InvalidArgument as exc:
            raise IntegrityError(
                f"Claude runtime state has invalid launch options for {role_id}: "
                f"{exc.message}"
            ) from exc
        return launch_options

    @classmethod
    def _private_runtime_state(
        cls,
        *,
        workspace: Path,
        profile: str,
    ) -> dict[str, object]:
        # Claude 2.1.25 has no dynamic skipDangerousModePermissionPrompt
        # setting. Snapshot its non-secret user state into a private home so
        # onboarding/API-key decisions remain available without allowing this
        # Run to mutate ~/.claude.json. Project history is deliberately
        # replaced by the one Workspace whose trust was already preflighted.
        state = dict(cls._read_user_state())
        state["projects"] = {
            str(workspace.resolve(strict=True)): {
                "hasTrustDialogAccepted": True,
            }
        }
        state["hasCompletedOnboarding"] = True
        if profile == "full-access":
            # This bit exists only in the Run-owned CLAUDE_CONFIG_DIR and is
            # backed by the immutable Agent-Team full-access confirmation.
            state["bypassPermissionsModeAccepted"] = True
        else:
            state.pop("bypassPermissionsModeAccepted", None)
        return state

    @classmethod
    def _assert_runtime_home(
        cls,
        *,
        run_dir: Path,
        role_id: str,
        workspace: Path,
        profile: str,
        requires_claude_auth: bool,
    ) -> Path:
        home = cls._runtime_home(run_dir, role_id)
        try:
            info = home.lstat()
        except OSError as exc:
            raise AgentTeamError(
                "HARNESS_STATE_NOT_PREPARED",
                f"Claude Code private state is unavailable for {role_id}",
            ) from exc
        if home.is_symlink() or not stat.S_ISDIR(info.st_mode):
            raise IntegrityError(f"Claude Code private state is unsafe: {home}")
        marker_path = home / "agent-team-home.json"
        if (
            not path_entry_exists(marker_path)
            or read_json(marker_path) != cls._runtime_marker(run_dir, role_id)
        ):
            raise AgentTeamError(
                "HARNESS_STATE_NOT_PREPARED",
                f"Claude Code private state is not prepared for {role_id}",
            )
        state = read_json(home / ".config.json")
        projects = state.get("projects")
        project = (
            projects.get(str(workspace.resolve(strict=True)))
            if isinstance(projects, dict)
            else None
        )
        if not isinstance(project, dict) or project.get(
            "hasTrustDialogAccepted"
        ) is not True:
            raise IntegrityError("Claude Code private Workspace trust is invalid")
        accepted = state.get("bypassPermissionsModeAccepted") is True
        if accepted != (profile == "full-access"):
            raise IntegrityError(
                "Claude Code private full-access confirmation is invalid"
            )
        if not requires_claude_auth and path_entry_exists(
            home / ".credentials.json"
        ):
            raise IntegrityError(
                "Claude Code external Provider state contains Claude credentials"
            )
        return home

    def prepare_run_state(
        self,
        *,
        run_dir: Path,
        role_id: str,
        launch_mode: str,
        session_generation: int = 1,
    ) -> None:
        super().prepare_run_state(
            run_dir=run_dir,
            role_id=role_id,
            launch_mode=launch_mode,
            session_generation=session_generation,
        )
        if launch_mode == "interactive":
            workspace = workspace_from_run_dir(run_dir)
            self._assert_interactive_workspace_trusted(workspace)
        options = self._role_launch_options(run_dir, role_id)
        self.assert_launch_prerequisites(options)
        if launch_mode == "interactive":
            profile = self._role_profile(run_dir, role_id)
            for directory in self._runtime_hierarchy(run_dir, role_id):
                ensure_dir(directory)
                info = directory.lstat()
                if directory.is_symlink() or not stat.S_ISDIR(info.st_mode):
                    raise IntegrityError(
                        f"Claude Code state directory is unsafe: {directory}"
                    )
                directory.chmod(0o700)
            home = self._runtime_home(run_dir, role_id)
            marker_path = home / "agent-team-home.json"
            marker = self._runtime_marker(run_dir, role_id)
            if path_entry_exists(marker_path):
                if read_json(marker_path) != marker:
                    raise IntegrityError(
                        f"Claude Code home belongs to another Run: {home}"
                    )
            else:
                atomic_json(marker_path, marker, immutable=True)
            atomic_json(
                home / ".config.json",
                self._private_runtime_state(
                    workspace=workspace,
                    profile=profile,
                ),
            )
            source_credentials = self._source_config_home() / ".credentials.json"
            if self.authentication_required(options) and path_entry_exists(
                source_credentials
            ):
                try:
                    source_info = source_credentials.lstat()
                except OSError as exc:
                    raise AgentTeamError(
                        "HARNESS_AUTH_STATE_UNREADABLE",
                        "cannot read Claude Code authentication state",
                    ) from exc
                if (
                    source_credentials.is_symlink()
                    or not stat.S_ISREG(source_info.st_mode)
                    or stat.S_IMODE(source_info.st_mode) & 0o077
                ):
                    raise AgentTeamError(
                        "HARNESS_AUTH_STATE_UNSAFE",
                        "Claude Code authentication state is not a private regular file",
                    )
                atomic_write(
                    home / ".credentials.json",
                    read_regular(source_credentials),
                    mode=0o600,
                )

    def finalize_run_state(
        self,
        *,
        run_dir: Path,
        role_id: str,
        launch_mode: str,
    ) -> None:
        super().finalize_run_state(
            run_dir=run_dir,
            role_id=role_id,
            launch_mode=launch_mode,
        )
        if launch_mode != "interactive":
            return
        home = self._runtime_home(run_dir, role_id)
        marker_path = home / "agent-team-home.json"
        if (
            not path_entry_exists(marker_path)
            or read_json(marker_path) != self._runtime_marker(run_dir, role_id)
        ):
            raise IntegrityError(
                f"Claude Code private state is not owned by this Run: {home}"
            )
        for directory, child_dirs, files in os.walk(
            home,
            topdown=False,
            followlinks=False,
        ):
            current = Path(directory)
            for name in (*child_dirs, *files):
                path = current / name
                info = path.lstat()
                if stat.S_ISLNK(info.st_mode):
                    # Claude Code creates Run-local convenience links such as
                    # debug/latest. Never follow or chmod their targets; the
                    # private 0700 hierarchy remains the access boundary.
                    continue
                if not (
                    stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)
                ):
                    raise IntegrityError(
                        f"Claude Code private state entry is unsafe: {path}"
                    )
                path.chmod(0o700 if stat.S_ISDIR(info.st_mode) else 0o600)
            current.chmod(0o700)
        for directory in self._runtime_hierarchy(run_dir, role_id):
            directory.chmod(0o700)

    def has_prepared_run_state(
        self,
        *,
        run_dir: Path,
        role_id: str,
        launch_mode: str,
    ) -> bool:
        self.assert_launch_mode(launch_mode)
        return launch_mode == "interactive" and path_entry_exists(
            self._runtime_home(run_dir, role_id)
        )

    @staticmethod
    def _boolean_environment(name: str) -> bool | None:
        raw = os.environ.get(name)
        if raw is None or not raw.strip():
            return None
        normalized = raw.strip().casefold()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
        raise AgentTeamError(
            "HARNESS_PROVIDER_CONFIG_INVALID",
            f"Claude Code Provider flag {name!r} must be a boolean value",
        )

    @classmethod
    def _selected_model_provider(cls, explicit: str | None) -> str:
        if explicit is not None:
            if explicit not in CLAUDE_MODEL_PROVIDERS:
                supported = ", ".join(sorted(CLAUDE_MODEL_PROVIDERS))
                raise InvalidArgument(
                    f"claude-code model provider must be one of: {supported}"
                )
            return explicit
        selected = [
            provider
            for provider, name in _CLAUDE_PROVIDER_FLAGS.items()
            if cls._boolean_environment(name) is True
        ]
        if cls._boolean_environment("CLAUDE_CODE_USE_MANTLE") is True:
            raise InvalidArgument(
                "Claude Code Mantle routing is not supported; choose one of "
                + ", ".join(sorted(CLAUDE_MODEL_PROVIDERS))
            )
        if len(selected) > 1:
            raise InvalidArgument(
                "Claude Code environment enables conflicting model providers: "
                + ", ".join(selected)
            )
        if selected:
            return selected[0]
        base_url = os.environ.get("ANTHROPIC_BASE_URL", "").strip()
        if base_url and base_url.rstrip("/") != _ANTHROPIC_API_BASE_URL:
            return "gateway"
        return "anthropic"

    @classmethod
    def _frozen_provider_config(cls, provider: str) -> dict[str, object]:
        settings: dict[str, object] = {}
        if provider == "gateway":
            base_url = os.environ.get("ANTHROPIC_BASE_URL")
            if base_url:
                settings["base_url"] = base_url
        elif provider == "bedrock":
            region = os.environ.get("AWS_REGION") or os.environ.get(
                "AWS_DEFAULT_REGION"
            )
            if region:
                settings["region"] = region
            base_url = os.environ.get("ANTHROPIC_BEDROCK_BASE_URL")
            if base_url:
                settings["base_url"] = base_url
            if cls._boolean_environment("CLAUDE_CODE_SKIP_BEDROCK_AUTH") is True:
                settings["skip_auth"] = True
        elif provider == "vertex":
            region = os.environ.get("CLOUD_ML_REGION")
            project_id = os.environ.get("ANTHROPIC_VERTEX_PROJECT_ID")
            base_url = os.environ.get("ANTHROPIC_VERTEX_BASE_URL")
            if region:
                settings["region"] = region
            if project_id:
                settings["project_id"] = project_id
            if base_url:
                settings["base_url"] = base_url
            if cls._boolean_environment("CLAUDE_CODE_SKIP_VERTEX_AUTH") is True:
                settings["skip_auth"] = True
        elif provider == "foundry":
            resource = os.environ.get("ANTHROPIC_FOUNDRY_RESOURCE")
            base_url = os.environ.get("ANTHROPIC_FOUNDRY_BASE_URL")
            if resource:
                settings["resource"] = resource
            if base_url:
                settings["base_url"] = base_url
            if cls._boolean_environment("CLAUDE_CODE_SKIP_FOUNDRY_AUTH") is True:
                settings["skip_auth"] = True
        credential_names = sorted(
            name
            for name in CLAUDE_PROVIDER_CREDENTIAL_ENVIRONMENTS[provider]
            if os.environ.get(name)
        )
        config: dict[str, object] = {
            "settings": settings,
            "credential_environment_names": credential_names,
        }
        error = claude_model_provider_config_error(provider, config)
        if error is not None:
            raise InvalidArgument(
                f"invalid Claude Code {provider!r} Provider configuration: {error}"
            )
        return config

    @staticmethod
    def _effective_provider_options(
        options: HarnessLaunchOptions,
    ) -> HarnessLaunchOptions:
        if options.model_provider is not None:
            return options
        return HarnessLaunchOptions(
            model=options.model,
            reasoning_effort=options.reasoning_effort,
            fast_mode=options.fast_mode,
            model_provider="anthropic",
            model_provider_config={
                "settings": {},
                "credential_environment_names": [],
            },
        )

    def assert_launch_options(self, options: HarnessLaunchOptions) -> None:
        if options.model is not None and not valid_model_id(options.model):
            raise InvalidArgument("claude-code model must be a non-empty model id")
        if (
            options.reasoning_effort is not None
            and options.reasoning_effort not in CLAUDE_REASONING_EFFORTS
        ):
            supported = ", ".join(sorted(CLAUDE_REASONING_EFFORTS))
            raise InvalidArgument(
                f"claude-code reasoning effort must be one of: {supported}"
            )
        if options.fast_mode is not None:
            raise InvalidArgument("fast mode is only supported by the codex adapter")
        if options.model_provider is None and options.model_provider_config is None:
            # Schema v6 Runs predate explicit Claude Provider routes and are
            # interpreted as the direct Anthropic route during recovery.
            return
        error = claude_model_provider_config_error(
            options.model_provider,
            options.model_provider_config,
        )
        if error is not None:
            raise InvalidArgument(f"invalid Claude Code model provider: {error}")

    @staticmethod
    def _user_settings_path() -> Path:
        configured_home = os.environ.get("CLAUDE_CONFIG_DIR")
        base = (
            Path(configured_home).expanduser()
            if configured_home
            else Path.home() / ".claude"
        )
        return base / "settings.json"

    def _user_launch_options(
        self,
        *,
        include_model: bool,
        include_reasoning_effort: bool,
    ) -> HarnessLaunchOptions:
        env_model = os.environ.get("ANTHROPIC_MODEL") if include_model else None
        env_effort = (
            os.environ.get("CLAUDE_CODE_EFFORT_LEVEL")
            if include_reasoning_effort
            else None
        )
        need_settings = (
            (include_model and env_model is None)
            or (include_reasoning_effort and env_effort is None)
        )
        path = self._user_settings_path()
        if not need_settings:
            value: dict[str, object] = {}
        else:
            try:
                raw = path.read_bytes()
            except FileNotFoundError:
                value = {}
            except OSError as exc:
                raise AgentTeamError(
                    "HARNESS_USER_CONFIG_UNREADABLE",
                    f"cannot read Claude Code user settings {path}: {exc}",
                ) from exc
            else:
                try:
                    parsed = json.loads(raw)
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise AgentTeamError(
                        "HARNESS_USER_CONFIG_INVALID",
                        f"Claude Code user settings are invalid: {path}",
                    ) from exc
                if not isinstance(parsed, dict):
                    raise AgentTeamError(
                        "HARNESS_USER_CONFIG_INVALID",
                        "Claude Code user settings must be an object",
                    )
                value = parsed
        settings_env = value.get("env", {})
        if not isinstance(settings_env, dict):
            raise AgentTeamError(
                "HARNESS_USER_CONFIG_INVALID",
                "Claude Code user settings env must be an object",
            )
        settings_model = value.get("model") if include_model else None
        settings_effort = (
            value.get("effortLevel") if include_reasoning_effort else None
        )
        model = None
        if include_model:
            model = (
                env_model
                if env_model is not None
                else settings_env.get("ANTHROPIC_MODEL", settings_model)
            )
        effort = None
        if include_reasoning_effort:
            effort = (
                env_effort
                if env_effort is not None
                else settings_env.get(
                    "CLAUDE_CODE_EFFORT_LEVEL",
                    settings_effort,
                )
            )
        if model is not None and not isinstance(model, str):
            raise AgentTeamError(
                "HARNESS_USER_CONFIG_INVALID",
                "Claude Code user model default must be a string",
            )
        if effort is not None and not isinstance(effort, str):
            raise AgentTeamError(
                "HARNESS_USER_CONFIG_INVALID",
                "Claude Code user effort default must be a string",
            )
        options = HarnessLaunchOptions(
            model=model,
            reasoning_effort=effort,
        )
        try:
            self.assert_launch_options(options)
        except InvalidArgument as exc:
            raise AgentTeamError(
                "HARNESS_USER_CONFIG_INVALID",
                f"Claude Code user settings are invalid: {exc.message}",
            ) from exc
        return options

    def resolve_launch_options(
        self,
        *,
        model: str | None,
        reasoning_effort: str | None,
        fast_mode: bool | None,
        model_provider: str | None = None,
        workspace: Path | None = None,
    ) -> HarnessLaunchOptions:
        explicit = HarnessLaunchOptions(
            model=model,
            reasoning_effort=reasoning_effort,
            fast_mode=fast_mode,
        )
        self.assert_launch_options(explicit)
        provider = self._selected_model_provider(model_provider)
        provider_config = self._frozen_provider_config(provider)
        defaults = (
            self._user_launch_options(
                include_model=model is None,
                include_reasoning_effort=reasoning_effort is None,
            )
            if model is None or reasoning_effort is None
            else HarnessLaunchOptions()
        )
        options = HarnessLaunchOptions(
            model=model if model is not None else defaults.model,
            reasoning_effort=(
                reasoning_effort
                if reasoning_effort is not None
                else defaults.reasoning_effort
            ),
            model_provider=provider,
            model_provider_config=provider_config,
        )
        self.assert_launch_options(options)
        return options

    @classmethod
    def _provider_environment(
        cls,
        options: HarnessLaunchOptions,
    ) -> dict[str, str]:
        selected = cls._effective_provider_options(options)
        cls().assert_launch_options(selected)
        provider = selected.model_provider or "anthropic"
        config = selected.model_provider_config or {}
        settings = config.get("settings", {})
        if not isinstance(settings, dict):
            raise InvalidArgument("invalid Claude Code Provider settings")
        env = {name: "0" for name in _CLAUDE_ROUTE_FLAGS}
        env.update(
            {
                "ANTHROPIC_BASE_URL": "",
                "ANTHROPIC_BEDROCK_BASE_URL": "",
                "ANTHROPIC_VERTEX_BASE_URL": "",
                "ANTHROPIC_FOUNDRY_BASE_URL": "",
                "AWS_REGION": "",
                "AWS_DEFAULT_REGION": "",
                "CLOUD_ML_REGION": "",
                "ANTHROPIC_VERTEX_PROJECT_ID": "",
                "ANTHROPIC_FOUNDRY_RESOURCE": "",
                "CLAUDE_CODE_SKIP_BEDROCK_AUTH": "0",
                "CLAUDE_CODE_SKIP_VERTEX_AUTH": "0",
                "CLAUDE_CODE_SKIP_FOUNDRY_AUTH": "0",
            }
        )
        referenced_credentials = set(
            cls._provider_environment_names(selected)
        )
        for name in set().union(
            *CLAUDE_PROVIDER_CREDENTIAL_ENVIRONMENTS.values()
        ) - referenced_credentials:
            env[name] = ""
        if provider == "anthropic":
            env["ANTHROPIC_BASE_URL"] = _ANTHROPIC_API_BASE_URL
        elif provider == "gateway":
            env["ANTHROPIC_BASE_URL"] = str(settings["base_url"])
        elif provider == "bedrock":
            env[_CLAUDE_PROVIDER_FLAGS[provider]] = "1"
            if "region" in settings:
                env["AWS_REGION"] = str(settings["region"])
                env["AWS_DEFAULT_REGION"] = str(settings["region"])
            if "base_url" in settings:
                env["ANTHROPIC_BEDROCK_BASE_URL"] = str(settings["base_url"])
            if settings.get("skip_auth") is True:
                env["CLAUDE_CODE_SKIP_BEDROCK_AUTH"] = "1"
        elif provider == "vertex":
            env[_CLAUDE_PROVIDER_FLAGS[provider]] = "1"
            if "region" in settings:
                env["CLOUD_ML_REGION"] = str(settings["region"])
            if "project_id" in settings:
                env["ANTHROPIC_VERTEX_PROJECT_ID"] = str(settings["project_id"])
            if "base_url" in settings:
                env["ANTHROPIC_VERTEX_BASE_URL"] = str(settings["base_url"])
            if settings.get("skip_auth") is True:
                env["CLAUDE_CODE_SKIP_VERTEX_AUTH"] = "1"
        elif provider == "foundry":
            env[_CLAUDE_PROVIDER_FLAGS[provider]] = "1"
            if "resource" in settings:
                env["ANTHROPIC_FOUNDRY_RESOURCE"] = str(settings["resource"])
            if "base_url" in settings:
                env["ANTHROPIC_FOUNDRY_BASE_URL"] = str(settings["base_url"])
            if settings.get("skip_auth") is True:
                env["CLAUDE_CODE_SKIP_FOUNDRY_AUTH"] = "1"
        return env

    @staticmethod
    def authentication_required(options: HarnessLaunchOptions) -> bool:
        return (
            options.model_provider in {None, "anthropic"}
            and not ClaudeCodeAdapter._provider_environment_names(options)
        )

    @staticmethod
    def _provider_environment_names(
        options: HarnessLaunchOptions,
    ) -> tuple[str, ...]:
        config = options.model_provider_config
        if not isinstance(config, dict):
            return ()
        names = config.get("credential_environment_names")
        if not isinstance(names, list):
            return ()
        return tuple(name for name in names if isinstance(name, str))

    def assert_launch_prerequisites(self, options: HarnessLaunchOptions) -> None:
        self.assert_launch_options(options)
        selected = self._effective_provider_options(options)
        for name in self._provider_environment_names(selected):
            if not os.environ.get(name):
                raise AgentTeamError(
                    "HARNESS_ENVIRONMENT_UNAVAILABLE",
                    f"Claude Code model provider {selected.model_provider!r} "
                    f"requires non-empty environment variable {name!r}",
                )
        if (
            self.authentication_required(selected)
            and self.authentication_status() is False
        ):
            raise AgentTeamError(
                "HARNESS_NOT_AUTHENTICATED",
                "claude-code is not authenticated for the selected model provider",
            )

    def worker_environment_names(
        self,
        *,
        run_dir: Path,
        role_id: str,
        options: HarnessLaunchOptions | None = None,
    ) -> tuple[str, ...]:
        selected = self._effective_provider_options(
            options or self._role_launch_options(run_dir, role_id)
        )
        self.assert_launch_options(selected)
        return self._provider_environment_names(selected)

    def prepare_launch(self, context: TurnLaunchContext) -> LaunchSpec:
        self.assert_launch_mode(context.launch_mode)
        self.assert_profile(
            context.launch_profile,
            context.session_policy,
            context.launch_profile_sha256,
            context.launch_mode,
        )
        options = HarnessLaunchOptions(
            model=context.model,
            reasoning_effort=context.reasoning_effort,
            fast_mode=context.fast_mode,
            model_provider=context.model_provider,
            model_provider_config=context.model_provider_config,
        )
        self.assert_launch_options(options)
        options = self._effective_provider_options(options)
        runtime_home: Path | None = None
        if context.launch_mode == "interactive":
            # Recheck on every Turn so a revoked or corrupted trust decision
            # fails closed before Claude starts a native TUI.
            workspace = Path(context.workspace)
            self._assert_interactive_workspace_trusted(workspace)
            run_dir = Path(context.turn_dir).parent.parent
            runtime_home = self._assert_runtime_home(
                run_dir=run_dir,
                role_id=context.role_id,
                workspace=workspace,
                profile=context.launch_profile,
                requires_claude_auth=self.authentication_required(options),
            )
        executable = str(self.executable())
        mapping = self.profile_mappings(context.launch_mode)[context.launch_profile]
        selection: tuple[str, ...] = (
            ("--model", options.model) if options.model is not None else ()
        )
        prompt_file: str | None = None
        if context.launch_mode == "interactive":
            base = (executable, *selection)
            prompt_file = str(Path(context.turn_dir) / "process" / "prompt.md")
            if context.session_ref and context.session_policy == "resume":
                session_ref = context.session_ref
                argv = (*base, *mapping["resume"], "--resume", session_ref)
                starts_new = False
            else:
                session_ref = str(uuid.uuid4())
                argv = (*base, *mapping["start"], "--session-id", session_ref)
                starts_new = True
        else:
            base = (
                executable,
                "-p",
                "--input-format",
                "text",
                "--output-format",
                "stream-json",
                "--verbose",
                *selection,
            )
            if context.session_ref and context.session_policy == "resume":
                session_ref = context.session_ref
                argv = (*base, *mapping["resume"], "--resume", session_ref)
                starts_new = False
            else:
                session_ref = str(uuid.uuid4())
                argv = (*base, *mapping["start"], "--session-id", session_ref)
                starts_new = True
        env = {
            "AGENT_TEAM_RUN_ID": context.run_id,
            "AGENT_TEAM_ROLE_ID": context.role_id,
            "AGENT_TEAM_TURN_ID": context.turn_id,
            "AGENT_TEAM_RUN_DIR": str(Path(context.turn_dir).parent.parent),
            "AGENT_TEAM_TURN_DIR": context.turn_dir,
            "AGENT_TEAM_CLI": context.agent_team_cli,
            "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1",
        }
        if options.reasoning_effort is not None:
            env["CLAUDE_CODE_EFFORT_LEVEL"] = options.reasoning_effort
        if runtime_home is not None:
            env["CLAUDE_CONFIG_DIR"] = str(runtime_home)
        env.update(self._provider_environment(options))
        return LaunchSpec(
            adapter_id=self.adapter_id,
            argv=argv,
            cwd=context.workspace,
            env=env,
            stdin=context.prompt,
            launch_profile=context.launch_profile,
            launch_profile_sha256=context.launch_profile_sha256,
            starts_new_session=starts_new,
            launch_mode=context.launch_mode,
            prompt_file=prompt_file,
            expected_session_ref=(
                session_ref if context.launch_mode == "interactive" else None
            ),
        )

    def parse_stream_record(
        self, record: StreamRecord
    ) -> AdapterEvidence | None:
        value = self.parse_json_record(record)
        if value is None:
            return None
        kind = value.get("type")
        session_id = value.get("session_id")
        session_ref = session_id if isinstance(session_id, str) else None
        if (
            kind == "result"
            and value.get("subtype") == "error_during_execution"
            and value.get("is_error") is True
            and value.get("num_turns") == 0
        ):
            errors = value.get("errors")
            if isinstance(errors, list) and any(
                isinstance(error, str)
                and error.startswith("No conversation found with session ID:")
                for error in errors
            ):
                return AdapterEvidence(
                    session_unavailable_reason="session_not_found"
                )
        if kind == "system" and value.get("subtype") == "init":
            return AdapterEvidence(observed_session_ref=session_ref)
        if kind == "assistant":
            return AdapterEvidence(
                agent_execution_started=True,
                observed_session_ref=session_ref,
            )
        if kind == "result":
            denials = value.get("permission_denials")
            if isinstance(denials, list) and denials:
                return AdapterEvidence(
                    permission_required=True,
                    observed_session_ref=session_ref,
                )
            if value.get("subtype") == "success" and not value.get("is_error", False):
                return AdapterEvidence(
                    agent_execution_started=True,
                    adapter_completed=True,
                    observed_session_ref=session_ref,
                )
        return None

    def normalize_stream_record(
        self,
        record: StreamRecord,
    ) -> list[NormalizedTraceEvent]:
        if record.source == "terminal":
            return super().normalize_stream_record(record)
        value = self.parse_json_record(record)
        if value is None:
            return super().normalize_stream_record(record)
        kind = value.get("type")
        session_ref = (
            value.get("session_id")
            if isinstance(value.get("session_id"), str)
            else None
        )
        if kind == "system" and value.get("subtype") == "init":
            return [
                NormalizedTraceEvent(
                    "session",
                    {
                        "state": "started",
                        "session_ref": session_ref,
                        "model": value.get("model"),
                        "permission_mode": value.get("permissionMode"),
                        "tools": value.get("tools"),
                        "plugins": value.get("plugins"),
                    },
                )
            ]
        if kind in {"assistant", "user"}:
            message = value.get("message")
            content = message.get("content") if isinstance(message, dict) else None
            if not isinstance(content, list):
                return super().normalize_stream_record(record)
            events: list[NormalizedTraceEvent] = []
            for block in content:
                if not isinstance(block, dict):
                    continue
                block_type = block.get("type")
                if kind == "assistant" and block_type == "text":
                    events.append(
                        NormalizedTraceEvent(
                            "agent_message",
                            {
                                "session_ref": session_ref,
                                "direction": "assistant",
                                "text": block.get("text", ""),
                            },
                        )
                    )
                elif kind == "assistant" and block_type in {
                    "server_tool_use",
                    "tool_use",
                }:
                    events.append(
                        NormalizedTraceEvent(
                            "tool_call",
                            {
                                "session_ref": session_ref,
                                "tool_call_id": block.get("id"),
                                "tool": block.get("name"),
                                "input": block.get("input"),
                                "payload": block,
                            },
                        )
                    )
                elif kind == "user" and block_type in {
                    "tool_result",
                    "web_search_tool_result",
                }:
                    events.append(
                        NormalizedTraceEvent(
                            "tool_result",
                            {
                                "session_ref": session_ref,
                                "tool_call_id": block.get("tool_use_id"),
                                "content": block.get("content"),
                                "is_error": block.get("is_error", False),
                                "payload": block,
                            },
                        )
                    )
                elif kind == "assistant" and block_type == "reasoning_summary":
                    # Only explicit, harness-exposed reasoning_summary blocks
                    # are retained as reasoning_summary events. Per acceptance
                    # area 2, only Harness-exposed reasoning summaries may be
                    # captured; generic "reasoning" blocks are private chain-
                    # of-thought and receive the same opaque redaction treatment
                    # as raw "thinking" blocks.
                    reasoning_text = block.get("summary") or block.get("text") or ""
                    if reasoning_text:
                        events.append(
                            NormalizedTraceEvent(
                                "reasoning_summary",
                                {
                                    "session_ref": session_ref,
                                    "text": reasoning_text,
                                },
                            )
                        )
                elif kind == "assistant" and block_type in {"thinking", "reasoning"}:
                    # Private extended thinking content is never retained in
                    # normalized traces. It is not a harness-exposed reasoning
                    # summary, and acceptance area 2 explicitly requires only
                    # Harness-exposed reasoning summaries be captured. We emit
                    # a lightweight diagnostic event that preserves sequence
                    # reference without leaking private content.
                    events.append(
                        NormalizedTraceEvent(
                            "diagnostic",
                            {
                                "session_ref": session_ref,
                                "block_type": block_type,
                                "redacted_private_reasoning": True,
                            },
                        )
                    )
                else:
                    events.append(
                        NormalizedTraceEvent(
                            "harness_event",
                            {
                                "source": record.source,
                                "kind": kind,
                                "payload": block,
                            },
                        )
                    )
            return events or super().normalize_stream_record(record)
        if kind == "result":
            if value.get("subtype") == "success" and not value.get(
                "is_error",
                False,
            ):
                return [
                    NormalizedTraceEvent(
                        "usage",
                        {
                            "state": "completed",
                            "session_ref": session_ref,
                            "usage": value.get("usage", {}),
                            "total_cost_usd": value.get("total_cost_usd"),
                            "duration_ms": value.get("duration_ms"),
                            "duration_api_ms": value.get("duration_api_ms"),
                            "num_turns": value.get("num_turns"),
                        },
                    )
                ]
            return [
                NormalizedTraceEvent(
                    "error",
                    {
                        "session_ref": session_ref,
                        "subtype": value.get("subtype"),
                        "errors": value.get("errors"),
                        "permission_denials": value.get("permission_denials"),
                    },
                )
            ]
        return super().normalize_stream_record(record)
