from __future__ import annotations

import json
import subprocess
from pathlib import Path

from agent_team.state import fixed_state_dir

from .base import (
    AdapterEvidence,
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
    def _permission_mapping() -> list[str]:
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
            "sandbox_workspace_write.network_access=false",
            "-c",
            "sandbox_workspace_write.exclude_tmpdir_env_var=false",
            "-c",
            "sandbox_workspace_write.exclude_slash_tmp=false",
        ]

    def profile_mappings(self) -> dict[str, dict[str, list[str]]]:
        permission_mapping = [
            "--ignore-user-config",
            "--ignore-rules",
            *self._permission_mapping(),
        ]
        return {
            "default": {
                "start": list(permission_mapping),
                "resume": list(permission_mapping),
            }
        }

    def prepare_launch(self, context: TurnLaunchContext) -> LaunchSpec:
        self.assert_profile(
            context.launch_profile,
            context.session_policy,
            context.launch_profile_sha256,
        )
        executable = str(self.executable())
        mapping = self.profile_mappings()[context.launch_profile]
        output = str(Path(context.turn_dir) / "output.md")
        if context.session_ref and context.session_policy == "resume":
            argv = (
                executable,
                "exec",
                "resume",
                "--json",
                *mapping["resume"],
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
            env={
                "AGENT_TEAM_RUN_ID": context.run_id,
                "AGENT_TEAM_ROLE_ID": context.role_id,
                "AGENT_TEAM_TURN_ID": context.turn_id,
                "AGENT_TEAM_RUN_DIR": str(Path(context.turn_dir).parent.parent),
                "AGENT_TEAM_TURN_DIR": context.turn_dir,
                "AGENT_TEAM_CLI": context.agent_team_cli,
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
