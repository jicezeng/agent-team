from __future__ import annotations

import json
import os
import secrets
import shutil
import stat
import uuid
from pathlib import Path
from typing import Any

from agent_team.assets import dsh_tui_source
from agent_team.config import DSH_REASONING_EFFORTS, valid_dsh_model_id
from agent_team.dsh_runtime import (
    DSH_NPM_INTEGRITY,
    DSH_NPM_VERSION,
    managed_dsh_executable,
    managed_dsh_runtime,
    managed_dsh_runtime_report,
    managed_dsh_version,
)
from agent_team.errors import AgentTeamError, IntegrityError, InvalidArgument
from agent_team.state import fixed_state_dir
from agent_team.util import (
    atomic_json,
    atomic_write,
    canonical_json_bytes,
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
    CapabilityReport,
    HarnessAdapter,
    HarnessLaunchOptions,
    LaunchSpec,
    StreamRecord,
    TurnLaunchContext,
)

_DEFAULT_MODEL = "deepseek-official/deepseek-v4-flash"
_DEFAULT_REASONING_EFFORT = "high"
_SESSION_NAMESPACE = uuid.UUID("a02f363b-039e-4a17-af70-639179544261")
_PROFILE_NAME = "agent-team"
_PLUGIN_PACKAGE = "@agent-team/dsh-tui"


def _tree_manifest(root: Path) -> dict[str, str]:
    manifest: dict[str, str] = {}
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
        if path.is_symlink():
            raise IntegrityError(f"DeepSeek Harness TUI asset contains a symlink: {path}")
        if path.is_file():
            relative = path.relative_to(root).as_posix()
            manifest[relative] = sha256_bytes(read_regular(path))
        elif not path.is_dir():
            raise IntegrityError(
                f"DeepSeek Harness TUI asset contains an unsafe entry: {path}"
            )
    return manifest


