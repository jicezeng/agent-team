from __future__ import annotations

import json
import os
import stat
import subprocess
import tomllib
from pathlib import Path

from agent_team.config import CODEX_REASONING_EFFORTS, valid_model_id
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
    HarnessLaunchOptions,
    HarnessAdapter,
    LaunchSpec,
    NormalizedTraceEvent,
    StreamRecord,
    TurnLaunchContext,
)


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
        state_dir = fixed_state_dir()
        writable_roots = [
            str(state_dir / "workspace-locks"),
            str(state_dir / "workspaces"),
        ]
        roots_value = json.dumps(
            writable_roots,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        return [
            "-c",
            'sandbox_mode="workspace-write"',
            "-c",
            'approval_policy="never"',
            "-c",
            f"sandbox_workspace_write.writable_roots={roots_value}",
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
        common = (
            ["--ignore-user-config", "--ignore-rules"]
            if launch_mode == "headless"
            else []
        )
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
        if launch_mode != "interactive":
            return
        home = self._interactive_home(run_dir, role_id)
        ensure_dir(home)
        marker_path = home / "agent-team-home.json"
        marker = self._interactive_marker(run_dir, role_id)
        if path_entry_exists(marker_path):
            if read_json(marker_path) != marker:
                raise IntegrityError(
                    f"Codex interactive home belongs to a different Run: {home}"
                )
        else:
            atomic_json(marker_path, marker, immutable=True)
        # An empty immutable config prevents the interactive entry point from
        # importing mutable user MCP, Hook, Plugin, or permission settings.
        atomic_write(home / "config.toml", b"", immutable=True)
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
            or read_regular(config) != b""
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
    ) -> HarnessLaunchOptions:
        path = self._user_config_path()
        try:
            raw = path.read_bytes()
        except FileNotFoundError:
            return HarnessLaunchOptions(
                fast_mode=False if include_fast_mode else None
            )
        except OSError as exc:
            raise AgentTeamError(
                "HARNESS_USER_CONFIG_UNREADABLE",
                f"cannot read Codex user config {path}: {exc}",
            ) from exc
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
                else None
            ),
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
    ) -> HarnessLaunchOptions:
        explicit = HarnessLaunchOptions(
            model=model,
            reasoning_effort=reasoning_effort,
            fast_mode=fast_mode,
        )
        self.assert_launch_options(explicit)
        defaults = (
            self._user_launch_options(
                include_model=model is None,
                include_reasoning_effort=reasoning_effort is None,
                include_fast_mode=fast_mode is None,
            )
            if model is None or reasoning_effort is None or fast_mode is None
            else HarnessLaunchOptions()
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
        )
        self.assert_launch_options(options)
        return options

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
        )
        self.assert_launch_options(options)
        executable = str(self.executable())
        mapping = self.profile_mappings(context.launch_mode)[context.launch_profile]
        selection: list[str] = []
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
