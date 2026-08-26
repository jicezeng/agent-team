from __future__ import annotations

import abc
import json
import shutil
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from agent_team import __version__
from agent_team.errors import AgentTeamError, IntegrityError
from agent_team.util import canonical_json_bytes, sha256_bytes

LAUNCH_MODES = frozenset({"headless", "interactive"})


def workspace_from_run_dir(run_dir: Path) -> Path:
    """Resolve the canonical workspace that owns an Agent-Team Run."""

    try:
        resolved_run = run_dir.resolve(strict=True)
        workspace = resolved_run.parent.parent.parent.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise IntegrityError("run path cannot be resolved safely") from exc
    expected = workspace / ".agent-team" / "runs" / resolved_run.name
    if run_dir.is_symlink() or not resolved_run.is_dir() or resolved_run != expected:
        raise IntegrityError("run directory is outside the canonical workspace path")
    return workspace


@dataclass(frozen=True, slots=True)
class CapabilityReport:
    adapter_id: str
    adapter_version: str
    executable: str
    executable_version: str
    authenticated: bool | None
    profiles: tuple[str, ...]
    launcher_stays_in_process_group: bool
    details: dict[str, Any]

    def to_json(self) -> dict[str, Any]:
        value = asdict(self)
        value["profiles"] = list(self.profiles)
        return value


@dataclass(frozen=True, slots=True)
class HarnessLaunchOptions:
    model: str | None = None
    reasoning_effort: str | None = None
    fast_mode: bool | None = None
    model_provider: str | None = None
    model_provider_config: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class TurnLaunchContext:
    run_id: str
    role_id: str
    turn_id: str
    workspace: str
    turn_dir: str
    prompt: str
    session_policy: str
    session_ref: str | None
    session_generation: int
    launch_profile: str
    launch_profile_sha256: str
    agent_team_cli: str
    model: str | None = None
    reasoning_effort: str | None = None
    fast_mode: bool | None = None
    launch_mode: str = "headless"
    model_provider: str | None = None
    model_provider_config: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class LaunchSpec:
    adapter_id: str
    argv: tuple[str, ...]
    cwd: str
    env: dict[str, str]
    stdin: str
    launch_profile: str
    launch_profile_sha256: str
    starts_new_session: bool
    launch_mode: str = "headless"
    prompt_file: str | None = None
    expected_session_ref: str | None = None
    schema_version: int = 2

    def to_json(self) -> dict[str, Any]:
        if self.schema_version == 1:
            return {
                "adapter_id": self.adapter_id,
                "argv": list(self.argv),
                "cwd": self.cwd,
                "env": self.env,
                "stdin": self.stdin,
                "launch_profile": self.launch_profile,
                "launch_profile_sha256": self.launch_profile_sha256,
                "starts_new_session": self.starts_new_session,
            }
        value = asdict(self)
        value["argv"] = list(self.argv)
        return value

    def content_sha256(self) -> str:
        return sha256_bytes(canonical_json_bytes(self.to_json()))

    @classmethod
    def from_json(cls, value: dict[str, Any]) -> LaunchSpec:
        legacy_required = {
            "adapter_id",
            "argv",
            "cwd",
            "env",
            "stdin",
            "launch_profile",
            "launch_profile_sha256",
            "starts_new_session",
        }
        current_required = legacy_required | {
            "schema_version",
            "launch_mode",
            "prompt_file",
            "expected_session_ref",
        }
        fields = set(value)
        if fields == legacy_required:
            schema_version = 1
            launch_mode = "headless"
            prompt_file = None
            expected_session_ref = None
        elif (
            fields == current_required
            and isinstance(value.get("schema_version"), int)
            and not isinstance(value.get("schema_version"), bool)
            and value.get("schema_version") == 2
        ):
            schema_version = 2
            launch_mode = value["launch_mode"]
            prompt_file = value["prompt_file"]
            expected_session_ref = value["expected_session_ref"]
        else:
            raise IntegrityError("launch spec has invalid fields")
        if (
            not isinstance(value["adapter_id"], str)
            or not value["adapter_id"]
            or not isinstance(value["argv"], list)
            or not value["argv"]
            or not isinstance(value["argv"][0], str)
            or not value["argv"][0]
            or not all(isinstance(item, str) for item in value["argv"])
            or not isinstance(value["cwd"], str)
            or not value["cwd"]
            or not isinstance(value["stdin"], str)
            or not isinstance(value["launch_profile"], str)
            or not value["launch_profile"]
            or not isinstance(value["launch_profile_sha256"], str)
            or len(value["launch_profile_sha256"]) != 64
            or any(
                char not in "0123456789abcdef"
                for char in value["launch_profile_sha256"]
            )
            or not isinstance(value["starts_new_session"], bool)
        ):
            raise IntegrityError("launch spec fields are invalid")
        if not isinstance(value["env"], dict) or not all(
            isinstance(key, str) and isinstance(item, str)
            for key, item in value["env"].items()
        ):
            raise IntegrityError("launch spec environment is invalid")
        if launch_mode not in LAUNCH_MODES:
            raise IntegrityError("launch spec mode is invalid")
        if expected_session_ref is not None and (
            not isinstance(expected_session_ref, str) or not expected_session_ref
        ):
            raise IntegrityError("launch spec expected session ref is invalid")
        if launch_mode == "interactive":
            if (
                not isinstance(prompt_file, str)
                or not prompt_file
                or not Path(prompt_file).is_absolute()
                or not value["stdin"]
                or (
                    not value["starts_new_session"]
                    and expected_session_ref is None
                )
            ):
                raise IntegrityError("interactive launch prompt is invalid")
        elif prompt_file is not None or expected_session_ref is not None:
            raise IntegrityError(
                "headless launch cannot contain interactive session fields"
            )
        return cls(
            adapter_id=value["adapter_id"],
            argv=tuple(value["argv"]),
            cwd=value["cwd"],
            env=value["env"],
            stdin=value["stdin"],
            launch_profile=value["launch_profile"],
            launch_profile_sha256=value["launch_profile_sha256"],
            starts_new_session=value["starts_new_session"],
            launch_mode=launch_mode,
            prompt_file=prompt_file,
            expected_session_ref=expected_session_ref,
            schema_version=schema_version,
        )


