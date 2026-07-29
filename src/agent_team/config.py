from __future__ import annotations

import re
import secrets
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .errors import IntegrityError, InvalidArgument
from .state import validate_state_root
from .util import canonical_json_bytes, read_json, require_keys


ROLE_ID_RE = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
RUN_ID_RE = re.compile(r"^at-[a-zA-Z0-9][a-zA-Z0-9_-]{0,62}$")
TEAM_REQUIRED = {
    "schema_version",
    "run_id",
    "workspace",
    "origin",
    "roles",
    "initial_role",
    "limits",
}
MAX_LIMIT_VALUE = (1 << 31) - 1


@dataclass(frozen=True, slots=True)
class Role:
    role_id: str
    binding: str
    adapter: str | None = None
    session_policy: str | None = None
    launch_profile: str | None = None
    launch_profile_sha256: str | None = None

    def to_json(self) -> dict[str, Any]:
        if self.binding == "origin":
            return {"binding": "origin"}
        return {
            "binding": "external",
            "adapter": self.adapter,
            "session_policy": self.session_policy,
            "launch_profile": self.launch_profile,
            "launch_profile_sha256": self.launch_profile_sha256,
        }


@dataclass(frozen=True, slots=True)
class Team:
    run_id: str
    workspace: Path
    origin_harness: str
    roles: dict[str, Role]
    initial_role: str
    max_turns: int
    max_wall_time_seconds: int

    def to_json(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "run_id": self.run_id,
            "workspace": str(self.workspace),
            "origin": {
                "harness": self.origin_harness,
                "session_mode": "embedded",
            },
            "roles": {
                role_id: self.roles[role_id].to_json() for role_id in sorted(self.roles)
            },
            "initial_role": self.initial_role,
            "limits": {
                "max_turns": self.max_turns,
                "max_wall_time_seconds": self.max_wall_time_seconds,
            },
        }

    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.to_json())


def validate_role_id(role_id: str) -> str:
    if not isinstance(role_id, str) or not ROLE_ID_RE.fullmatch(role_id):
        raise InvalidArgument(
            f"invalid role id {role_id!r}; expected [a-z][a-z0-9_-]{{0,31}}"
        )
    return role_id


def validate_run_id(run_id: str) -> str:
    if not isinstance(run_id, str) or not RUN_ID_RE.fullmatch(run_id):
        raise InvalidArgument(f"invalid run id: {run_id!r}")
    return run_id


def generate_run_id() -> str:
    import datetime as dt

    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d-%H%M%S")
    return f"at-{stamp}-{secrets.token_hex(3)}"


def _require_exact(
    value: dict[str, Any],
    required: set[str],
    subject: str,
) -> None:
    require_keys(value, required=required, subject=subject)


