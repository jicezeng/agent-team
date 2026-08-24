from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import tomllib
from pathlib import Path

from agent_team.config import (
    CODEX_BUILTIN_MODEL_PROVIDERS,
    CODEX_MODEL_PROVIDER_CONFIG_KEYS,
    CODEX_REASONING_EFFORTS,
    codex_model_provider_config_error,
    valid_model_id,
    valid_model_provider_id,
)
from agent_team.errors import AgentTeamError, IntegrityError, InvalidArgument
from agent_team.state import fixed_state_dir
from agent_team.util import (
    atomic_json,
    atomic_write,
    ensure_dir,
    fsync_dir,
    open_regular,
    path_entry_exists,
    read_json,
    read_regular,
    sha256_bytes,
)

from .base import (
    AdapterEvidence,
    HarnessAdapter,
    HarnessLaunchOptions,
    LaunchSpec,
    NormalizedTraceEvent,
    StreamRecord,
    TurnLaunchContext,
    workspace_from_run_dir,
)

# Codex 0.147.0 treats this field as the number of times its startup model
# availability tooltip has been shown and stops persisting updates at four.
# Interactive Run homes preseed that terminal state so native TUI bookkeeping
# cannot mutate the otherwise frozen security-boundary config between Turns.
_MODEL_AVAILABILITY_NUX_MAX_SHOW_COUNT = 4
_IGNORED_MODEL_PROVIDER_FIELDS = frozenset({"env_key_instructions"})


