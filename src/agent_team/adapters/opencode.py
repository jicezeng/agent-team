from __future__ import annotations

import json
import os
import re
import stat
import subprocess
from pathlib import Path
from typing import Any

from agent_team.assets import effective_agent_team_cli
from agent_team.config import valid_opencode_model_id, valid_opencode_variant
from agent_team.errors import AgentTeamError, IntegrityError, InvalidArgument
from agent_team.state import fixed_state_dir
from agent_team.util import (
    atomic_json,
    canonical_json_bytes,
    ensure_dir,
    path_entry_exists,
    read_json,
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
)

_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_RUNTIME_AGENT = "agent-team-runtime"
_TERMINAL_FINISH_REASONS = frozenset(
    {"stop", "length", "content-filter", "unknown"}
)


class OpenCodeAdapter(HarnessAdapter):
    """OpenCode 1.x adapter with native interactive and JSON-stream execution."""

    adapter_id = "opencode"
    executable_name = "opencode"

    @staticmethod
    def _formal_command_patterns() -> tuple[list[str], list[str]]:
        cli = str(effective_agent_team_cli())
        formal = [
            f"{cli} handoff *",
            f"{cli} complete *",
            f"{cli} block *",
        ]
        forbidden = [
            f"{cli} cancel *",
            f"{cli} recover *",
            f"{cli} unlock *",
            f"{cli} init *",
            f"{cli} start *",
            f"{cli} install *",
            f"{cli} origin-*",
            f"{cli} _*",
        ]
        return formal, forbidden

    @classmethod
    def _permission_mapping(cls, profile: str) -> dict[str, object]:
        formal, forbidden = cls._formal_command_patterns()
        if profile in {"default", "trusted-workspace"}:
            # OpenCode has no OS sandbox around its Bash tool. Keep built-in
            # file operations within the worktree and expose only the three
            # exact state-transition commands through Bash; pretending that a
            # broad shell allowlist is workspace-contained would be unsafe.
            bash: dict[str, str] = {"*": "deny"}
            bash.update({pattern: "allow" for pattern in formal})
            permission: dict[str, object] = {
                "*": "deny",
                "read": "allow",
                "edit": "allow",
                "glob": "allow",
                "grep": "allow",
                "list": "allow",
                "lsp": "allow",
                "todowrite": "allow",
                "bash": bash,
                "external_directory": "deny",
                "task": "deny",
                "skill": "deny",
                "question": "deny",
                "doom_loop": "deny",
            }
            if profile == "trusted-workspace":
                permission["webfetch"] = "allow"
                permission["websearch"] = "allow"
            return permission
        if profile == "full-access":
            bash = {"*": "allow"}
            bash.update({pattern: "deny" for pattern in forbidden})
            return {"*": "allow", "bash": bash}
        raise AgentTeamError(
            "UNKNOWN_LAUNCH_PROFILE",
            f"opencode profile {profile!r} is not supported",
        )

    @classmethod
    def _runtime_config(
        cls,
        profile: str,
        *,
        model: str | None = None,
        variant: str | None = None,
    ) -> dict[str, object]:
        permission = cls._permission_mapping(profile)
        agent: dict[str, object] = {
            "description": "Isolated Agent-Team external role runtime",
            "mode": "primary",
            "permission": permission,
        }
        if model is not None:
            agent["model"] = model
        if variant is not None:
            agent["variant"] = variant
        return {
            "$schema": "https://opencode.ai/config.json",
            "autoupdate": False,
            "share": "disabled",
            "default_agent": _RUNTIME_AGENT,
            "permission": permission,
            "agent": {_RUNTIME_AGENT: agent},
        }

    def profile_mappings(
        self,
        launch_mode: str = "headless",
    ) -> dict[str, dict[str, list[str]]]:
        self.assert_launch_mode(launch_mode)
        fixed = ["--pure", "--auto", "--agent", _RUNTIME_AGENT]
        return {
            profile: {"start": fixed.copy(), "resume": fixed.copy()}
            for profile in ("default", "trusted-workspace", "full-access")
        }

    def probe(self) -> CapabilityReport:
        report = super().probe()
        details = dict(report.details)
        details["runtime_isolation"] = {
            "xdg_config_home": "private per Run and role",
            "project_config": "disabled",
            "external_plugins": "disabled by --pure",
            "user_data_and_auth": "OpenCode account data remains available",
        }
        details["inline_config_mappings"] = {
            profile: self._runtime_config(profile)
            for profile in ("default", "trusted-workspace", "full-access")
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
            "inline_config": self._runtime_config(profile),
            "environment": {
                "OPENCODE_CONFIG_CONTENT": "canonical inline_config",
                "OPENCODE_DISABLE_AUTOUPDATE": "1",
                "OPENCODE_DISABLE_PROJECT_CONFIG": "1",
                "XDG_CONFIG_HOME": "private per Run and role",
            },
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
                [str(self.executable()), "providers", "list", "--pure"],
                capture_output=True,
                text=True,
                check=False,
                timeout=10,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        if result.returncode != 0:
            return None
        text = _ANSI_ESCAPE_RE.sub("", result.stdout + result.stderr)
        credentials = re.search(r"\b(\d+)\s+credentials?\b", text)
        environment = re.search(r"\b(\d+)\s+environment variables?\b", text)
        if credentials is None or environment is None:
            return None
        return int(credentials.group(1)) + int(environment.group(1)) > 0

    def _resolved_user_model(self, workspace: Path | None) -> str | None:
        cwd = workspace or Path.cwd()
        try:
            result = subprocess.run(
                [str(self.executable()), "debug", "config", "--pure"],
                cwd=cwd,
                capture_output=True,
                text=True,
                check=False,
                timeout=15,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise AgentTeamError(
                "HARNESS_CONFIG_PROBE_FAILED",
                "cannot resolve the OpenCode model default",
            ) from exc
        if result.returncode != 0:
            raise AgentTeamError(
                "HARNESS_CONFIG_PROBE_FAILED",
                "opencode debug config failed while resolving the model default",
            )
        try:
            value = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise AgentTeamError(
                "HARNESS_USER_CONFIG_INVALID",
                "opencode debug config did not return a JSON object",
            ) from exc
        model = value.get("model") if isinstance(value, dict) else None
        return model if isinstance(model, str) and model else None

    def resolve_launch_options(
        self,
        *,
        model: str | None,
        reasoning_effort: str | None,
        fast_mode: bool | None,
        workspace: Path | None = None,
    ) -> HarnessLaunchOptions:
        resolved_model = model if model is not None else self._resolved_user_model(workspace)
        options = HarnessLaunchOptions(
            model=resolved_model,
            reasoning_effort=reasoning_effort,
            fast_mode=fast_mode,
        )
        self.assert_launch_options(options)
        return options

    def assert_launch_options(self, options: HarnessLaunchOptions) -> None:
        if not valid_opencode_model_id(options.model):
            raise InvalidArgument(
                "opencode requires a model in provider/model form; set "
                "--role-model ROLE=provider/model when the user default is absent "
                "or unqualified"
            )
        if options.reasoning_effort is not None and not valid_opencode_variant(
            options.reasoning_effort
        ):
            raise InvalidArgument(
                "opencode reasoning effort must be a non-empty provider-specific "
                "variant without control characters or '#': for example high or max"
            )
        if options.fast_mode is not None:
            raise InvalidArgument("fast mode is only supported by the codex adapter")

    @staticmethod
    def _config_home(run_dir: Path, role_id: str) -> Path:
        digest = sha256_bytes(os.fsencode(str(run_dir.resolve(strict=True))))
        return fixed_state_dir() / "harness-homes" / "opencode" / digest / role_id

    @classmethod
    def _home_marker(cls, run_dir: Path, role_id: str) -> dict[str, object]:
        return {
            "schema_version": 1,
            "adapter": "opencode",
            "run_dir": str(run_dir.resolve(strict=True)),
            "role_id": role_id,
        }

    @classmethod
    def _home_hierarchy(cls, run_dir: Path, role_id: str) -> tuple[Path, ...]:
        home = cls._config_home(run_dir, role_id)
        state = fixed_state_dir()
        return (
            state,
            state / "harness-homes",
            state / "harness-homes" / "opencode",
            home.parent,
            home,
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
        for directory in self._home_hierarchy(run_dir, role_id):
            ensure_dir(directory)
            info = directory.lstat()
            if directory.is_symlink() or not stat.S_ISDIR(info.st_mode):
                raise IntegrityError(
                    f"OpenCode configuration directory is unsafe: {directory}"
                )
            directory.chmod(0o700)
        home = self._config_home(run_dir, role_id)
        marker_path = home / "agent-team-home.json"
        marker = self._home_marker(run_dir, role_id)
        if path_entry_exists(marker_path):
            if read_json(marker_path) != marker:
                raise IntegrityError(
                    f"OpenCode configuration home belongs to another Run: {home}"
                )
        else:
            atomic_json(marker_path, marker, immutable=True)

    def _assert_config_home(self, context: TurnLaunchContext) -> Path:
        run_dir = Path(context.turn_dir).parent.parent
        home = self._config_home(run_dir, context.role_id)
        try:
            info = home.lstat()
        except OSError as exc:
            raise AgentTeamError(
                "HARNESS_STATE_NOT_PREPARED",
                f"OpenCode configuration state is unavailable for {context.role_id}",
            ) from exc
        if home.is_symlink() or not stat.S_ISDIR(info.st_mode):
            raise IntegrityError("OpenCode configuration home is unsafe")
        marker_path = home / "agent-team-home.json"
        if (
            not path_entry_exists(marker_path)
            or read_json(marker_path)
            != self._home_marker(run_dir, context.role_id)
        ):
            raise AgentTeamError(
                "HARNESS_STATE_NOT_PREPARED",
                f"OpenCode configuration state is not prepared for {context.role_id}",
            )
        return home

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
        home = self._config_home(run_dir, role_id)
        try:
            info = home.lstat()
        except OSError as exc:
            raise IntegrityError(
                f"OpenCode configuration state is unavailable: {home}"
            ) from exc
        if home.is_symlink() or not stat.S_ISDIR(info.st_mode):
            raise IntegrityError(f"OpenCode configuration state is unsafe: {home}")
        marker_path = home / "agent-team-home.json"
        if (
            not path_entry_exists(marker_path)
            or read_json(marker_path) != self._home_marker(run_dir, role_id)
        ):
            raise IntegrityError(
                f"OpenCode configuration state is not owned by this Run: {home}"
            )
        resolved_home = home.resolve(strict=True)
        for directory, child_dirs, files in os.walk(
            home,
            topdown=False,
            followlinks=False,
        ):
            current = Path(directory)
            for name in (*child_dirs, *files):
                path = current / name
                path_info = path.lstat()
                if stat.S_ISLNK(path_info.st_mode):
                    try:
                        target = path.resolve(strict=True)
                        target.relative_to(resolved_home)
                    except (OSError, RuntimeError, ValueError) as exc:
                        raise IntegrityError(
                            f"OpenCode configuration symlink escapes its home: {path}"
                        ) from exc
                    continue
                if stat.S_ISDIR(path_info.st_mode):
                    path.chmod(0o700)
                elif stat.S_ISREG(path_info.st_mode):
                    owner_mode = stat.S_IMODE(path_info.st_mode) & 0o700
                    path.chmod(owner_mode or 0o600)
                else:
                    raise IntegrityError(
                        f"OpenCode configuration entry is unsafe: {path}"
                    )
            current.chmod(0o700)
        for directory in self._home_hierarchy(run_dir, role_id):
            directory.chmod(0o700)

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
        home = self._assert_config_home(context)
        executable = str(self.executable())
        mapping = self.profile_mappings(context.launch_mode)[context.launch_profile]
        model = options.model
        assert model is not None
        config = self._runtime_config(
            context.launch_profile,
            model=model,
            variant=options.reasoning_effort,
        )
        env = {
            "AGENT_TEAM_RUN_ID": context.run_id,
            "AGENT_TEAM_ROLE_ID": context.role_id,
            "AGENT_TEAM_TURN_ID": context.turn_id,
            "AGENT_TEAM_RUN_DIR": str(Path(context.turn_dir).parent.parent),
            "AGENT_TEAM_TURN_DIR": context.turn_dir,
            "AGENT_TEAM_CLI": context.agent_team_cli,
            "OPENCODE_CONFIG_CONTENT": canonical_json_bytes(config).decode(),
            "OPENCODE_DISABLE_AUTOUPDATE": "1",
            "OPENCODE_DISABLE_PROJECT_CONFIG": "1",
            "XDG_CONFIG_HOME": str(home),
        }
        prompt_file: str | None = None
        expected_session_ref: str | None = None
        if context.launch_mode == "interactive":
            prompt_file = str(Path(context.turn_dir) / "process" / "prompt.md")
            base = (
                executable,
                "run",
                "--interactive",
                *mapping["start"],
                "--dir",
                context.workspace,
                "--model",
                model,
            )
            if options.reasoning_effort is not None:
                base = (*base, "--variant", options.reasoning_effort)
            if context.session_ref and context.session_policy == "resume":
                argv = (*base, "--session", context.session_ref)
                starts_new = False
                expected_session_ref = context.session_ref
            else:
                argv = base
                starts_new = True
        else:
            base = (
                executable,
                "run",
                *mapping["start"],
                "--format",
                "json",
                "--dir",
                context.workspace,
                "--model",
                model,
            )
            if options.reasoning_effort is not None:
                base = (*base, "--variant", options.reasoning_effort)
            if context.session_ref and context.session_policy == "resume":
                argv = (*base, "--session", context.session_ref)
                starts_new = False
            else:
                argv = base
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

    def interactive_session_refs(self, launch: LaunchSpec) -> set[str]:
        if launch.launch_mode != "interactive":
            return set()
        env = os.environ.copy()
        env.update(launch.env)
        try:
            result = subprocess.run(
                [
                    str(self.executable()),
                    "session",
                    "list",
                    "--pure",
                    "--format",
                    "json",
                    "--max-count",
                    "1000",
                ],
                cwd=launch.cwd,
                env=env,
                capture_output=True,
                text=True,
                check=False,
                timeout=15,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise AgentTeamError(
                "HARNESS_SESSION_PROBE_FAILED",
                "cannot list OpenCode sessions",
            ) from exc
        if result.returncode != 0:
            raise AgentTeamError(
                "HARNESS_SESSION_PROBE_FAILED",
                "opencode session list failed",
            )
        output = result.stdout.strip()
        if not output:
            value: object = []
        else:
            try:
                value = json.loads(output)
            except json.JSONDecodeError as exc:
                raise IntegrityError("OpenCode session list is not valid JSON") from exc
        if not isinstance(value, list):
            raise IntegrityError("OpenCode session list must be an array")
        workspace = Path(launch.cwd).resolve(strict=True)
        refs: set[str] = set()
        for item in value:
            if not isinstance(item, dict):
                raise IntegrityError("OpenCode session entry must be an object")
            session_ref = item.get("id")
            directory = item.get("directory")
            if not isinstance(session_ref, str) or not session_ref:
                raise IntegrityError("OpenCode session id is invalid")
            if not isinstance(directory, str) or not directory:
                raise IntegrityError("OpenCode session directory is invalid")
            try:
                candidate = Path(directory).resolve(strict=True)
            except OSError:
                continue
            if candidate == workspace:
                refs.add(session_ref)
        return refs

    @staticmethod
    def _session_ref(value: dict[str, Any]) -> str | None:
        session_ref = value.get("sessionID")
        return session_ref if isinstance(session_ref, str) and session_ref else None

    @staticmethod
    def _permission_error(value: object) -> bool:
        try:
            text = json.dumps(value, ensure_ascii=False).lower()
        except (TypeError, ValueError):
            text = str(value).lower()
        return "permission" in text and any(
            marker in text
            for marker in ("denied", "required", "rejected", "not allowed")
        )

    def parse_stream_record(self, record: StreamRecord) -> AdapterEvidence | None:
        if record.encoding == "utf-8" and record.source == "stderr":
            text = _ANSI_ESCAPE_RE.sub("", record.data).strip()
            if text == "Error: Session not found":
                return AdapterEvidence(
                    session_unavailable_reason="session_not_found"
                )
        value = self.parse_json_record(record)
        if value is None:
            return None
        kind = value.get("type")
        session_ref = self._session_ref(value)
        if kind in {"step_start", "text", "reasoning", "reasoning_summary"}:
            return AdapterEvidence(
                agent_execution_started=True,
                observed_session_ref=session_ref,
            )
        if kind == "tool_use":
            if self._permission_error(value.get("part")):
                return AdapterEvidence(
                    agent_execution_started=True,
                    permission_required=True,
                    observed_session_ref=session_ref,
                )
            return AdapterEvidence(
                agent_execution_started=True,
                observed_session_ref=session_ref,
            )
        if kind == "step_finish":
            part = value.get("part")
            reason = part.get("reason") if isinstance(part, dict) else None
            return AdapterEvidence(
                agent_execution_started=True,
                adapter_completed=reason in _TERMINAL_FINISH_REASONS,
                observed_session_ref=session_ref,
            )
        if kind == "error" and self._permission_error(value.get("error")):
            return AdapterEvidence(
                permission_required=True,
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
        part = value.get("part")
        part = part if isinstance(part, dict) else {}
        session_ref = self._session_ref(value)
        if kind == "step_start":
            return [
                NormalizedTraceEvent(
                    "session",
                    {
                        "state": "started",
                        "session_ref": session_ref,
                        "message_id": part.get("messageID"),
                    },
                )
            ]
        if kind == "text":
            return [
                NormalizedTraceEvent(
                    "agent_message",
                    {
                        "session_ref": session_ref,
                        "direction": "assistant",
                        "text": part.get("text", ""),
                    },
                )
            ]
        if kind == "tool_use":
            state = part.get("state")
            state = state if isinstance(state, dict) else {}
            status = state.get("status")
            call = NormalizedTraceEvent(
                "tool_call",
                {
                    "session_ref": session_ref,
                    "tool_call_id": part.get("callID"),
                    "tool": part.get("tool"),
                    "input": state.get("input"),
                    "status": status,
                },
            )
            if status not in {"completed", "error"}:
                return [call]
            return [
                call,
                NormalizedTraceEvent(
                    "tool_result",
                    {
                        "session_ref": session_ref,
                        "tool_call_id": part.get("callID"),
                        "tool": part.get("tool"),
                        "status": status,
                        "output": state.get("output"),
                        "error": state.get("error"),
                        "metadata": state.get("metadata"),
                    },
                ),
            ]
        if kind == "reasoning_summary":
            return [
                NormalizedTraceEvent(
                    "reasoning_summary",
                    {
                        "session_ref": session_ref,
                        "text": part.get("text") or part.get("summary") or "",
                    },
                )
            ]
        if kind == "reasoning":
            return [
                NormalizedTraceEvent(
                    "diagnostic",
                    {
                        "session_ref": session_ref,
                        "block_type": "reasoning",
                        "redacted_private_reasoning": True,
                    },
                )
            ]
        if kind == "step_finish":
            reason = part.get("reason")
            return [
                NormalizedTraceEvent(
                    "usage",
                    {
                        "state": (
                            "completed"
                            if reason in _TERMINAL_FINISH_REASONS
                            else "intermediate"
                        ),
                        "session_ref": session_ref,
                        "reason": reason,
                        "usage": part.get("tokens", {}),
                        "cost": part.get("cost"),
                    },
                )
            ]
        if kind == "error":
            return [
                NormalizedTraceEvent(
                    "error",
                    {
                        "session_ref": session_ref,
                        "error": value.get("error"),
                    },
                )
            ]
        return super().normalize_stream_record(record)