def parse_team(value: dict[str, Any], *, run_dir: Path | None = None) -> Team:
    _require_exact(value, TEAM_REQUIRED, "team.json")
    if value["schema_version"] != 1:
        raise IntegrityError("unsupported team.json schema")
    run_id = value["run_id"]
    if not isinstance(run_id, str) or not RUN_ID_RE.fullmatch(run_id):
        raise IntegrityError("team.json run_id is invalid")
    if run_dir is not None and run_dir.name != run_id:
        raise IntegrityError(
            "team.json run_id does not match run directory", "team.json"
        )
    if not isinstance(value["workspace"], str):
        raise IntegrityError("team.json workspace is invalid")
    try:
        workspace = Path(value["workspace"]).resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise IntegrityError("team.json workspace cannot be resolved") from exc
    if value["workspace"] != str(workspace):
        raise IntegrityError("team.json workspace is not a canonical real path")
    if run_dir is not None:
        validate_state_root(workspace)
        if run_dir.parent != workspace / ".agent-team" / "runs":
            raise IntegrityError("run directory is not under configured workspace")
    origin = value["origin"]
    if not isinstance(origin, dict):
        raise IntegrityError("team.json origin must be an object")
    _require_exact(origin, {"harness", "session_mode"}, "team.json origin")
    if origin["session_mode"] != "embedded":
        raise IntegrityError("origin.session_mode must be embedded")
    if not isinstance(origin["harness"], str) or not origin["harness"]:
        raise IntegrityError("origin.harness is invalid")
    roles_value = value["roles"]
    if not isinstance(roles_value, dict) or not roles_value:
        raise IntegrityError("team.json roles must be a non-empty object")
    roles: dict[str, Role] = {}
    for role_id, role_value in roles_value.items():
        if not isinstance(role_id, str) or not ROLE_ID_RE.fullmatch(role_id):
            raise IntegrityError(f"invalid role id in team.json: {role_id!r}")
        if not isinstance(role_value, dict):
            raise IntegrityError(f"role {role_id} must be an object")
        binding = role_value.get("binding")
        if binding == "origin":
            _require_exact(role_value, {"binding"}, f"role {role_id}")
            roles[role_id] = Role(role_id, "origin")
        elif binding == "external":
            _require_exact(
                role_value,
                {
                    "binding",
                    "adapter",
                    "session_policy",
                    "launch_profile",
                    "launch_profile_sha256",
                },
                f"role {role_id}",
            )
            adapter = role_value["adapter"]
            policy = role_value["session_policy"]
            profile = role_value["launch_profile"]
            fingerprint = role_value["launch_profile_sha256"]
            if not isinstance(adapter, str) or adapter not in {
                "codex",
                "claude-code",
            }:
                raise IntegrityError(f"unsupported adapter for {role_id}: {adapter!r}")
            if not isinstance(policy, str) or policy not in {"resume", "fresh"}:
                raise IntegrityError(
                    f"invalid session policy for {role_id}: {policy!r}"
                )
            if not isinstance(profile, str) or not profile:
                raise IntegrityError(f"invalid launch profile for {role_id}")
            if (
                not isinstance(fingerprint, str)
                or len(fingerprint) != 64
                or any(char not in "0123456789abcdef" for char in fingerprint)
            ):
                raise IntegrityError(f"invalid launch profile hash for {role_id}")
            roles[role_id] = Role(
                role_id,
                "external",
                adapter,
                policy,
                profile,
                fingerprint,
            )
        else:
            raise IntegrityError(f"invalid binding for role {role_id}: {binding!r}")
    initial = value["initial_role"]
    if not isinstance(initial, str) or initial not in roles:
        raise IntegrityError("initial_role must reference an existing role")
    limits = value["limits"]
    if not isinstance(limits, dict):
        raise IntegrityError("team.json limits must be an object")
    _require_exact(
        limits,
        {"max_turns", "max_wall_time_seconds"},
        "team.json limits",
    )
    max_turns = limits["max_turns"]
    wall = limits["max_wall_time_seconds"]
    if (
        isinstance(max_turns, bool)
        or not isinstance(max_turns, int)
        or max_turns < 1
        or max_turns > MAX_LIMIT_VALUE
    ):
        raise IntegrityError("max_turns must be a positive integer")
    if (
        isinstance(wall, bool)
        or not isinstance(wall, int)
        or wall < 1
        or wall > MAX_LIMIT_VALUE
    ):
        raise IntegrityError("max_wall_time_seconds must be a positive integer")
    return Team(
        run_id,
        workspace,
        origin["harness"],
        roles,
        initial,
        max_turns,
        wall,
    )


def load_team(run_dir: Path) -> Team:
    return parse_team(read_json(run_dir / "team.json"), run_dir=run_dir)


def make_team(
    *,
    run_id: str,
    workspace: Path,
    origin_harness: str,
    roles: dict[str, Role],
    initial_role: str,
    max_turns: int,
    max_wall_time_seconds: int,
) -> Team:
    validate_run_id(run_id)
    try:
        workspace = workspace.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise InvalidArgument("workspace cannot be resolved") from exc
    if not roles:
        raise InvalidArgument("at least one role is required")
    for role_id, role in roles.items():
        validate_role_id(role_id)
        if role.role_id != role_id:
            raise InvalidArgument(f"role key mismatch for {role_id}")
    if initial_role not in roles:
        raise InvalidArgument("initial role must reference a configured role")
    if (
        isinstance(max_turns, bool)
        or not isinstance(max_turns, int)
        or isinstance(max_wall_time_seconds, bool)
        or not isinstance(max_wall_time_seconds, int)
        or not 1 <= max_turns <= MAX_LIMIT_VALUE
        or not 1 <= max_wall_time_seconds <= MAX_LIMIT_VALUE
    ):
        raise InvalidArgument(
            f"turn and wall-time limits must be integers in "
            f"1..{MAX_LIMIT_VALUE}"
        )
    if not origin_harness:
        raise InvalidArgument("origin harness must not be empty")
    team = Team(
        run_id,
        workspace,
        origin_harness,
        roles,
        initial_role,
        max_turns,
        max_wall_time_seconds,
    )
    return parse_team(team.to_json())
