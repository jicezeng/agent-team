from __future__ import annotations

import json
import os
import re
import secrets
import shutil
import stat
import uuid
from pathlib import Path
from typing import Any

from agent_team.assets import dsh_tui_source
from agent_team.config import DSH_REASONING_EFFORTS, load_team, valid_dsh_model_id
from agent_team.dsh_runtime import (
    DSH_NPM_INTEGRITY,
    DSH_NPM_VERSION,
    install_managed_dsh_runtime,
    managed_dsh_executable,
    managed_dsh_runtime,
    managed_dsh_runtime_report,
    managed_dsh_version,
)
from agent_team.errors import (
    AgentTeamError,
    IntegrityError,
    InvalidArgument,
    RoutePreflightError,
)
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
    AdapterEvidenceSnapshot,
    CapabilityReport,
    HarnessAdapter,
    HarnessLaunchOptions,
    LaunchSpec,
    ProcessResult,
    StreamRecord,
    TurnLaunchContext,
)
from .capability_snapshot import (
    assert_capability_path,
    copy_capability_path,
    environment_reference_names,
)

_SESSION_NAMESPACE = uuid.UUID("a02f363b-039e-4a17-af70-639179544261")
_PROFILE_NAME = "agent-team"
_PLUGIN_PACKAGE = "@agent-team/dsh-tui"
_PLUGIN_STATE_FILE = "agent-team-dsh-plugin.json"
_CAPABILITY_STATE_FILE = "agent-team-user-capabilities.json"
_OUTPUT_LIMIT_EXIT_CODE = 75
_RESERVED_PLUGIN_PACKAGES = {"@deepseek-ai/dsh-base", _PLUGIN_PACKAGE}
_SOURCE_SURFACE_BUNDLES = {
    "@deepseek-ai/dsh-base",
    "@deepseek-ai/dsh-headless",
    "@deepseek-ai/dsh-web-app",
}
_PACKAGE_NAME_RE = re.compile(
    r"^(?:@[a-z0-9][a-z0-9._-]*/)?[a-z0-9][a-z0-9._-]*$"
)