@dataclass(frozen=True, slots=True)
class RawStreamChunk:
    schema_version: int
    seq: int
    observed_at: str
    source: str
    encoding: str
    data: str


@dataclass(frozen=True, slots=True)
class StreamRecord:
    source: str
    first_seq: int
    last_seq: int
    observed_at: str
    encoding: str
    data: str


@dataclass(frozen=True, slots=True)
class NormalizedTraceEvent:
    event_type: str
    data: dict[str, Any]


@dataclass(frozen=True, slots=True)
class AdapterEvidence:
    agent_execution_started: bool = False
    adapter_completed: bool = False
    permission_required: bool = False
    observed_session_ref: str | None = None
    session_unavailable_reason: str | None = None


@dataclass(slots=True)
class AdapterEvidenceSnapshot:
    agent_execution_started: bool = False
    adapter_completed: bool = False
    permission_required: bool = False
    observed_session_ref: str | None = None
    session_unavailable_reason: str | None = None

    def merge(self, evidence: AdapterEvidence) -> bool:
        before = self._state()
        if evidence.session_unavailable_reason is not None:
            if (
                not evidence.session_unavailable_reason
                or evidence.agent_execution_started
                or evidence.adapter_completed
                or evidence.permission_required
                or evidence.observed_session_ref is not None
            ):
                raise IntegrityError(
                    "adapter emitted invalid session-unavailable evidence"
                )
            if self.agent_execution_started or self.adapter_completed:
                raise IntegrityError(
                    "adapter reported an unavailable session after execution started"
                )
            if self.permission_required:
                raise IntegrityError(
                    "adapter reported both permission and session-unavailable evidence"
                )
            if (
                self.session_unavailable_reason is not None
                and self.session_unavailable_reason
                != evidence.session_unavailable_reason
            ):
                raise IntegrityError(
                    "adapter emitted conflicting session-unavailable reasons"
                )
            self.session_unavailable_reason = evidence.session_unavailable_reason
        elif self.session_unavailable_reason is not None:
            if (
                evidence.agent_execution_started
                or evidence.adapter_completed
                or evidence.permission_required
            ):
                raise IntegrityError(
                    "adapter evidence conflicts with an unavailable session"
                )
            # Claude may emit a new init record after rejecting the requested
            # resume. It is not a recoverable replacement Session.
            return before != self._state()
        self.agent_execution_started = (
            self.agent_execution_started or evidence.agent_execution_started
        )
        self.adapter_completed = self.adapter_completed or evidence.adapter_completed
        self.permission_required = (
            self.permission_required or evidence.permission_required
        )
        if evidence.observed_session_ref:
            if (
                self.observed_session_ref is not None
                and self.observed_session_ref != evidence.observed_session_ref
            ):
                raise IntegrityError("adapter emitted conflicting session refs")
            self.observed_session_ref = evidence.observed_session_ref
        if self.adapter_completed and not self.agent_execution_started:
            raise IntegrityError("adapter completion without execution start")
        if self.adapter_completed and self.permission_required:
            raise IntegrityError("adapter cannot be complete and permission-blocked")
        return before != self._state()

    def _state(self) -> tuple[bool, bool, bool, str | None, str | None]:
        return (
            self.agent_execution_started,
            self.adapter_completed,
            self.permission_required,
            self.observed_session_ref,
            self.session_unavailable_reason,
        )

    def to_json(self) -> dict[str, Any]:
        """Return only the design's fixed durable Supervisor evidence fields."""
        return {
            "agent_execution_started": self.agent_execution_started,
            "adapter_completed": self.adapter_completed,
            "permission_required": self.permission_required,
            "observed_session_ref": self.observed_session_ref,
        }


