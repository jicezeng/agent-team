from __future__ import annotations

import json
import os
import subprocess
import tempfile
import uuid
from pathlib import Path

from agent_team.assets import effective_agent_team_cli, effective_claude_plugin
from agent_team.errors import AgentTeamError

from .base import (
    AdapterEvidence,
    HarnessAdapter,
    LaunchSpec,
    NormalizedTraceEvent,
    StreamRecord,
    TurnLaunchContext,
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
    def _permission_mapping() -> list[str]:
        cli = str(effective_agent_team_cli())
        formal_commands = [
            f"{cli} handoff *",
            f"{cli} complete *",
            f"{cli} block *",
        ]
        sandbox_settings = json.dumps(
            {
                "sandbox": {
                    "enabled": True,
                    "failIfUnavailable": True,
                    "autoAllowBashIfSandboxed": True,
                    "allowUnsandboxedCommands": False,
                    "excludedCommands": formal_commands,
                    "filesystem": {
                        "allowWrite": [str(claude_internal_tmpdir())],
                    },
                }
            },
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
            "--permission-mode",
            "acceptEdits",
            "--settings",
            sandbox_settings,
            "--allowedTools",
            *(f"Bash({command})" for command in formal_commands),
            "--disallowedTools",
            *forbidden_commands,
        ]

    def profile_mappings(self) -> dict[str, dict[str, list[str]]]:
        fixed = [
            *self._permission_mapping(),
            "--setting-sources",
            "",
            "--strict-mcp-config",
            "--tools",
            "default",
            "--plugin-dir",
            str(effective_claude_plugin()),
        ]
        return {
            "default": {
                "start": fixed.copy(),
                "resume": fixed.copy(),
            }
        }

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

    def prepare_launch(self, context: TurnLaunchContext) -> LaunchSpec:
        self.assert_profile(
            context.launch_profile,
            context.session_policy,
            context.launch_profile_sha256,
        )
        executable = str(self.executable())
        mapping = self.profile_mappings()[context.launch_profile]
        base = (
            executable,
            "-p",
            "--input-format",
            "text",
            "--output-format",
            "stream-json",
            "--verbose",
        )
        if context.session_ref and context.session_policy == "resume":
            argv = (*base, *mapping["resume"], "--resume", context.session_ref)
            starts_new = False
        else:
            session_id = str(uuid.uuid4())
            argv = (*base, *mapping["start"], "--session-id", session_id)
            starts_new = True
        return LaunchSpec(
            adapter_id=self.adapter_id,
            argv=argv,
            cwd=context.workspace,
            env={
                "AGENT_TEAM_RUN_ID": context.run_id,
                "AGENT_TEAM_ROLE_ID": context.role_id,
                "AGENT_TEAM_TURN_ID": context.turn_id,
                "AGENT_TEAM_RUN_DIR": str(Path(context.turn_dir).parent.parent),
                "AGENT_TEAM_TURN_DIR": context.turn_dir,
                "AGENT_TEAM_CLI": context.agent_team_cli,
                "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1",
            },
            stdin=context.prompt,
            launch_profile=context.launch_profile,
            launch_profile_sha256=context.launch_profile_sha256,
            starts_new_session=starts_new,
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