def _tree_manifest(
    root: Path,
    *,
    ignored_names: frozenset[str] = frozenset(),
) -> dict[str, str]:
    manifest: dict[str, str] = {}
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
        relative_path = path.relative_to(root)
        if any(part in ignored_names for part in relative_path.parts):
            continue
        if path.is_symlink():
            raise IntegrityError(
                f"DeepSeek Harness plugin tree contains a symlink: {path}"
            )
        if path.is_file():
            relative = relative_path.as_posix()
            manifest[relative] = sha256_bytes(read_regular(path))
        elif not path.is_dir():
            raise IntegrityError(
                f"DeepSeek Harness plugin tree contains an unsafe entry: {path}"
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
                    "dsh_home": "private per Run, role, and Session generation",
                    "credentials": "environment only",
                    "user_profiles": (
                        "headless profile Plugin/MCP layers copied before kickoff"
                    ),
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
        components = [
            base.encode(),
            canonical_json_bytes(self._plugin_contract()),
            b"dsh-user-capabilities-v1:headless-profile-plugin-mcp-snapshot",
        ]
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
        model_provider: str | None = None,
        workspace: Path | None = None,
    ) -> HarnessLaunchOptions:
        del workspace
        options = HarnessLaunchOptions(
            model=model,
            reasoning_effort=reasoning_effort,
            fast_mode=fast_mode,
            model_provider=model_provider,
        )
        self.assert_launch_options(options)
        return options

    def assert_launch_options(self, options: HarnessLaunchOptions) -> None:
        if options.model is not None and not valid_dsh_model_id(options.model):
            raise InvalidArgument(
                "an explicit deepseek-harness model must use provider/model form"
            )
        if (
            options.reasoning_effort is not None
            and options.reasoning_effort not in DSH_REASONING_EFFORTS
        ):
            raise InvalidArgument(
                "deepseek-harness reasoning effort must be one of: off, high, max"
            )
        if options.fast_mode is not None:
            raise InvalidArgument("fast mode is only supported by the codex adapter")
        if (
            options.model_provider is not None
            or options.model_provider_config is not None
        ):
            raise InvalidArgument(
                "deepseek-harness encodes its Provider in --role-model "
                "ROLE=provider/model; --role-model-provider is not accepted"
            )

    def ensure_launch_dependencies(self, options: HarnessLaunchOptions) -> None:
        """Provision the managed runtime only when a DSH role is selected."""

        super().ensure_launch_dependencies(options)
        install_managed_dsh_runtime()

    @staticmethod
    def _run_home_root(run_dir: Path) -> Path:
        digest = sha256_bytes(os.fsencode(str(run_dir.resolve(strict=True))))
        return (
            fixed_state_dir()
            / "harness-homes"
            / "deepseek-harness"
            / digest
        )

    @classmethod
    def _home(
        cls,
        run_dir: Path,
        role_id: str,
        session_generation: int = 1,
    ) -> Path:
        if (
            isinstance(session_generation, bool)
            or not isinstance(session_generation, int)
            or session_generation < 1
        ):
            raise IntegrityError("DeepSeek Harness Session generation is invalid")
        root = cls._run_home_root(run_dir)
        # Keep generation 1 at its historical location so a blocked or active
        # Run created by an older Agent-Team release remains resumable after an
        # upgrade. Later generations are immutable sibling homes rather than
        # replacements for the first installed artifact.
        if session_generation == 1:
            return root / role_id
        return (
            root
            / "session-generations"
            / role_id
            / f"{session_generation:08d}"
        )

    @classmethod
    def _home_hierarchy(
        cls,
        run_dir: Path,
        role_id: str,
        session_generation: int = 1,
    ) -> tuple[Path, ...]:
        home = cls._home(run_dir, role_id, session_generation)
        state = fixed_state_dir()
        common = (
            state,
            state / "harness-homes",
            state / "harness-homes" / "deepseek-harness",
            cls._run_home_root(run_dir),
        )
        if session_generation == 1:
            return (*common, home)
        generations = cls._run_home_root(run_dir) / "session-generations"
        return (*common, generations, generations / role_id, home)

    @classmethod
    def _marker(
        cls,
        run_dir: Path,
        role_id: str,
        session_generation: int = 1,
    ) -> dict[str, Any]:
        marker: dict[str, Any] = {
            "schema_version": 1,
            "adapter": "deepseek-harness",
            "run_dir": str(run_dir.resolve(strict=True)),
            "role_id": role_id,
        }
        if session_generation > 1:
            marker.update(
                {
                    "schema_version": 2,
                    "session_generation": session_generation,
                }
            )
        return marker

    @classmethod
    def _prepared_homes(
        cls,
        run_dir: Path,
        role_id: str,
    ) -> tuple[tuple[int, Path], ...]:
        prepared: list[tuple[int, Path]] = []
        first = cls._home(run_dir, role_id, 1)
        if path_entry_exists(first):
            prepared.append((1, first))
        generation_root = cls._run_home_root(run_dir) / "session-generations" / role_id
        if not path_entry_exists(generation_root):
            return tuple(prepared)
        info = generation_root.lstat()
        if generation_root.is_symlink() or not stat.S_ISDIR(info.st_mode):
            raise IntegrityError(
                f"DeepSeek Harness generation root is unsafe: {generation_root}"
            )
        for home in sorted(generation_root.iterdir(), key=lambda item: item.name):
            if (
                len(home.name) < 8
                or not home.name.isascii()
                or not home.name.isdigit()
            ):
                raise IntegrityError(
                    f"DeepSeek Harness generation entry is invalid: {home}"
                )
            generation = int(home.name)
            if (
                generation < 2
                or home.name != f"{generation:08d}"
                or home != cls._home(run_dir, role_id, generation)
            ):
                raise IntegrityError(
                    f"DeepSeek Harness generation entry is invalid: {home}"
                )
            home_info = home.lstat()
            if home.is_symlink() or not stat.S_ISDIR(home_info.st_mode):
                raise IntegrityError(
                    f"DeepSeek Harness generation home is unsafe: {home}"
                )
            prepared.append((generation, home))
        return tuple(prepared)

    @staticmethod
    def _profile_manifest(
        candidate: dict[str, Any] | None = None,
        capabilities: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        bundles = ["@deepseek-ai/dsh-base"]
        dependencies = {
            dependency["name"]: dependency["version"]
            for dependency in (capabilities or {}).get("dependencies", [])
        }
        copied_bundles = (capabilities or {}).get("bundles", [])
        bundles.extend(copied_bundles)
        if candidate is not None:
            if (
                candidate["package"] in dependencies
                or candidate["package"] in copied_bundles
            ):
                raise IntegrityError(
                    "workspace DSH plugin conflicts with a copied user Plugin"
                )
            bundles.append(candidate["package"])
            dependencies[candidate["package"]] = candidate["version"]
        bundles.append(_PLUGIN_PACKAGE)
        return {
            "name": "dsh-profile-agent-team",
            "private": True,
            "dependencies": dependencies,
            "dsh": {
                "profile": {"bundles": bundles}
            },
        }

    @staticmethod
    def _package_target(profile: Path, package_name: str) -> Path:
        if not _PACKAGE_NAME_RE.fullmatch(package_name):
            raise IntegrityError(
                f"workspace DSH plugin has an invalid package name: {package_name!r}"
            )
        return profile / "node_modules" / Path(*package_name.split("/"))

    @staticmethod
    def _candidate_contract(
        source: Path,
        *,
        workspace: Path,
        source_relative: str,
    ) -> dict[str, Any]:
        try:
            source_info = source.lstat()
        except OSError as exc:
            raise RoutePreflightError(
                "DSH_PLUGIN_UNAVAILABLE",
                f"workspace DSH plugin is unavailable: {source_relative}",
            ) from exc
        if source.is_symlink() or not stat.S_ISDIR(source_info.st_mode):
            raise RoutePreflightError(
                "DSH_PLUGIN_INVALID",
                f"workspace DSH plugin is not a real directory: {source_relative}",
            )
        try:
            resolved_workspace = workspace.resolve(strict=True)
            resolved_source = source.resolve(strict=True)
            resolved_source.relative_to(resolved_workspace)
        except (OSError, RuntimeError, ValueError) as exc:
            raise RoutePreflightError(
                "DSH_PLUGIN_INVALID",
                f"workspace DSH plugin escapes the workspace: {source_relative}",
            ) from exc
        current = resolved_workspace
        for part in source_relative.split("/"):
            current /= part
            try:
                current_info = current.lstat()
            except OSError as exc:
                raise RoutePreflightError(
                    "DSH_PLUGIN_UNAVAILABLE",
                    f"workspace DSH plugin is unavailable: {source_relative}",
                ) from exc
            if current.is_symlink() or not stat.S_ISDIR(current_info.st_mode):
                raise RoutePreflightError(
                    "DSH_PLUGIN_INVALID",
                    "workspace DSH plugin path must contain only real "
                    f"directories: {source_relative}",
                )
        try:
            manifest = json.loads(read_regular(source / "package.json"))
        except (OSError, json.JSONDecodeError, IntegrityError) as exc:
            raise RoutePreflightError(
                "DSH_PLUGIN_INVALID",
                f"workspace DSH plugin has no valid package.json: {source_relative}",
            ) from exc
        if not isinstance(manifest, dict):
            raise RoutePreflightError(
                "DSH_PLUGIN_INVALID",
                f"workspace DSH plugin manifest is not an object: {source_relative}",
            )
        package_name = manifest.get("name")
        version = manifest.get("version")
        dsh = manifest.get("dsh")
        bundle = dsh.get("bundle") if isinstance(dsh, dict) else None
        patch = bundle.get("patch") if isinstance(bundle, dict) else None
        normalized_patch = (
            patch[2:]
            if isinstance(patch, str) and patch.startswith("./")
            else patch
        )
        if (
            not isinstance(package_name, str)
            or not _PACKAGE_NAME_RE.fullmatch(package_name)
            or package_name in _RESERVED_PLUGIN_PACKAGES
            or not isinstance(version, str)
            or not version
            or not isinstance(normalized_patch, str)
            or not normalized_patch
            or normalized_patch.startswith("/")
            or "\\" in normalized_patch
            or any(
                part in {"", ".", ".."}
                for part in normalized_patch.split("/")
            )
        ):
            raise RoutePreflightError(
                "DSH_PLUGIN_INVALID",
                f"workspace DSH plugin is not an installable bundle: {source_relative}",
            )
        patch_path = source / normalized_patch
        try:
            patch_info = patch_path.lstat()
            patch_path.resolve(strict=True).relative_to(source.resolve(strict=True))
        except (OSError, RuntimeError, ValueError) as exc:
            raise RoutePreflightError(
                "DSH_PLUGIN_INVALID",
                f"workspace DSH plugin bundle patch is unavailable: {source_relative}",
            ) from exc
        if patch_path.is_symlink() or not stat.S_ISREG(patch_info.st_mode):
            raise RoutePreflightError(
                "DSH_PLUGIN_INVALID",
                f"workspace DSH plugin bundle patch is unsafe: {source_relative}",
            )
        try:
            tree = _tree_manifest(source, ignored_names=frozenset({"node_modules"}))
        except (IntegrityError, OSError) as exc:
            raise RoutePreflightError(
                "DSH_PLUGIN_INVALID",
                f"workspace DSH plugin tree is unsafe: {source_relative}",
            ) from exc
        return {
            "schema_version": 1,
            "source": source_relative,
            "package": package_name,
            "version": version,
            "bundle_patch": normalized_patch,
            "manifest": tree,
            "content_sha256": sha256_bytes(canonical_json_bytes(tree)),
        }

    @staticmethod
    def _validate_candidate_snapshot(
        value: Any,
        *,
        expected_source: str,
    ) -> dict[str, Any]:
        if not isinstance(value, dict) or set(value) != {
            "schema_version",
            "source",
            "package",
            "version",
            "bundle_patch",
            "manifest",
            "content_sha256",
        }:
            raise IntegrityError("DeepSeek Harness plugin snapshot has invalid fields")
        package_name = value["package"]
        version = value["version"]
        bundle_patch = value["bundle_patch"]
        manifest = value["manifest"]
        content_sha256 = value["content_sha256"]
        if (
            value["schema_version"] != 1
            or value["source"] != expected_source
            or not isinstance(package_name, str)
            or not _PACKAGE_NAME_RE.fullmatch(package_name)
            or package_name in _RESERVED_PLUGIN_PACKAGES
            or not isinstance(version, str)
            or not version
            or not isinstance(bundle_patch, str)
            or not bundle_patch
            or bundle_patch.startswith("/")
            or "\\" in bundle_patch
            or any(part in {"", ".", ".."} for part in bundle_patch.split("/"))
            or not isinstance(manifest, dict)
            or not manifest
            or any(
                not isinstance(path, str)
                or not path
                or path.startswith("/")
                or "\\" in path
                or any(part in {"", ".", ".."} for part in path.split("/"))
                or not isinstance(digest, str)
                or len(digest) != 64
                or any(char not in "0123456789abcdef" for char in digest)
                for path, digest in manifest.items()
            )
            or not isinstance(content_sha256, str)
            or len(content_sha256) != 64
            or any(char not in "0123456789abcdef" for char in content_sha256)
            or sha256_bytes(canonical_json_bytes(manifest)) != content_sha256
        ):
            raise IntegrityError("DeepSeek Harness plugin snapshot is invalid")
        return value

    @staticmethod
    def _copy_plugin(
        source: Path,
        target: Path,
        *,
        ignored_names: frozenset[str] = frozenset(),
    ) -> None:
        expected = _tree_manifest(source, ignored_names=ignored_names)
        if path_entry_exists(target):
            if target.is_symlink() or not target.is_dir():
                raise IntegrityError(f"DeepSeek Harness plugin target is unsafe: {target}")
            if _tree_manifest(target) != expected:
                raise IntegrityError(
                    "DeepSeek Harness plugin state differs from its frozen source"
                )
            return
        ensure_dir(target.parent)
        temporary = target.parent / (
            f".tmp-{target.name}-{os.getpid()}-{secrets.token_hex(8)}"
        )
        try:
            shutil.copytree(
                source,
                temporary,
                ignore=shutil.ignore_patterns(*ignored_names),
            )
            if _tree_manifest(temporary) != expected:
                raise IntegrityError("copied DeepSeek Harness plugin tree changed")
            for path in temporary.rglob("*"):
                path.chmod(0o700 if path.is_dir() else 0o600)
            temporary.chmod(0o700)
            os.rename(temporary, target)
            fsync_dir(target.parent)
        finally:
            if path_entry_exists(temporary):
                shutil.rmtree(temporary)

    @staticmethod
    def _source_dsh_home() -> Path:
        configured = os.environ.get("DSH_HOME")
        supplied = (
            Path(configured).expanduser()
            if configured
            else Path.home() / ".dsh"
        )
        try:
            return supplied.resolve(strict=path_entry_exists(supplied))
        except OSError as exc:
            raise AgentTeamError(
                "HARNESS_USER_CONFIG_UNREADABLE",
                f"cannot resolve DeepSeek Harness home: {supplied}",
            ) from exc

    @staticmethod
    def _source_profile_name() -> str:
        name = os.environ.get("AGENT_TEAM_DSH_SOURCE_PROFILE", "headless")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", name):
            raise AgentTeamError(
                "HARNESS_USER_CONFIG_INVALID",
                "AGENT_TEAM_DSH_SOURCE_PROFILE is not a valid profile name",
            )
        return name

    @staticmethod
    def _optional_regular(path: Path, *, default: bytes) -> bytes:
        try:
            return read_regular(path)
        except FileNotFoundError:
            return default
        except OSError as exc:
            raise AgentTeamError(
                "HARNESS_USER_CONFIG_UNREADABLE",
                f"cannot read DeepSeek Harness capability config: {path}",
            ) from exc

    @classmethod
    def _resolve_source_package(
        cls,
        *,
        source_home: Path,
        source_profile: Path,
        package_name: str,
    ) -> Path:
        relative = Path(*package_name.split("/"))
        candidates = (
            source_profile / "node_modules" / relative,
            source_home / "profiles" / "node_modules" / relative,
        )
        for candidate in candidates:
            if path_entry_exists(candidate):
                try:
                    return candidate.resolve(strict=True)
                except OSError as exc:
                    raise AgentTeamError(
                        "HARNESS_CAPABILITY_UNAVAILABLE",
                        f"cannot resolve DeepSeek Harness Plugin {package_name!r}",
                    ) from exc
        raise AgentTeamError(
            "HARNESS_CAPABILITY_UNAVAILABLE",
            f"DeepSeek Harness Plugin is not installed in the source profile: "
            f"{package_name}",
        )

    @classmethod
    def _package_version(cls, package_root: Path, package_name: str) -> str:
        try:
            manifest = json.loads(read_regular(package_root / "package.json"))
        except (OSError, json.JSONDecodeError, IntegrityError) as exc:
            raise AgentTeamError(
                "HARNESS_CAPABILITY_UNAVAILABLE",
                f"DeepSeek Harness Plugin has no valid package manifest: {package_name}",
            ) from exc
        version = manifest.get("version") if isinstance(manifest, dict) else None
        if not isinstance(version, str) or not version:
            raise AgentTeamError(
                "HARNESS_CAPABILITY_UNAVAILABLE",
                f"DeepSeek Harness Plugin has no version: {package_name}",
            )
        return version

    @classmethod
    def _new_user_capabilities(
        cls,
        *,
        run_dir: Path,
        role_id: str,
        home: Path,
    ) -> dict[str, object]:
        source_home = cls._source_dsh_home()
        if source_home == home or source_home.is_relative_to(cls._run_home_root(run_dir)):
            raise AgentTeamError(
                "HARNESS_USER_CONFIG_INVALID",
                "DeepSeek Harness source home cannot be an Agent-Team private home",
            )
        profile_name = cls._source_profile_name()
        source_profile = source_home / "profiles" / profile_name
        manifest_path = source_profile / "package.json"
        if path_entry_exists(manifest_path):
            try:
                manifest = json.loads(read_regular(manifest_path))
            except (OSError, json.JSONDecodeError, IntegrityError) as exc:
                raise AgentTeamError(
                    "HARNESS_USER_CONFIG_INVALID",
                    f"DeepSeek Harness source profile is invalid: {source_profile}",
                ) from exc
            if not isinstance(manifest, dict):
                raise AgentTeamError(
                    "HARNESS_USER_CONFIG_INVALID",
                    "DeepSeek Harness source profile manifest must be an object",
                )
        else:
            manifest = {}
        configured_dependencies = manifest.get("dependencies", {})
        dsh = manifest.get("dsh", {})
        profile = dsh.get("profile", {}) if isinstance(dsh, dict) else {}
        configured_bundles = (
            profile.get("bundles", []) if isinstance(profile, dict) else []
        )
        if not isinstance(configured_dependencies, dict) or not all(
            isinstance(name, str) and isinstance(spec, str)
            for name, spec in configured_dependencies.items()
        ):
            raise AgentTeamError(
                "HARNESS_USER_CONFIG_INVALID",
                "DeepSeek Harness source profile dependencies are invalid",
            )
        if not isinstance(configured_bundles, list) or not all(
            isinstance(name, str) for name in configured_bundles
        ):
            raise AgentTeamError(
                "HARNESS_USER_CONFIG_INVALID",
                "DeepSeek Harness source profile bundles are invalid",
            )
        bundles: list[str] = []
        for package_name in configured_bundles:
            if package_name in _SOURCE_SURFACE_BUNDLES:
                continue
            if (
                not _PACKAGE_NAME_RE.fullmatch(package_name)
                or package_name in _RESERVED_PLUGIN_PACKAGES
                or package_name in bundles
            ):
                raise AgentTeamError(
                    "HARNESS_USER_CONFIG_INVALID",
                    f"DeepSeek Harness source bundle is invalid: {package_name!r}",
                )
            bundles.append(package_name)

        package_names = sorted(
            (set(configured_dependencies) | set(bundles))
            - _SOURCE_SURFACE_BUNDLES
        )
        target_profile = home / "profiles" / _PROFILE_NAME
        dependency_snapshots: list[dict[str, object]] = []
        runtime_modules = managed_dsh_runtime() / "node_modules"
        for package_name in package_names:
            if (
                not _PACKAGE_NAME_RE.fullmatch(package_name)
                or package_name in _RESERVED_PLUGIN_PACKAGES
            ):
                raise AgentTeamError(
                    "HARNESS_USER_CONFIG_INVALID",
                    f"DeepSeek Harness source Plugin is invalid: {package_name!r}",
                )
            runtime_package = runtime_modules / Path(*package_name.split("/"))
            source_package = cls._resolve_source_package(
                source_home=source_home,
                source_profile=source_profile,
                package_name=package_name,
            )
            source_version = cls._package_version(source_package, package_name)
            if path_entry_exists(runtime_package):
                runtime_version = cls._package_version(runtime_package, package_name)
                if runtime_version != source_version:
                    raise AgentTeamError(
                        "HARNESS_CAPABILITY_VERSION_CONFLICT",
                        f"DeepSeek Harness Plugin {package_name!r} requires "
                        f"{source_version}, but the managed runtime provides "
                        f"{runtime_version}",
                    )
                continue
            target = cls._package_target(target_profile, package_name)
            dependency_snapshots.append(
                {
                    "name": package_name,
                    "version": source_version,
                    "path": target.relative_to(home).as_posix(),
                    "snapshot": copy_capability_path(source_package, target),
                }
            )

        profile_patch = cls._optional_regular(
            source_profile / "cordis.patch.yml",
            default=b"[]\n",
        )
        home_patch = cls._optional_regular(
            source_home / "cordis.patch.yml",
            default=b"[]\n",
        )
        target_profile.mkdir(mode=0o700, parents=True, exist_ok=True)
        atomic_write(
            target_profile / "cordis.patch.yml",
            profile_patch,
            immutable=True,
        )
        atomic_write(home / "cordis.patch.yml", home_patch, immutable=True)
        return {
            "schema_version": 1,
            "adapter": cls.adapter_id,
            "run_dir": str(run_dir.resolve(strict=True)),
            "role_id": role_id,
            "source_profile": profile_name,
            "bundles": bundles,
            "dependencies": dependency_snapshots,
            "profile_patch_sha256": sha256_bytes(profile_patch),
            "home_patch_sha256": sha256_bytes(home_patch),
            "environment_names": list(
                environment_reference_names(
                    [
                        profile_patch.decode("utf-8", errors="ignore"),
                        home_patch.decode("utf-8", errors="ignore"),
                    ]
                )
            ),
        }

    @classmethod
    def _read_user_capabilities(
        cls,
        *,
        run_dir: Path,
        role_id: str,
        home: Path,
    ) -> dict[str, Any]:
        value = read_json(home / _CAPABILITY_STATE_FILE)
        required = {
            "schema_version",
            "adapter",
            "run_dir",
            "role_id",
            "source_profile",
            "bundles",
            "dependencies",
            "profile_patch_sha256",
            "home_patch_sha256",
            "environment_names",
        }
        dependencies = value.get("dependencies")
        bundles = value.get("bundles")
        environment_names = value.get("environment_names")
        if (
            set(value) != required
            or value.get("schema_version") != 1
            or value.get("adapter") != cls.adapter_id
            or value.get("run_dir") != str(run_dir.resolve(strict=True))
            or value.get("role_id") != role_id
            or not isinstance(value.get("source_profile"), str)
            or not isinstance(bundles, list)
            or not all(
                isinstance(name, str)
                and _PACKAGE_NAME_RE.fullmatch(name)
                and name not in _RESERVED_PLUGIN_PACKAGES
                for name in bundles
            )
            or len(set(bundles)) != len(bundles)
            or not isinstance(dependencies, list)
            or not isinstance(environment_names, list)
            or not all(
                isinstance(name, str)
                and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name)
                for name in environment_names
            )
        ):
            raise IntegrityError("DeepSeek Harness user capability snapshot is invalid")
        profile = home / "profiles" / _PROFILE_NAME
        for key, path in (
            ("profile_patch_sha256", profile / "cordis.patch.yml"),
            ("home_patch_sha256", home / "cordis.patch.yml"),
        ):
            expected = value.get(key)
            if (
                not isinstance(expected, str)
                or len(expected) != 64
                or sha256_bytes(read_regular(path)) != expected
            ):
                raise IntegrityError(
                    "DeepSeek Harness Plugin/MCP patch changed after snapshot"
                )
        for dependency in dependencies:
            if (
                not isinstance(dependency, dict)
                or set(dependency) != {"name", "version", "path", "snapshot"}
                or not isinstance(dependency.get("name"), str)
                or not _PACKAGE_NAME_RE.fullmatch(dependency["name"])
                or dependency["name"] in _RESERVED_PLUGIN_PACKAGES
                or not isinstance(dependency.get("version"), str)
                or not dependency["version"]
                or not isinstance(dependency.get("path"), str)
                or Path(dependency["path"]).is_absolute()
                or any(
                    part in {"", ".", ".."}
                    for part in dependency["path"].split("/")
                )
                or not isinstance(dependency.get("snapshot"), dict)
            ):
                raise IntegrityError(
                    "DeepSeek Harness user Plugin snapshot is invalid"
                )
            assert_capability_path(
                home / dependency["path"],
                dependency["snapshot"],
                subject=f"DeepSeek Harness Plugin {dependency['name']!r}",
            )
        return value

    @classmethod
    def _clone_user_capabilities(
        cls,
        *,
        run_dir: Path,
        role_id: str,
        source_home: Path,
        target_home: Path,
    ) -> dict[str, Any]:
        state = cls._read_user_capabilities(
            run_dir=run_dir,
            role_id=role_id,
            home=source_home,
        )
        target_profile = target_home / "profiles" / _PROFILE_NAME
        ensure_dir(target_profile)
        for relative in (
            Path("profiles", _PROFILE_NAME, "cordis.patch.yml"),
            Path("cordis.patch.yml"),
        ):
            atomic_write(
                target_home / relative,
                read_regular(source_home / relative),
                immutable=True,
            )
        for dependency in state["dependencies"]:
            target = target_home / dependency["path"]
            descriptor = copy_capability_path(
                source_home / dependency["path"],
                target,
            )
            if descriptor != dependency["snapshot"]:
                raise IntegrityError(
                    "cloned DeepSeek Harness Plugin differs from its snapshot"
                )
        atomic_json(
            target_home / _CAPABILITY_STATE_FILE,
            state,
            immutable=True,
        )
        return cls._read_user_capabilities(
            run_dir=run_dir,
            role_id=role_id,
            home=target_home,
        )

    def prepare_capability_state(
        self,
        *,
        run_dir: Path,
        role_id: str,
        launch_mode: str,
    ) -> None:
        super().prepare_capability_state(
            run_dir=run_dir,
            role_id=role_id,
            launch_mode=launch_mode,
        )
        managed_dsh_runtime_report()
        for directory in self._home_hierarchy(run_dir, role_id, 1):
            ensure_dir(directory)
            info = directory.lstat()
            if directory.is_symlink() or not stat.S_ISDIR(info.st_mode):
                raise IntegrityError(
                    f"DeepSeek Harness state directory is unsafe: {directory}"
                )
            directory.chmod(0o700)
        home = self._home(run_dir, role_id, 1)
        marker_path = home / "agent-team-home.json"
        marker = self._marker(run_dir, role_id, 1)
        if path_entry_exists(marker_path):
            if read_json(marker_path) != marker:
                raise IntegrityError(
                    f"DeepSeek Harness home belongs to another frozen Run: {home}"
                )
        else:
            atomic_json(marker_path, marker, immutable=True)
        ensure_dir(home / "profiles" / _PROFILE_NAME)
        capability_path = home / _CAPABILITY_STATE_FILE
        if path_entry_exists(capability_path):
            self._read_user_capabilities(
                run_dir=run_dir,
                role_id=role_id,
                home=home,
            )
        else:
            atomic_json(
                capability_path,
                self._new_user_capabilities(
                    run_dir=run_dir,
                    role_id=role_id,
                    home=home,
                ),
                immutable=True,
            )

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
        self.prepare_capability_state(
            run_dir=run_dir,
            role_id=role_id,
            launch_mode=launch_mode,
        )
        # Validate the exact managed runtime before creating any role state.
        managed_dsh_runtime_report()
        for directory in self._home_hierarchy(
            run_dir,
            role_id,
            session_generation,
        ):
            ensure_dir(directory)
            info = directory.lstat()
            if directory.is_symlink() or not stat.S_ISDIR(info.st_mode):
                raise IntegrityError(
                    f"DeepSeek Harness state directory is unsafe: {directory}"
                )
            directory.chmod(0o700)
        home = self._home(run_dir, role_id, session_generation)
        marker_path = home / "agent-team-home.json"
        marker = self._marker(run_dir, role_id, session_generation)
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
        if session_generation == 1:
            capabilities = self._read_user_capabilities(
                run_dir=run_dir,
                role_id=role_id,
                home=home,
            )
        else:
            capabilities = self._clone_user_capabilities(
                run_dir=run_dir,
                role_id=role_id,
                source_home=self._home(run_dir, role_id, 1),
                target_home=home,
            )
        team = load_team(run_dir)
        role = team.roles[role_id]
        candidate: dict[str, Any] | None = None
        candidate_state = home / _PLUGIN_STATE_FILE
        if role.dsh_plugin is not None:
            if path_entry_exists(candidate_state):
                candidate = self._validate_candidate_snapshot(
                    read_json(candidate_state),
                    expected_source=role.dsh_plugin,
                )
            else:
                source = team.workspace / role.dsh_plugin
                candidate = self._candidate_contract(
                    source,
                    workspace=team.workspace,
                    source_relative=role.dsh_plugin,
                )
                if candidate["package"] in {
                    dependency["name"]
                    for dependency in capabilities["dependencies"]
                } or candidate["package"] in capabilities["bundles"]:
                    raise RoutePreflightError(
                        "DSH_PLUGIN_CONFLICT",
                        "workspace DSH plugin conflicts with a copied user Plugin: "
                        f"{candidate['package']}",
                    )
                target = self._package_target(profile, candidate["package"])
                self._copy_plugin(
                    source,
                    target,
                    ignored_names=frozenset({"node_modules"}),
                )
                atomic_json(candidate_state, candidate, immutable=True)
        elif path_entry_exists(candidate_state):
            raise IntegrityError(
                f"unexpected DeepSeek Harness plugin snapshot: {candidate_state}"
            )
        atomic_write(
            profile / "package.json",
            canonical_json_bytes(self._profile_manifest(candidate, capabilities)),
            immutable=True,
        )
        atomic_write(
            profile / "pnpm-workspace.yaml",
            b"packages:\n  - .\n\nnodeLinker: hoisted\nautoInstallPeers: false\n",
            immutable=True,
        )
        self._copy_plugin(
            dsh_tui_source(),
            profile / "node_modules" / "@agent-team" / "dsh-tui",
        )
        if candidate is not None:
            target = self._package_target(profile, candidate["package"])
            if not path_entry_exists(target) or _tree_manifest(target) != candidate[
                "manifest"
            ]:
                raise IntegrityError(
                    "DeepSeek Harness installed plugin differs from its frozen snapshot"
                )
        ensure_dir(home / "sessions")
        (home / "sessions").chmod(0o700)

    def _assert_home(self, context: TurnLaunchContext) -> Path:
        run_dir = Path(context.turn_dir).parent.parent
        home = self._home(run_dir, context.role_id, context.session_generation)
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
            run_dir,
            context.role_id,
            context.session_generation,
        ):
            raise AgentTeamError(
                "HARNESS_STATE_NOT_PREPARED",
                f"DeepSeek Harness state is not prepared for {context.role_id}",
            )
        team = load_team(run_dir)
        role = team.roles[context.role_id]
        capabilities = self._read_user_capabilities(
            run_dir=run_dir,
            role_id=context.role_id,
            home=home,
        )
        candidate_state = home / _PLUGIN_STATE_FILE
        candidate = (
            self._validate_candidate_snapshot(
                read_json(candidate_state),
                expected_source=role.dsh_plugin or "",
            )
            if path_entry_exists(candidate_state)
            else None
        )
        if (role.dsh_plugin is None) != (candidate is None):
            raise IntegrityError("DeepSeek Harness plugin snapshot does not match role")
        profile = home / "profiles" / _PROFILE_NAME
        if candidate is not None:
            target = self._package_target(profile, candidate["package"])
            if _tree_manifest(target) != candidate["manifest"]:
                raise IntegrityError(
                    "DeepSeek Harness installed plugin changed after provisioning"
                )
        expected_manifest = canonical_json_bytes(
            self._profile_manifest(candidate, capabilities)
        )
        if read_regular(profile / "package.json") != expected_manifest:
            raise IntegrityError(
                "DeepSeek Harness profile changed after plugin provisioning"
            )
        return home

    def worker_environment_names(
        self,
        *,
        run_dir: Path,
        role_id: str,
        options: HarnessLaunchOptions | None = None,
    ) -> tuple[str, ...]:
        del options
        capabilities = self._read_user_capabilities(
            run_dir=run_dir,
            role_id=role_id,
            home=self._home(run_dir, role_id, 1),
        )
        return tuple(capabilities["environment_names"])

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
            model_provider=context.model_provider,
            model_provider_config=context.model_provider_config,
        )
        self.assert_launch_options(options)
        home = self._assert_home(context)
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
        argv = [
            str(self.executable()),
            "--profile",
            _PROFILE_NAME,
            session_option,
            session_ref,
        ]
        if options.model is not None:
            provider, model = options.model.split("/", 1)
            argv.extend(("--provider", provider, "--model", model))
        if options.reasoning_effort is not None:
            argv.extend(("--reasoning-effort", options.reasoning_effort))
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
        candidate_state = home / _PLUGIN_STATE_FILE
        if path_entry_exists(candidate_state):
            env["AGENT_TEAM_DSH_PLUGIN_SHA256"] = read_json(candidate_state)[
                "content_sha256"
            ]
            env["AGENT_TEAM_DSH_PLUGIN_GENERATION"] = str(
                context.session_generation
            )
        return LaunchSpec(
            adapter_id=self.adapter_id,
            argv=tuple(argv),
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
        homes = self._prepared_homes(run_dir, role_id)
        if not homes:
            raise IntegrityError(
                f"DeepSeek Harness state is unavailable for role: {role_id}"
            )
        resolved_runtime = managed_dsh_runtime().resolve(strict=True)
        resolved_workspace = load_team(run_dir).workspace.resolve(strict=True)
        for generation, home in homes:
            info = home.lstat()
            if home.is_symlink() or not stat.S_ISDIR(info.st_mode):
                raise IntegrityError(f"DeepSeek Harness state is unsafe: {home}")
            if read_json(home / "agent-team-home.json") != self._marker(
                run_dir,
                role_id,
                generation,
            ):
                raise IntegrityError(
                    f"DeepSeek Harness state is not owned by this Run: {home}"
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
                            if not (
                                target.is_relative_to(resolved_home)
                                or target.is_relative_to(resolved_runtime)
                                or target.is_relative_to(resolved_workspace)
                            ):
                                raise ValueError
                        except (OSError, RuntimeError, ValueError) as exc:
                            raise IntegrityError(
                                "DeepSeek Harness state symlink escapes managed "
                                f"roots: {path}"
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
            for directory in self._home_hierarchy(
                run_dir,
                role_id,
                generation,
            ):
                directory.chmod(0o700)

    def has_prepared_run_state(
        self,
        *,
        run_dir: Path,
        role_id: str,
        launch_mode: str,
    ) -> bool:
        self.assert_launch_mode(launch_mode)
        return bool(self._prepared_homes(run_dir, role_id))

    def parse_stream_record(self, record: StreamRecord) -> AdapterEvidence | None:
        del record
        return None

    def recoverable_termination_kind(
        self,
        result: ProcessResult,
        evidence: AdapterEvidenceSnapshot,
    ) -> str | None:
        self.assert_launch_mode(result.launch_mode)
        if (
            result.launch_mode == "interactive"
            and result.process_exit_code == _OUTPUT_LIMIT_EXIT_CODE
            and result.group_quiescent
            and evidence.agent_execution_started
            and not evidence.adapter_completed
            and not evidence.permission_required
            and evidence.observed_session_ref is not None
            and evidence.session_unavailable_reason is None
        ):
            return "output_limit"
        return None

    def candidate_activation_failure(
        self,
        *,
        run_dir: Path,
        role_id: str,
        session_generation: int,
        result: ProcessResult,
        evidence: AdapterEvidenceSnapshot,
    ) -> str | None:
        self.assert_launch_mode(result.launch_mode)
        team = load_team(run_dir)
        role = team.roles[role_id]
        if role.dsh_plugin is None:
            return None
        if (
            result.launch_mode != "interactive"
            or result.process_exit_code in {None, 0}
            or result.termination_kind != "crash"
            or not result.group_quiescent
            or evidence.adapter_completed
            or evidence.permission_required
            or evidence.observed_session_ref is None
            or evidence.session_unavailable_reason is not None
        ):
            return None
        home = self._home(run_dir, role_id, session_generation)
        candidate_state = home / _PLUGIN_STATE_FILE
        if not path_entry_exists(candidate_state):
            raise IntegrityError(
                "DeepSeek Harness candidate activation has no frozen snapshot"
            )
        self._validate_candidate_snapshot(
            read_json(candidate_state),
            expected_source=role.dsh_plugin,
        )
        initialized = self._session_refs(home / "sessions", team.workspace)
        if evidence.observed_session_ref in initialized:
            return None
        return (
            "Candidate-bound DeepSeek Harness exited before its fresh Session "
            "was durably initialized; inspect the preserved Turn trace and "
            "Harness loader diagnostics."
        )