class CodexAdapter(HarnessAdapter):
    adapter_id = "codex"
    executable_name = "codex"

    def authentication_status(self) -> bool | None:
        try:
            result = subprocess.run(
                [str(self.executable()), "login", "status"],
                capture_output=True,
                text=True,
                check=False,
                timeout=5,
            )
        except subprocess.TimeoutExpired:
            return None
        return result.returncode == 0

    @staticmethod
    def _workspace_permission_mapping(*, network_access: bool) -> list[str]:
        return [
            "-c",
            'sandbox_mode="workspace-write"',
            "-c",
            'approval_policy="never"',
            "-c",
            "sandbox_workspace_write.writable_roots=[]",
            "-c",
            (
                "sandbox_workspace_write.network_access=true"
                if network_access
                else "sandbox_workspace_write.network_access=false"
            ),
            "-c",
            "sandbox_workspace_write.exclude_tmpdir_env_var=false",
            "-c",
            "sandbox_workspace_write.exclude_slash_tmp=false",
        ]

    def profile_mappings(
        self,
        launch_mode: str = "headless",
    ) -> dict[str, dict[str, list[str]]]:
        self.assert_launch_mode(launch_mode)
        # These two isolation flags are intentionally scoped to `codex exec`
        # and are rejected by the interactive CLI. Interactive Runs instead
        # receive a private CODEX_HOME prepared by `prepare_run_state`.
        common = [
            *(
                ["--ignore-user-config", "--ignore-rules"]
                if launch_mode == "headless"
                else []
            ),
            # Codex merges hooks from every active config layer instead of
            # replacing lower-precedence hook sources. Freeze the feature off
            # so a trusted Workspace cannot add an unrecorded host process to
            # an Agent-Team Profile. Admin requirements can still force
            # managed hooks; that effective policy is outside this mapping.
            "-c",
            "features.hooks=false",
        ]
        profiles = {
            "default": [
                *common,
                *self._workspace_permission_mapping(network_access=False),
            ],
            "trusted-workspace": [
                *common,
                *self._workspace_permission_mapping(network_access=True),
            ],
            "full-access": [
                *common,
                "-c",
                'sandbox_mode="danger-full-access"',
                "-c",
                'approval_policy="never"',
            ],
        }
        return {
            profile: {"start": mapping.copy(), "resume": mapping.copy()}
            for profile, mapping in profiles.items()
        }

    def profile_fingerprint(
        self,
        profile: str,
        session_policy: str,
        launch_mode: str = "headless",
    ) -> str:
        base = super().profile_fingerprint(profile, session_policy, launch_mode)
        components = [
            base.encode("utf-8"),
            b"codex-frozen-model-provider-v1:env-name-only-bridge",
        ]
        if launch_mode == "interactive":
            components.append(
                b"codex-interactive-home-v3:"
                b"frozen-model-availability-nux-terminal-count-4"
            )
        framed = b"".join(
            len(component).to_bytes(8, "big") + component for component in components
        )
        return sha256_bytes(framed)

    @staticmethod
    def _interactive_home(run_dir: Path, role_id: str) -> Path:
        digest = sha256_bytes(os.fsencode(str(run_dir.resolve(strict=True))))
        return fixed_state_dir() / "harness-homes" / "codex" / digest / role_id

    @classmethod
    def _interactive_marker(cls, run_dir: Path, role_id: str) -> dict[str, object]:
        return {
            "schema_version": 1,
            "adapter": "codex",
            "run_dir": str(run_dir.resolve(strict=True)),
            "role_id": role_id,
        }

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
                f"Codex interactive state has an invalid role: {role_id}"
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
                f"Codex runtime state has invalid launch options for {role_id}: "
                f"{exc.message}"
            ) from exc
        return launch_options

    @classmethod
    def _role_model(cls, run_dir: Path, role_id: str) -> str | None:
        return cls._role_launch_options(run_dir, role_id).model

    @classmethod
    def _interactive_config(cls, run_dir: Path, role_id: str) -> bytes:
        workspace = workspace_from_run_dir(run_dir)
        model = cls._role_model(run_dir, role_id)
        quoted_workspace = json.dumps(str(workspace), ensure_ascii=False)
        lines = [
            f"[projects.{quoted_workspace}]",
            'trust_level = "trusted"',
        ]
        expected: dict[str, object] = {
            "projects": {str(workspace): {"trust_level": "trusted"}}
        }
        if model is not None:
            quoted_model = json.dumps(model, ensure_ascii=False)
            lines.extend(
                (
                    "",
                    "[tui.model_availability_nux]",
                    f"{quoted_model} = {_MODEL_AVAILABILITY_NUX_MAX_SHOW_COUNT}",
                )
            )
            expected["tui"] = {
                "model_availability_nux": {
                    model: _MODEL_AVAILABILITY_NUX_MAX_SHOW_COUNT
                }
            }
        config = ("\n".join(lines) + "\n").encode()
        # Keep this generated security boundary both minimal and syntactically
        # valid. The frozen model's native availability counter is preseeded
        # at its terminal value so Codex does not mutate config.toml after a
        # fresh interactive launch.
        # No mutable user MCP, Hook, Plugin, or permission setting is copied
        # into the isolated interactive home.
        parsed = tomllib.loads(config.decode("utf-8"))
        if parsed != expected:
            raise IntegrityError("generated Codex interactive config is invalid")
        return config

    def _ensure_private_interactive_home(
        self,
        run_dir: Path,
        role_id: str,
    ) -> Path:
        home = self._interactive_home(run_dir, role_id)
        state_dir = fixed_state_dir()
        hierarchy = (
            state_dir,
            state_dir / "harness-homes",
            state_dir / "harness-homes" / "codex",
            home.parent,
            home,
        )
        for directory in hierarchy:
            ensure_dir(directory)
            info = directory.lstat()
            if directory.is_symlink() or not stat.S_ISDIR(info.st_mode):
                raise IntegrityError(
                    f"Codex interactive state directory is unsafe: {directory}"
                )
            directory.chmod(0o700)
        return home

    def _prepare_interactive_config(
        self,
        run_dir: Path,
        role_id: str,
        home: Path,
    ) -> None:
        config_path = home / "config.toml"
        expected = self._interactive_config(run_dir, role_id)
        if not path_entry_exists(config_path):
            atomic_write(config_path, expected, immutable=True)
            return
        existing = read_regular(config_path)
        if existing == expected:
            return
        if existing != b"":
            raise IntegrityError(
                f"Codex interactive config has unexpected content: {config_path}"
            )
        # Versions before the trust-only config created an empty config after
        # committing this Run-owned marker. prepare_run_state is called only
        # for an UNSTARTED Run, so that exact legacy state can be migrated
        # without accepting or replacing any other pre-existing content.
        atomic_write(config_path, expected)

    @staticmethod
    def _source_codex_home() -> Path:
        configured = os.environ.get("CODEX_HOME")
        supplied = (
            Path(configured).expanduser()
            if configured
            else Path.home() / ".codex"
        )
        try:
            return supplied.resolve(strict=path_entry_exists(supplied))
        except OSError as exc:
            raise AgentTeamError(
                "HARNESS_USER_CONFIG_UNREADABLE",
                f"cannot resolve Codex home: {supplied}",
            ) from exc

    def _assert_interactive_authentication(self, home: Path) -> None:
        env = os.environ.copy()
        env["CODEX_HOME"] = str(home)
        try:
            result = subprocess.run(
                [str(self.executable()), "login", "status"],
                capture_output=True,
                text=True,
                check=False,
                timeout=5,
                env=env,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise AgentTeamError(
                "HARNESS_AUTH_PROBE_FAILED",
                "cannot verify authentication in the isolated Codex home",
            ) from exc
        if result.returncode != 0:
            raise AgentTeamError(
                "HARNESS_NOT_AUTHENTICATED",
                "Codex is not authenticated in the isolated interactive home",
            )

    def prepare_run_state(
        self,
        *,
        run_dir: Path,
        role_id: str,
        launch_mode: str,
    ) -> None:
        super().prepare_run_state(
            run_dir=run_dir,
            role_id=role_id,
            launch_mode=launch_mode,
        )
        options = self._role_launch_options(run_dir, role_id)
        self.assert_launch_prerequisites(options)
        if launch_mode != "interactive":
            return
        home = self._ensure_private_interactive_home(run_dir, role_id)
        marker_path = home / "agent-team-home.json"
        marker = self._interactive_marker(run_dir, role_id)
        if path_entry_exists(marker_path):
            if read_json(marker_path) != marker:
                raise IntegrityError(
                    f"Codex interactive home belongs to a different Run: {home}"
                )
        else:
            atomic_json(marker_path, marker, immutable=True)
        # A trust-only immutable config bypasses Codex's native workspace
        # confirmation without importing mutable user MCP, Hook, Plugin, or
        # permission settings into this Run-owned interactive home.
        self._prepare_interactive_config(run_dir, role_id, home)
        if not self.authentication_required(options):
            return
        source_auth = self._source_codex_home() / "auth.json"
        target_auth = home / "auth.json"
        if path_entry_exists(source_auth):
            try:
                source_real = source_auth.resolve(strict=True)
                source_info = source_real.stat()
            except OSError as exc:
                raise AgentTeamError(
                    "HARNESS_AUTH_STATE_UNREADABLE",
                    f"cannot resolve Codex authentication state: {source_auth}",
                ) from exc
            if not stat.S_ISREG(source_info.st_mode):
                raise AgentTeamError(
                    "HARNESS_AUTH_STATE_UNREADABLE",
                    f"Codex authentication state is not a regular file: {source_auth}",
                )
            if stat.S_IMODE(source_info.st_mode) & 0o077:
                raise AgentTeamError(
                    "HARNESS_AUTH_STATE_UNSAFE",
                    f"Codex authentication state is not private: {source_auth}",
                )
            if path_entry_exists(target_auth):
                target_info = target_auth.lstat()
                if target_auth.is_symlink() or not stat.S_ISREG(target_info.st_mode):
                    raise IntegrityError(
                        f"Codex interactive auth state is invalid: {target_auth}"
                    )
                if stat.S_IMODE(target_info.st_mode) & 0o077:
                    raise IntegrityError(
                        f"Codex interactive auth state is not private: {target_auth}"
                    )
            # Do not hard-link mutable credential state: an in-place Harness
            # update must never mutate the user's source auth file. Preflight
            # runs only while the Run is UNSTARTED, so retrying after login may
            # safely refresh a stale copy without changing an active Session.
            atomic_write(target_auth, read_regular(source_real), mode=0o600)
            fsync_dir(home)
        self._assert_interactive_authentication(home)

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
        home = self._interactive_home(run_dir, role_id)
        try:
            home_info = home.lstat()
        except OSError as exc:
            raise IntegrityError(
                f"Codex interactive state is unavailable: {home}"
            ) from exc
        if home.is_symlink() or not stat.S_ISDIR(home_info.st_mode):
            raise IntegrityError(f"Codex interactive state is unsafe: {home}")
        marker_path = home / "agent-team-home.json"
        if (
            not path_entry_exists(marker_path)
            or read_json(marker_path) != self._interactive_marker(run_dir, role_id)
        ):
            raise IntegrityError(
                f"Codex interactive state is not owned by this Run: {home}"
            )
        for directory in (
            fixed_state_dir(),
            fixed_state_dir() / "harness-homes",
            fixed_state_dir() / "harness-homes" / "codex",
            home.parent,
            home,
        ):
            info = directory.lstat()
            if directory.is_symlink() or not stat.S_ISDIR(info.st_mode):
                raise IntegrityError(
                    f"Codex interactive state directory is unsafe: {directory}"
                )
            directory.chmod(0o700)
        # Codex creates per-process wrapper symlinks below tmp and may assign
        # explicit 0755/0644 modes to caches despite the managed 0077 umask.
        # The process group is already proven quiescent here, so transient tmp
        # state can be removed and all durable state can be made account-only.
        temporary = home / "tmp"
        if path_entry_exists(temporary):
            temporary_info = temporary.lstat()
            if temporary.is_symlink() or not stat.S_ISDIR(temporary_info.st_mode):
                raise IntegrityError(
                    f"Codex interactive temporary state is unsafe: {temporary}"
                )
            shutil.rmtree(temporary)
            fsync_dir(home)

        for directory, child_dirs, files in os.walk(
            home,
            topdown=False,
            followlinks=False,
        ):
            current = Path(directory)
            for name in (*child_dirs, *files):
                path = current / name
                info = path.lstat()
                if path.is_symlink() or not (
                    stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)
                ):
                    raise IntegrityError(
                        f"Codex interactive state entry is unsafe: {path}"
                    )
                if stat.S_ISDIR(info.st_mode):
                    path.chmod(0o700)
                else:
                    owner_mode = stat.S_IMODE(info.st_mode) & 0o700
                    path.chmod(owner_mode or 0o600)
            current.chmod(0o700)

    def has_prepared_run_state(
        self,
        *,
        run_dir: Path,
        role_id: str,
        launch_mode: str,
    ) -> bool:
        self.assert_launch_mode(launch_mode)
        return launch_mode == "interactive" and path_entry_exists(
            self._interactive_home(run_dir, role_id)
        )

    def _assert_interactive_home(self, context: TurnLaunchContext) -> Path:
        run_dir = Path(context.turn_dir).parent.parent
        home = self._interactive_home(run_dir, context.role_id)
        try:
            home_info = home.lstat()
        except OSError as exc:
            raise AgentTeamError(
                "INTERACTIVE_STATE_NOT_PREPARED",
                f"Codex interactive state is unavailable for {context.role_id}",
            ) from exc
        if home.is_symlink() or not stat.S_ISDIR(home_info.st_mode):
            raise IntegrityError("Codex interactive home is unsafe")
        marker_path = home / "agent-team-home.json"
        if (
            not path_entry_exists(marker_path)
            or read_json(marker_path)
            != self._interactive_marker(run_dir, context.role_id)
        ):
            raise AgentTeamError(
                "INTERACTIVE_STATE_NOT_PREPARED",
                f"Codex interactive state is not prepared for {context.role_id}",
            )
        config = home / "config.toml"
        if not path_entry_exists(config) or config.is_symlink():
            raise IntegrityError("Codex interactive config is missing or unsafe")
        config_info = config.lstat()
        if (
            not stat.S_ISREG(config_info.st_mode)
            or stat.S_IMODE(config_info.st_mode) & 0o077
            or read_regular(config)
            != self._interactive_config(run_dir, context.role_id)
        ):
            raise IntegrityError("Codex interactive config is not isolated")
        return home

    def interactive_session_refs(self, launch: LaunchSpec) -> set[str]:
        if launch.launch_mode != "interactive":
            return set()
        supplied_home = launch.env.get("CODEX_HOME")
        if not supplied_home:
            raise IntegrityError("interactive Codex launch has no isolated home")
        sessions = Path(supplied_home) / "sessions"
        if not path_entry_exists(sessions):
            return set()
        if sessions.is_symlink() or not sessions.is_dir():
            raise IntegrityError("Codex interactive sessions path is unsafe")
        refs: set[str] = set()

        def walk_error(exc: OSError) -> None:
            raise exc

        for directory, child_dirs, files in os.walk(
            sessions,
            topdown=True,
            followlinks=False,
            onerror=walk_error,
        ):
            current = Path(directory)
            current_info = current.lstat()
            if current.is_symlink() or not stat.S_ISDIR(current_info.st_mode):
                raise IntegrityError(f"Codex session directory is unsafe: {current}")
            for child in child_dirs:
                child_path = current / child
                child_info = child_path.lstat()
                if child_path.is_symlink() or not stat.S_ISDIR(child_info.st_mode):
                    raise IntegrityError(
                        f"Codex session directory is unsafe: {child_path}"
                    )
            for filename in files:
                path = current / filename
                info = path.lstat()
                if path.is_symlink() or not stat.S_ISREG(info.st_mode):
                    raise IntegrityError(f"Codex session entry is unsafe: {path}")
                if path.suffix != ".jsonl":
                    continue
                fd = open_regular(path)
                try:
                    first_line = os.read(fd, 1024 * 1024).split(b"\n", 1)[0]
                finally:
                    os.close(fd)
                try:
                    event = json.loads(first_line)
                except (UnicodeDecodeError, json.JSONDecodeError):
                    # Codex may expose the new file before its first metadata
                    # record is complete. Ignoring it cannot authorize an
                    # action: Supervisor still requires one unambiguous ref.
                    continue
                payload = event.get("payload") if isinstance(event, dict) else None
                if (
                    not isinstance(payload, dict)
                    or event.get("type") != "session_meta"
                    or payload.get("cwd") != launch.cwd
                ):
                    continue
                session_ref = payload.get("id") or payload.get("session_id")
                if not isinstance(session_ref, str) or not session_ref:
                    raise IntegrityError(f"Codex session ref is invalid: {path}")
                refs.add(session_ref)
        return refs

    def assert_launch_options(self, options: HarnessLaunchOptions) -> None:
        if options.model is not None and not valid_model_id(options.model):
            raise InvalidArgument("codex model must be a non-empty model id")
        if (
            options.reasoning_effort is not None
            and options.reasoning_effort not in CODEX_REASONING_EFFORTS
        ):
            supported = ", ".join(sorted(CODEX_REASONING_EFFORTS))
            raise InvalidArgument(
                f"codex reasoning effort must be one of: {supported}"
            )
        if options.fast_mode is not None and not isinstance(
            options.fast_mode, bool
        ):
            raise InvalidArgument("codex fast mode must be a boolean")
        provider = options.model_provider
        provider_config = options.model_provider_config
        if provider is not None and not valid_model_provider_id(provider):
            raise InvalidArgument(
                "codex model provider must match [A-Za-z0-9][A-Za-z0-9_-]{0,63}"
            )
        if provider_config is not None:
            if provider is None:
                raise InvalidArgument("codex model provider config has no provider id")
            error = codex_model_provider_config_error(provider_config)
            if error is not None:
                raise InvalidArgument(f"codex model provider config {error}")
        if (
            provider in CODEX_BUILTIN_MODEL_PROVIDERS
            and provider_config is not None
        ):
            raise InvalidArgument(
                f"codex built-in model provider {provider!r} cannot be overridden"
            )
        if (
            provider is not None
            and provider not in CODEX_BUILTIN_MODEL_PROVIDERS
            and provider_config is None
        ):
            raise InvalidArgument(
                f"codex custom model provider {provider!r} has no frozen definition"
            )

    @staticmethod
    def authentication_required(options: HarnessLaunchOptions) -> bool:
        if options.model_provider in {None, "openai"}:
            return True
        config = options.model_provider_config
        return isinstance(config, dict) and config.get("requires_openai_auth") is True

    @staticmethod
    def _provider_environment_names(
        options: HarnessLaunchOptions,
    ) -> tuple[str, ...]:
        config = options.model_provider_config
        if not isinstance(config, dict):
            return ()
        names: set[str] = set()
        env_key = config.get("env_key")
        if isinstance(env_key, str):
            names.add(env_key)
        headers = config.get("env_http_headers")
        if isinstance(headers, dict):
            names.update(
                name for name in headers.values() if isinstance(name, str)
            )
        return tuple(sorted(names))

    def assert_launch_prerequisites(self, options: HarnessLaunchOptions) -> None:
        self.assert_launch_options(options)
        for name in self._provider_environment_names(options):
            if not os.environ.get(name):
                raise AgentTeamError(
                    "HARNESS_ENVIRONMENT_UNAVAILABLE",
                    f"Codex model provider {options.model_provider!r} requires "
                    f"non-empty environment variable {name!r}",
                )
        if (
            self.authentication_required(options)
            and self.authentication_status() is False
        ):
            raise AgentTeamError(
                "HARNESS_NOT_AUTHENTICATED",
                "codex is not authenticated for the selected model provider",
            )

    def worker_environment_names(
        self,
        *,
        run_dir: Path,
        role_id: str,
        options: HarnessLaunchOptions | None = None,
    ) -> tuple[str, ...]:
        selected = options or self._role_launch_options(run_dir, role_id)
        self.assert_launch_options(selected)
        return self._provider_environment_names(selected)

    @staticmethod
    def _user_config_path() -> Path:
        configured_home = os.environ.get("CODEX_HOME")
        base = (
            Path(configured_home).expanduser()
            if configured_home
            else Path.home() / ".codex"
        )
        return base / "config.toml"

    def _user_launch_options(
        self,
        *,
        include_model: bool,
        include_reasoning_effort: bool,
        include_fast_mode: bool,
        include_model_provider: bool,
        selected_model_provider: str | None,
    ) -> HarnessLaunchOptions:
        needs_custom_provider = (
            not include_model_provider
            and selected_model_provider not in CODEX_BUILTIN_MODEL_PROVIDERS
        )
        if not (
            include_model
            or include_reasoning_effort
            or include_fast_mode
            or include_model_provider
            or needs_custom_provider
        ):
            return HarnessLaunchOptions(model_provider=selected_model_provider)
        path = self._user_config_path()
        try:
            raw = path.read_bytes()
        except FileNotFoundError:
            value: dict[str, object] = {}
        except OSError as exc:
            raise AgentTeamError(
                "HARNESS_USER_CONFIG_UNREADABLE",
                f"cannot read Codex user config {path}: {exc}",
            ) from exc
        else:
            try:
                value = tomllib.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
                raise AgentTeamError(
                    "HARNESS_USER_CONFIG_INVALID",
                    f"Codex user config is invalid: {path}",
                ) from exc
        model = value.get("model") if include_model else None
        effort = (
            value.get("model_reasoning_effort")
            if include_reasoning_effort
            else None
        )
        service_tier = value.get("service_tier") if include_fast_mode else None
        features = value.get("features", {}) if include_fast_mode else {}
        provider = (
            value.get("model_provider", "openai")
            if include_model_provider
            else selected_model_provider
        )
        if model is not None and not isinstance(model, str):
            raise AgentTeamError(
                "HARNESS_USER_CONFIG_INVALID",
                "Codex user config model must be a string",
            )
        if effort is not None and not isinstance(effort, str):
            raise AgentTeamError(
                "HARNESS_USER_CONFIG_INVALID",
                "Codex user config model_reasoning_effort must be a string",
            )
        if service_tier is not None and not isinstance(service_tier, str):
            raise AgentTeamError(
                "HARNESS_USER_CONFIG_INVALID",
                "Codex user config service_tier must be a string",
            )
        if not isinstance(features, dict):
            raise AgentTeamError(
                "HARNESS_USER_CONFIG_INVALID",
                "Codex user config features must be a table",
            )
        if not valid_model_provider_id(provider):
            raise AgentTeamError(
                "HARNESS_USER_CONFIG_INVALID",
                "Codex user config model_provider must be a valid provider id",
            )
        assert isinstance(provider, str)
        provider_config: dict[str, object] | None = None
        providers = value.get("model_providers", {})
        if provider in CODEX_BUILTIN_MODEL_PROVIDERS:
            if isinstance(providers, dict) and provider in providers:
                raise AgentTeamError(
                    "HARNESS_USER_CONFIG_INVALID",
                    f"Codex built-in model provider {provider!r} cannot be overridden",
                )
        else:
            if not isinstance(providers, dict):
                raise AgentTeamError(
                    "HARNESS_USER_CONFIG_INVALID",
                    "Codex user config model_providers must be a table",
                )
            configured = providers.get(provider)
            if not isinstance(configured, dict):
                raise AgentTeamError(
                    "HARNESS_USER_CONFIG_INVALID",
                    f"Codex model provider {provider!r} is not defined",
                )
            unsupported = (
                set(configured)
                - CODEX_MODEL_PROVIDER_CONFIG_KEYS
                - _IGNORED_MODEL_PROVIDER_FIELDS
            )
            if unsupported:
                raise AgentTeamError(
                    "HARNESS_PROVIDER_CONFIG_UNSUPPORTED",
                    f"Codex model provider {provider!r} uses unsupported or "
                    "secret-bearing fields: "
                    + ", ".join(sorted(unsupported)),
                )
            provider_config = {
                key: (
                    dict(sorted(item.items())) if isinstance(item, dict) else item
                )
                for key, item in configured.items()
                if key in CODEX_MODEL_PROVIDER_CONFIG_KEYS
            }
            provider_config.setdefault("wire_api", "responses")
        feature_enabled = features.get("fast_mode", True)
        if not isinstance(feature_enabled, bool):
            raise AgentTeamError(
                "HARNESS_USER_CONFIG_INVALID",
                "Codex user config features.fast_mode must be a boolean",
            )
        options = HarnessLaunchOptions(
            model=model,
            reasoning_effort=effort,
            fast_mode=(
                service_tier == "fast" and feature_enabled
                if include_fast_mode
                else False
            ),
            model_provider=provider,
            model_provider_config=provider_config,
        )
        try:
            self.assert_launch_options(options)
        except InvalidArgument as exc:
            raise AgentTeamError(
                "HARNESS_USER_CONFIG_INVALID",
                f"Codex user config is invalid: {exc.message}",
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
        del workspace
        explicit = HarnessLaunchOptions(
            model=model,
            reasoning_effort=reasoning_effort,
            fast_mode=fast_mode,
        )
        self.assert_launch_options(explicit)
        if model_provider is not None and not valid_model_provider_id(model_provider):
            raise InvalidArgument(
                "codex model provider must match [A-Za-z0-9][A-Za-z0-9_-]{0,63}"
            )
        defaults = self._user_launch_options(
            include_model=model is None,
            include_reasoning_effort=reasoning_effort is None,
            include_fast_mode=fast_mode is None,
            include_model_provider=model_provider is None,
            selected_model_provider=model_provider,
        )
        options = HarnessLaunchOptions(
            model=model if model is not None else defaults.model,
            reasoning_effort=(
                reasoning_effort
                if reasoning_effort is not None
                else defaults.reasoning_effort
            ),
            fast_mode=(
                fast_mode if fast_mode is not None else defaults.fast_mode
            ),
            model_provider=defaults.model_provider,
            model_provider_config=defaults.model_provider_config,
        )
        self.assert_launch_options(options)
        return options

    @classmethod
    def _toml_literal(cls, value: object) -> str:
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, int):
            return str(value)
        if isinstance(value, str):
            return json.dumps(value, ensure_ascii=False)
        if isinstance(value, dict):
            entries = []
            for key, item in sorted(value.items()):
                if not isinstance(key, str):
                    raise IntegrityError("Codex provider config key is not a string")
                entries.append(
                    f"{json.dumps(key, ensure_ascii=False)} = "
                    f"{cls._toml_literal(item)}"
                )
            return "{ " + ", ".join(entries) + " }"
        raise IntegrityError("Codex provider config contains an unsupported value")

    @classmethod
    def _provider_selection(cls, options: HarnessLaunchOptions) -> list[str]:
        provider = options.model_provider
        if provider is None:
            return []
        selection = ["-c", f"model_provider={cls._toml_literal(provider)}"]
        config = options.model_provider_config
        if config is None:
            return selection
        for key, value in sorted(config.items()):
            selection.extend(
                (
                    "-c",
                    f"model_providers.{provider}.{key}={cls._toml_literal(value)}",
                )
            )
        return selection

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
        executable = str(self.executable())
        mapping = self.profile_mappings(context.launch_mode)[context.launch_profile]
        selection = self._provider_selection(options)
        if options.model is not None:
            selection.extend(("--model", options.model))
        if options.reasoning_effort is not None:
            selection.extend(
                (
                    "-c",
                    f'model_reasoning_effort="{options.reasoning_effort}"',
                )
            )
        if options.fast_mode:
            selection.extend(("-c", 'service_tier="fast"', "--enable", "fast_mode"))
        env = {
            "AGENT_TEAM_RUN_ID": context.run_id,
            "AGENT_TEAM_ROLE_ID": context.role_id,
            "AGENT_TEAM_TURN_ID": context.turn_id,
            "AGENT_TEAM_RUN_DIR": str(Path(context.turn_dir).parent.parent),
            "AGENT_TEAM_TURN_DIR": context.turn_dir,
            "AGENT_TEAM_CLI": context.agent_team_cli,
        }
        prompt_file: str | None = None
        expected_session_ref: str | None = None
        if context.launch_mode == "interactive":
            home = self._assert_interactive_home(context)
            env["CODEX_HOME"] = str(home)
            prompt_file = str(Path(context.turn_dir) / "process" / "prompt.md")
            if context.session_ref and context.session_policy == "resume":
                argv = (
                    executable,
                    "resume",
                    *mapping["resume"],
                    *selection,
                    "--no-alt-screen",
                    context.session_ref,
                )
                starts_new = False
                expected_session_ref = context.session_ref
            else:
                argv = (
                    executable,
                    *mapping["start"],
                    *selection,
                    "--no-alt-screen",
                    "-C",
                    context.workspace,
                )
                starts_new = True
        else:
            output = str(Path(context.turn_dir) / "output.md")
            if context.session_ref and context.session_policy == "resume":
                argv = (
                    executable,
                    "exec",
                    "resume",
                    "--json",
                    *mapping["resume"],
                    *selection,
                    "-o",
                    output,
                    context.session_ref,
                    "-",
                )
                starts_new = False
            else:
                argv = (
                    executable,
                    "exec",
                    "--json",
                    "--color",
                    "never",
                    *mapping["start"],
                    *selection,
                    "-C",
                    context.workspace,
                    "-o",
                    output,
                    "-",
                )
                starts_new = True
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
            expected_session_ref=expected_session_ref,
        )

    def parse_stream_record(
        self, record: StreamRecord
    ) -> AdapterEvidence | None:
        value = self.parse_json_record(record)
        if value is None:
            return None
        kind = value.get("type")
        if kind == "thread.started":
            thread_id = value.get("thread_id")
            return AdapterEvidence(
                agent_execution_started=True,
                observed_session_ref=thread_id if isinstance(thread_id, str) else None,
            )
        if kind == "turn.started":
            return AdapterEvidence(agent_execution_started=True)
        if kind == "turn.completed":
            return AdapterEvidence(
                agent_execution_started=True,
                adapter_completed=True,
            )
        if isinstance(kind, str) and kind in {
            "approval.required",
            "permission.required",
        }:
            return AdapterEvidence(permission_required=True)
        if kind == "error":
            error = value.get("error")
            error_code = error.get("code") if isinstance(error, dict) else None
            if isinstance(error_code, str) and error_code in {
                "permission_denied",
                "approval_required",
            }:
                return AdapterEvidence(permission_required=True)
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
        if kind == "thread.started":
            return [
                NormalizedTraceEvent(
                    "session",
                    {"session_ref": value.get("thread_id"), "state": "started"},
                )
            ]
        if kind == "turn.started":
            return [NormalizedTraceEvent("turn", {"state": "started"})]
        if kind == "turn.completed":
            return [
                NormalizedTraceEvent(
                    "usage",
                    {
                        "state": "completed",
                        "usage": value.get("usage", {}),
                    },
                )
            ]
        if kind in {"item.started", "item.completed"}:
            item = value.get("item")
            if not isinstance(item, dict):
                return super().normalize_stream_record(record)
            item_type = item.get("type")
            state = "started" if kind == "item.started" else "completed"
            if item_type == "agent_message":
                return [
                    NormalizedTraceEvent(
                        "agent_message",
                        {
                            "item_id": item.get("id"),
                            "state": state,
                            "direction": "assistant",
                            "text": item.get("text", ""),
                        },
                    )
                ]
            if item_type == "command_execution":
                event_type = "tool_call" if state == "started" else "tool_result"
                return [
                    NormalizedTraceEvent(
                        event_type,
                        {
                            "item_id": item.get("id"),
                            "tool": "command_execution",
                            "state": state,
                            "command": item.get("command"),
                            "output": item.get("aggregated_output"),
                            "exit_code": item.get("exit_code"),
                            "status": item.get("status"),
                        },
                    )
                ]
            if item_type == "file_change":
                return [
                    NormalizedTraceEvent(
                        "file_change",
                        {
                            "item_id": item.get("id"),
                            "state": state,
                            "changes": item.get("changes"),
                            "status": item.get("status"),
                        },
                    )
                ]
            if item_type in {"reasoning", "reasoning_summary"}:
                return [
                    NormalizedTraceEvent(
                        "reasoning_summary",
                        {
                            "item_id": item.get("id"),
                            "state": state,
                            "text": item.get("text") or item.get("summary") or "",
                        },
                    )
                ]
            if item_type in {
                "collab_agent_tool_call",
                "dynamic_tool_call",
                "image_generation",
                "image_view",
                "mcp_tool_call",
                "web_search",
            }:
                event_type = "tool_call" if state == "started" else "tool_result"
                return [
                    NormalizedTraceEvent(
                        event_type,
                        {
                            "item_id": item.get("id"),
                            "tool": item_type,
                            "state": state,
                            "input": (
                                item.get("arguments")
                                or item.get("input")
                                or item.get("query")
                                or item.get("action")
                            ),
                            "output": (
                                item.get("result")
                                or item.get("output")
                                or item.get("content")
                            ),
                            "status": item.get("status"),
                            "payload": item,
                        },
                    )
                ]
            if item_type == "user_message":
                return [
                    NormalizedTraceEvent(
                        "agent_message",
                        {
                            "item_id": item.get("id"),
                            "state": state,
                            "direction": "user",
                            "text": item.get("text") or item.get("content") or "",
                        },
                    )
                ]
            return [
                NormalizedTraceEvent(
                    "harness_event",
                    {
                        "source": record.source,
                        "kind": kind,
                        "payload": value,
                    },
                )
            ]
        if kind == "error":
            return [NormalizedTraceEvent("error", {"payload": value.get("error")})]
        return super().normalize_stream_record(record)