class DeepSeekHarnessAdapter(HarnessAdapter):
    """Interactive DeepSeek Harness adapter with Run-private DSH state."""

    adapter_id = "deepseek-harness"
    executable_name = "dsh"

    @staticmethod
    def assert_launch_mode(launch_mode: str) -> None:
        if launch_mode != "interactive":
            raise AgentTeamError(
                "LAUNCH_MODE_UNSUPPORTED",
                "deepseek-harness is controlled through its interactive TUI; "
                "headless mode is not supported by this adapter",
            )

    def executable(self) -> Path:
        return managed_dsh_executable()

    def executable_version(self) -> str:
        return managed_dsh_version()

    def authentication_status(self) -> bool | None:
        return bool(os.environ.get("DEEPSEEK_API_KEY", "").strip())

    def profile_mappings(
        self,
        launch_mode: str = "interactive",
    ) -> dict[str, dict[str, list[str]]]:
        self.assert_launch_mode(launch_mode)
        profiles = {
            "default": [
                "sandbox=workspace-write",
                "approval=never",
                "network=inherited",
            ],
            "trusted-workspace": [
                "sandbox=workspace-write",
                "approval=never",
                "network=inherited",
            ],
            "full-access": [
                "sandbox=danger-full-access",
                "approval=never",
                "network=inherited",
            ],
        }
        return {
            profile: {"start": mapping.copy(), "resume": mapping.copy()}
            for profile, mapping in profiles.items()
        }

    @staticmethod
    def _plugin_contract() -> dict[str, Any]:
        source = dsh_tui_source()
        return {
            "package": _PLUGIN_PACKAGE,
            "manifest": _tree_manifest(source),
            "runtime_version": DSH_NPM_VERSION,
            "runtime_integrity": DSH_NPM_INTEGRITY,
            "profile": _PROFILE_NAME,
            "state": "private per Run and role",
            "session_encoding": "jsonl-none",
        }

    def probe(self) -> CapabilityReport:
        mappings = self.profile_mappings("interactive")
        runtime = managed_dsh_runtime_report()
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
                "launch_modes": {"interactive": mappings},
                "runtime": runtime,
                "tui": self._plugin_contract(),
                "runtime_isolation": {
                    "dsh_home": "private per Run and role",
                    "credentials": "environment only",
                    "user_profiles": "not loaded",
                    "session_resume": "native agents.resume over private JSONL",
                },
            },
        )

    def profile_fingerprint(
        self,
        profile: str,
        session_policy: str,
        launch_mode: str = "interactive",
    ) -> str:
        base = super().profile_fingerprint(profile, session_policy, launch_mode)
        components = [base.encode(), canonical_json_bytes(self._plugin_contract())]
        framed = b"".join(
            len(component).to_bytes(8, "big") + component
            for component in components
        )
        return sha256_bytes(framed)

    def resolve_launch_options(
        self,
        *,
        model: str | None,
        reasoning_effort: str | None,
        fast_mode: bool | None,
        workspace: Path | None = None,
    ) -> HarnessLaunchOptions:
        del workspace
        options = HarnessLaunchOptions(
            model=model or _DEFAULT_MODEL,
            reasoning_effort=reasoning_effort or _DEFAULT_REASONING_EFFORT,
            fast_mode=fast_mode,
        )
        self.assert_launch_options(options)
        return options

    def assert_launch_options(self, options: HarnessLaunchOptions) -> None:
        if not valid_dsh_model_id(options.model):
            raise InvalidArgument(
                "deepseek-harness requires a model in provider/model form"
            )
        if options.reasoning_effort not in DSH_REASONING_EFFORTS:
            raise InvalidArgument(
                "deepseek-harness reasoning effort must be one of: off, high, max"
            )
        if options.fast_mode is not None:
            raise InvalidArgument("fast mode is only supported by the codex adapter")

    @staticmethod
    def _home(run_dir: Path, role_id: str) -> Path:
        digest = sha256_bytes(os.fsencode(str(run_dir.resolve(strict=True))))
        return (
            fixed_state_dir()
            / "harness-homes"
            / "deepseek-harness"
            / digest
            / role_id
        )

    @classmethod
    def _home_hierarchy(cls, run_dir: Path, role_id: str) -> tuple[Path, ...]:
        home = cls._home(run_dir, role_id)
        state = fixed_state_dir()
        return (
            state,
            state / "harness-homes",
            state / "harness-homes" / "deepseek-harness",
            home.parent,
            home,
        )

    @classmethod
    def _marker(cls, run_dir: Path, role_id: str) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "adapter": "deepseek-harness",
            "run_dir": str(run_dir.resolve(strict=True)),
            "role_id": role_id,
        }

    @staticmethod
    def _profile_manifest() -> dict[str, Any]:
        return {
            "name": "dsh-profile-agent-team",
            "private": True,
            "dsh": {
                "profile": {
                    "bundles": ["@deepseek-ai/dsh-base", _PLUGIN_PACKAGE]
                }
            },
        }

    @staticmethod
    def _copy_plugin(source: Path, target: Path) -> None:
        expected = _tree_manifest(source)
        if path_entry_exists(target):
            if target.is_symlink() or not target.is_dir():
                raise IntegrityError(f"DeepSeek Harness TUI target is unsafe: {target}")
            if _tree_manifest(target) != expected:
                raise IntegrityError(
                    "DeepSeek Harness TUI state differs from the frozen bundled plugin"
                )
            return
        ensure_dir(target.parent)
        temporary = target.parent / (
            f".tmp-{target.name}-{os.getpid()}-{secrets.token_hex(8)}"
        )
        try:
            shutil.copytree(source, temporary)
            if _tree_manifest(temporary) != expected:
                raise IntegrityError("copied DeepSeek Harness TUI asset changed")
            for path in temporary.rglob("*"):
                path.chmod(0o700 if path.is_dir() else 0o600)
            temporary.chmod(0o700)
            os.rename(temporary, target)
            fsync_dir(target.parent)
        finally:
            if path_entry_exists(temporary):
                shutil.rmtree(temporary)

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
        # Validate the exact managed runtime before creating any role state.
        managed_dsh_runtime_report()
        for directory in self._home_hierarchy(run_dir, role_id):
            ensure_dir(directory)
            info = directory.lstat()
            if directory.is_symlink() or not stat.S_ISDIR(info.st_mode):
                raise IntegrityError(
                    f"DeepSeek Harness state directory is unsafe: {directory}"
                )
            directory.chmod(0o700)
        home = self._home(run_dir, role_id)
        marker_path = home / "agent-team-home.json"
        marker = self._marker(run_dir, role_id)
        if path_entry_exists(marker_path):
            if read_json(marker_path) != marker:
                raise IntegrityError(
                    f"DeepSeek Harness home belongs to another frozen Run: {home}"
                )
        else:
            atomic_json(marker_path, marker, immutable=True)

        profile = home / "profiles" / _PROFILE_NAME
        ensure_dir(profile)
        profile.chmod(0o700)
        atomic_write(
            profile / "package.json",
            canonical_json_bytes(self._profile_manifest()),
            immutable=True,
        )
        atomic_write(profile / "cordis.patch.yml", b"[]\n", immutable=True)
        atomic_write(
            profile / "pnpm-workspace.yaml",
            b"packages:\n  - .\n\nnodeLinker: hoisted\nautoInstallPeers: false\n",
            immutable=True,
        )
        self._copy_plugin(
            dsh_tui_source(),
            profile / "node_modules" / "@agent-team" / "dsh-tui",
        )
        ensure_dir(home / "sessions")
        (home / "sessions").chmod(0o700)

    def _assert_home(self, context: TurnLaunchContext) -> Path:
        run_dir = Path(context.turn_dir).parent.parent
        home = self._home(run_dir, context.role_id)
        try:
            info = home.lstat()
        except OSError as exc:
            raise AgentTeamError(
                "HARNESS_STATE_NOT_PREPARED",
                f"DeepSeek Harness state is unavailable for {context.role_id}",
            ) from exc
        if home.is_symlink() or not stat.S_ISDIR(info.st_mode):
            raise IntegrityError("DeepSeek Harness state is unsafe")
        if read_json(home / "agent-team-home.json") != self._marker(
            run_dir, context.role_id
        ):
            raise AgentTeamError(
                "HARNESS_STATE_NOT_PREPARED",
                f"DeepSeek Harness state is not prepared for {context.role_id}",
            )
        return home

    @staticmethod
    def _session_id(context: TurnLaunchContext) -> str:
        identity = "\0".join(
            (
                context.run_id,
                context.role_id,
                str(context.session_generation),
            )
        )
        return f"agent-team-{uuid.uuid5(_SESSION_NAMESPACE, identity)}"

    @staticmethod
    def _session_refs(root: Path, workspace: Path) -> set[str]:
        if not path_entry_exists(root):
            return set()
        info = root.lstat()
        if root.is_symlink() or not stat.S_ISDIR(info.st_mode):
            raise IntegrityError("DeepSeek Harness Session root is unsafe")
        workspace = workspace.resolve(strict=True)
        refs: set[str] = set()
        resolved_root = root.resolve(strict=True)
        for log in sorted(root.glob("*/*/session.jsonl")):
            try:
                log.resolve(strict=True).relative_to(resolved_root)
            except (OSError, RuntimeError, ValueError) as exc:
                raise IntegrityError(
                    f"DeepSeek Harness Session log escapes its root: {log}"
                ) from exc
            log_info = log.lstat()
            if log.is_symlink() or not stat.S_ISREG(log_info.st_mode):
                raise IntegrityError(f"DeepSeek Harness Session log is unsafe: {log}")
            fd = open_regular(log)
            try:
                prefix = os.read(fd, 65537)
            finally:
                os.close(fd)
            newline = prefix.find(b"\n")
            if newline < 0 or newline > 65536:
                raise IntegrityError(f"DeepSeek Harness Session header is invalid: {log}")
            try:
                header = json.loads(prefix[:newline])
            except json.JSONDecodeError as exc:
                raise IntegrityError(
                    f"DeepSeek Harness Session header is not JSON: {log}"
                ) from exc
            if (
                not isinstance(header, dict)
                or header.get("type") != "session"
                or not isinstance(header.get("id"), str)
                or not header["id"]
                or not isinstance(header.get("cwd"), str)
            ):
                raise IntegrityError(f"DeepSeek Harness Session header is invalid: {log}")
            try:
                cwd = Path(header["cwd"]).resolve(strict=True)
            except OSError:
                continue
            if cwd == workspace:
                if header["id"] in refs:
                    raise IntegrityError(
                        f"duplicate DeepSeek Harness Session id: {header['id']}"
                    )
                refs.add(header["id"])
        return refs

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
        home = self._assert_home(context)
        model_selector = options.model
        assert model_selector is not None
        provider, model = model_selector.split("/", 1)
        session_ref = (
            context.session_ref
            if context.session_policy == "resume" and context.session_ref
            else self._session_id(context)
        )
        starts_new = not (
            context.session_policy == "resume" and context.session_ref is not None
        )
        if not starts_new:
            refs = self._session_refs(home / "sessions", Path(context.workspace))
            if session_ref not in refs:
                raise AgentTeamError(
                    "HARNESS_SESSION_UNAVAILABLE",
                    f"DeepSeek Harness Session cannot be resumed: {session_ref}",
                )
        session_option = "--session-id" if starts_new else "--resume"
        argv = (
            str(self.executable()),
            "--profile",
            _PROFILE_NAME,
            session_option,
            session_ref,
            "--provider",
            provider,
            "--model",
            model,
            "--reasoning-effort",
            options.reasoning_effort or _DEFAULT_REASONING_EFFORT,
        )
        permission_mode = (
            "danger-full-access"
            if context.launch_profile == "full-access"
            else "workspace-write"
        )
        env = {
            "AGENT_TEAM_RUN_ID": context.run_id,
            "AGENT_TEAM_ROLE_ID": context.role_id,
            "AGENT_TEAM_TURN_ID": context.turn_id,
            "AGENT_TEAM_RUN_DIR": str(Path(context.turn_dir).parent.parent),
            "AGENT_TEAM_TURN_DIR": context.turn_dir,
            "AGENT_TEAM_CLI": context.agent_team_cli,
            "AGENT_TEAM_DSH_SESSION_ROOT": str(home / "sessions"),
            "DSH_HOME": str(home),
            "DSH_PERMISSION_MODE": permission_mode,
            "DSH_TELEMETRY_DISABLED": "1",
            "DSH_TOOLS_MODE": "native",
        }
        return LaunchSpec(
            adapter_id=self.adapter_id,
            argv=argv,
            cwd=context.workspace,
            env=env,
            stdin=context.prompt,
            launch_profile=context.launch_profile,
            launch_profile_sha256=context.launch_profile_sha256,
            starts_new_session=starts_new,
            launch_mode="interactive",
            prompt_file=str(Path(context.turn_dir) / "process" / "prompt.md"),
            expected_session_ref=session_ref,
        )

    def interactive_session_refs(self, launch: LaunchSpec) -> set[str]:
        if launch.launch_mode != "interactive":
            return set()
        root = launch.env.get("AGENT_TEAM_DSH_SESSION_ROOT")
        if root is None:
            raise IntegrityError("DeepSeek Harness launch has no Session root")
        return self._session_refs(Path(root), Path(launch.cwd))

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
        home = self._home(run_dir, role_id)
        try:
            info = home.lstat()
        except OSError as exc:
            raise IntegrityError(
                f"DeepSeek Harness state is unavailable: {home}"
            ) from exc
        if home.is_symlink() or not stat.S_ISDIR(info.st_mode):
            raise IntegrityError(f"DeepSeek Harness state is unsafe: {home}")
        if read_json(home / "agent-team-home.json") != self._marker(run_dir, role_id):
            raise IntegrityError(
                f"DeepSeek Harness state is not owned by this Run: {home}"
            )
        resolved_home = home.resolve(strict=True)
        resolved_runtime = managed_dsh_runtime().resolve(strict=True)
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
                        if not (
                            target.is_relative_to(resolved_home)
                            or target.is_relative_to(resolved_runtime)
                        ):
                            raise ValueError
                    except (OSError, RuntimeError, ValueError) as exc:
                        raise IntegrityError(
                            f"DeepSeek Harness state symlink escapes managed roots: {path}"
                        ) from exc
                    continue
                if stat.S_ISDIR(path_info.st_mode):
                    path.chmod(0o700)
                elif stat.S_ISREG(path_info.st_mode):
                    path.chmod(
                        0o700
                        if stat.S_IMODE(path_info.st_mode) & 0o111
                        else 0o600
                    )
                else:
                    raise IntegrityError(
                        f"DeepSeek Harness state entry is unsafe: {path}"
                    )
            current.chmod(0o700)
        for directory in self._home_hierarchy(run_dir, role_id):
            directory.chmod(0o700)

    def parse_stream_record(self, record: StreamRecord) -> AdapterEvidence | None:
        del record
        return None