@dataclass(frozen=True, slots=True)
class ProcessResult:
    process_exit_code: int | None
    termination_kind: str
    group_quiescent: bool
    launch_mode: str = "headless"


@dataclass(frozen=True, slots=True)
class ExitInfo:
    is_normal_completion: bool
    reason: str


class HarnessAdapter(abc.ABC):
    adapter_id: str
    executable_name: str
    adapter_version: str = __version__

    @abc.abstractmethod
    def profile_mappings(
        self,
        launch_mode: str = "headless",
    ) -> dict[str, dict[str, list[str]]]:
        raise NotImplementedError

    @abc.abstractmethod
    def prepare_launch(self, context: TurnLaunchContext) -> LaunchSpec:
        raise NotImplementedError

    @abc.abstractmethod
    def resolve_launch_options(
        self,
        *,
        model: str | None,
        reasoning_effort: str | None,
        fast_mode: bool | None,
        model_provider: str | None = None,
        workspace: Path | None = None,
    ) -> HarnessLaunchOptions:
        """Resolve explicit values over isolated user defaults for a new Run.

        ``workspace`` lets Harnesses with project-scoped configuration resolve
        the default that applies to the exact Run worktree. Adapters whose
        defaults are account-scoped may ignore it.
        """
        raise NotImplementedError

    @abc.abstractmethod
    def assert_launch_options(self, options: HarnessLaunchOptions) -> None:
        """Validate immutable launch options without reading mutable defaults."""
        raise NotImplementedError

    def ensure_launch_dependencies(self, options: HarnessLaunchOptions) -> None:
        """Provision optional, Agent-Team-owned dependencies for a selected role."""

        self.assert_launch_options(options)

    def assert_launch_prerequisites(self, options: HarnessLaunchOptions) -> None:
        """Validate role-specific authentication and launch prerequisites."""

        self.assert_launch_options(options)
        if (
            self.authentication_required(options)
            and self.authentication_status() is False
        ):
            raise AgentTeamError(
                "HARNESS_NOT_AUTHENTICATED",
                f"{self.adapter_id} is not authenticated",
            )

    def authentication_required(self, options: HarnessLaunchOptions) -> bool:
        """Return whether the selected role route uses Harness account auth."""

        self.assert_launch_options(options)
        return True

    @abc.abstractmethod
    def parse_stream_record(self, record: StreamRecord) -> AdapterEvidence | None:
        raise NotImplementedError

    def normalize_stream_record(
        self,
        record: StreamRecord,
    ) -> list[NormalizedTraceEvent]:
        # A native TUI is not a structured Harness protocol. Even when one PTY
        # chunk happens to contain valid JSON, retain it only as diagnostic
        # terminal output so it can never be confused with headless evidence.
        if record.source == "terminal":
            return [
                NormalizedTraceEvent(
                    "diagnostic",
                    {
                        "source": record.source,
                        "encoding": record.encoding,
                        "text": record.data,
                    },
                )
            ]
        value = self.parse_json_record(record)
        if value is not None:
            return [
                NormalizedTraceEvent(
                    "harness_event",
                    {
                        "source": record.source,
                        "payload": value,
                    },
                )
            ]
        return [
            NormalizedTraceEvent(
                "diagnostic",
                {
                    "source": record.source,
                    "encoding": record.encoding,
                    "text": record.data,
                },
            )
        ]

    def prepare_run_state(
        self,
        *,
        run_dir: Path,
        role_id: str,
        launch_mode: str,
        session_generation: int = 1,
    ) -> None:
        """Prepare private state for the role's next Session generation."""

        if (
            isinstance(session_generation, bool)
            or not isinstance(session_generation, int)
            or session_generation < 1
        ):
            raise IntegrityError("session generation must be a positive integer")
        self.assert_launch_mode(launch_mode)

    def worker_environment_names(
        self,
        *,
        run_dir: Path,
        role_id: str,
        options: HarnessLaunchOptions | None = None,
    ) -> tuple[str, ...]:
        """Return environment names that must cross the tmux Worker boundary.

        Values are deliberately resolved by the tmux launcher at Worker
        creation time.  Adapters must return names only so credentials never
        become part of durable Run state.
        """

        del options
        return ()

    def has_prepared_run_state(
        self,
        *,
        run_dir: Path,
        role_id: str,
        launch_mode: str,
    ) -> bool:
        """Return whether Adapter-private state exists for an unvisited role.

        Terminal cleanup always finalizes roles that have a durable Runtime.
        This probe covers the narrower case where route preflight prepared
        private state but no business Turn was committed for that role.
        """

        del run_dir, role_id
        self.assert_launch_mode(launch_mode)
        return False

    def finalize_run_state(
        self,
        *,
        run_dir: Path,
        role_id: str,
        launch_mode: str,
    ) -> None:
        """Finalize private adapter state after its process group is quiescent."""

        self.assert_launch_mode(launch_mode)

    def interactive_session_refs(self, launch: LaunchSpec) -> set[str]:
        """Return session refs durably visible for an interactive launch."""

        return set()

    def classify_result(
        self,
        result: ProcessResult,
        evidence: AdapterEvidenceSnapshot,
    ) -> ExitInfo:
        self.assert_launch_mode(result.launch_mode)
        if result.launch_mode == "interactive":
            expected_termination = (
                result.termination_kind == "action"
                or (
                    result.termination_kind == "normal"
                    and result.process_exit_code == 0
                )
            )
        else:
            expected_termination = (
                result.termination_kind == "normal"
                and result.process_exit_code == 0
            )
        normal = (
            result.group_quiescent
            and expected_termination
            and evidence.agent_execution_started
            and evidence.adapter_completed
            and not evidence.permission_required
            and evidence.observed_session_ref is not None
            and evidence.session_unavailable_reason is None
        )
        return ExitInfo(normal, "normal_completion" if normal else "abnormal_exit")

    def executable(self) -> Path:
        located = shutil.which(self.executable_name)
        if not located:
            raise AgentTeamError(
                "HARNESS_NOT_FOUND",
                f"{self.executable_name} is not installed",
            )
        return Path(located).resolve(strict=True)

    def executable_version(self) -> str:
        result = subprocess.run(
            [str(self.executable()), "--version"],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
        if result.returncode != 0:
            raise AgentTeamError(
                "HARNESS_PROBE_FAILED",
                f"{self.executable_name} --version failed: {result.stderr.strip()}",
            )
        return (result.stdout or result.stderr).strip()

    def authentication_status(self) -> bool | None:
        return None

    def probe(self) -> CapabilityReport:
        mappings = self.profile_mappings("headless")
        interactive_mappings = self.profile_mappings("interactive")
        return CapabilityReport(
            adapter_id=self.adapter_id,
            adapter_version=self.adapter_version,
            executable=str(self.executable()),
            executable_version=self.executable_version(),
            authenticated=self.authentication_status(),
            profiles=tuple(sorted(mappings)),
            launcher_stays_in_process_group=True,
            details={
                "profile_mappings": mappings,
                "launch_modes": {
                    "headless": mappings,
                    "interactive": interactive_mappings,
                },
            },
        )

    @staticmethod
    def assert_launch_mode(launch_mode: str) -> None:
        if launch_mode not in LAUNCH_MODES:
            raise AgentTeamError(
                "UNKNOWN_LAUNCH_MODE",
                f"launch mode {launch_mode!r} is not supported",
            )

    def profile_fingerprint(
        self,
        profile: str,
        session_policy: str,
        launch_mode: str = "headless",
    ) -> str:
        self.assert_launch_mode(launch_mode)
        report = self.probe()
        launch_modes = report.details.get("launch_modes")
        mappings = (
            launch_modes.get(launch_mode)
            if isinstance(launch_modes, dict)
            else None
        )
        if not isinstance(mappings, dict):
            raise AgentTeamError(
                "INVALID_CAPABILITY_REPORT",
                f"{self.adapter_id} probe did not return {launch_mode} mappings",
            )
        if profile not in mappings:
            raise AgentTeamError(
                "UNKNOWN_LAUNCH_PROFILE",
                f"{self.adapter_id} profile {profile!r} is not supported",
            )
        mapping = mappings[profile]
        if not isinstance(mapping, dict):
            raise AgentTeamError(
                "INVALID_LAUNCH_PROFILE",
                f"{self.adapter_id} profile {profile!r} mapping is invalid",
            )

        def required_path(path: str) -> list[str]:
            value = mapping.get(path)
            if (
                not isinstance(value, list)
                or not value
                or not isinstance(value[0], str)
                or not value[0]
                or not all(isinstance(item, str) for item in value)
            ):
                raise AgentTeamError(
                    f"{path.upper()}_PROFILE_UNSUPPORTED",
                    f"{self.adapter_id} profile {profile!r} has no valid "
                    f"{path} mapping",
                )
            return value

        start = required_path("start")
        selected = {"start": start}
        if session_policy == "resume":
            resume = required_path("resume")
            if resume != start:
                raise AgentTeamError(
                    "RESUME_PERMISSION_MISMATCH",
                    f"{self.adapter_id} profile {profile!r} does not preserve "
                    "equivalent technical permissions across Start and Resume",
                )
            selected["resume"] = resume
        elif session_policy != "fresh":
            raise AgentTeamError(
                "INVALID_SESSION_POLICY",
                f"invalid session policy: {session_policy}",
            )
        components = [
            report.adapter_id.encode("utf-8"),
            report.adapter_version.encode("utf-8"),
            report.executable.encode("utf-8"),
            report.executable_version.encode("utf-8"),
            profile.encode("utf-8"),
            session_policy.encode("utf-8"),
            canonical_json_bytes(selected),
        ]
        # Preserve the historical headless fingerprint framing. Mapping or
        # executable changes still change the digest and intentionally block
        # further execution; completed Runs remain observable independently.
        # Interactive mode is a distinct contract and gets an explicit frame.
        if launch_mode == "interactive":
            components.append(launch_mode.encode("utf-8"))
        framed = b"".join(
            len(component).to_bytes(8, "big") + component
            for component in components
        )
        return sha256_bytes(framed)

    def assert_profile(
        self,
        profile: str,
        session_policy: str,
        expected_sha256: str,
        launch_mode: str = "headless",
    ) -> None:
        current = self.profile_fingerprint(profile, session_policy, launch_mode)
        if current != expected_sha256:
            raise AgentTeamError(
                "PROFILE_CHANGED_NEW_RUN_REQUIRED",
                f"{self.adapter_id} launch profile changed "
                f"(frozen={expected_sha256}, current={current})",
            )

    @staticmethod
    def parse_json_record(record: StreamRecord) -> dict[str, Any] | None:
        if record.encoding != "utf-8":
            return None
        try:
            value = json.loads(record.data)
        except json.JSONDecodeError:
            return None
        return value if isinstance(value, dict) else None
