from __future__ import annotations

import subprocess
import uuid
from pathlib import Path

from agent_team.assets import effective_claude_plugin

from .base import (
    AdapterEvidence,
    HarnessAdapter,
    LaunchSpec,
    StreamRecord,
    TurnLaunchContext,
)


class ClaudeCodeAdapter(HarnessAdapter):
    adapter_id = "claude-code"
    executable_name = "claude"

    def profile_mappings(self) -> dict[str, dict[str, list[str]]]:
        fixed = [
            "--permission-mode",
            "acceptEdits",
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
