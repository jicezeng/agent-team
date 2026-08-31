from __future__ import annotations

import re
import secrets
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .errors import IntegrityError, InvalidArgument
from .state import validate_state_root
from .util import (
    canonical_json_bytes,
    read_json,
    require_keys,
    require_schema_version,
)

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
TEAM_V2_REQUIRED = TEAM_REQUIRED | {"observability"}
TEAM_V3_REQUIRED = TEAM_V2_REQUIRED
TEAM_V4_REQUIRED = TEAM_V3_REQUIRED
TEAM_V5_REQUIRED = TEAM_V4_REQUIRED
TEAM_V6_REQUIRED = TEAM_V5_REQUIRED
TEAM_V7_REQUIRED = TEAM_V6_REQUIRED
TEAM_V8_REQUIRED = TEAM_V7_REQUIRED | {"workflow"}
MAX_LIMIT_VALUE = (1 << 31) - 1
DEFAULT_MAX_TRACE_BYTES = 64 * 1024 * 1024
LEGACY_AUDIT_PAYLOAD_SECTIONS = ("Decision rationale", "Evidence")
REQUIRED_AUDIT_PAYLOAD_SECTIONS = (
    "Decision rationale",
    "Acceptance coverage",
    "Open findings",
    "Evidence",
)
MAX_MODEL_ID_LENGTH = 2048
MODEL_PROVIDER_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
ENVIRONMENT_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
CODEX_MODEL_PROVIDER_CONFIG_KEYS = frozenset(
    {
        "name",
        "base_url",
        "env_key",
        "env_http_headers",
        "requires_openai_auth",
        "wire_api",
        "request_max_retries",
        "stream_max_retries",
        "stream_idle_timeout_ms",
        "supports_standalone_web_search",
        "supports_websockets",
    }
)
CODEX_BUILTIN_MODEL_PROVIDERS = frozenset(
    {"openai", "ollama", "lmstudio", "amazon-bedrock"}
)
CODEX_REASONING_EFFORTS = frozenset(
    {"minimal", "low", "medium", "high", "xhigh", "max", "ultra"}
)
CLAUDE_REASONING_EFFORTS = frozenset(
    {"auto", "low", "medium", "high", "xhigh", "max"}
)
CLAUDE_MODEL_PROVIDERS = frozenset(
    {"anthropic", "bedrock", "vertex", "foundry", "gateway"}
)
CLAUDE_PROVIDER_SETTING_FIELDS = {
    "anthropic": frozenset(),
    "gateway": frozenset({"base_url"}),
    "bedrock": frozenset({"region", "base_url", "skip_auth"}),
    "vertex": frozenset({"region", "project_id", "base_url", "skip_auth"}),
    "foundry": frozenset({"resource", "base_url", "skip_auth"}),
}
CLAUDE_PROVIDER_CREDENTIAL_ENVIRONMENTS = {
    "anthropic": frozenset(
        {
            "ANTHROPIC_API_KEY",
            "ANTHROPIC_AUTH_TOKEN",
            "ANTHROPIC_CUSTOM_HEADERS",
            "CLAUDE_CODE_OAUTH_TOKEN",
        }
    ),
    "gateway": frozenset(
        {
            "ANTHROPIC_API_KEY",
            "ANTHROPIC_AUTH_TOKEN",
            "ANTHROPIC_CUSTOM_HEADERS",
            "CLAUDE_CODE_OAUTH_TOKEN",
        }
    ),
    "bedrock": frozenset(
        {
            "AWS_ACCESS_KEY_ID",
            "AWS_SECRET_ACCESS_KEY",
            "AWS_SESSION_TOKEN",
            "AWS_BEARER_TOKEN_BEDROCK",
            "AWS_PROFILE",
            "AWS_CONFIG_FILE",
            "AWS_SHARED_CREDENTIALS_FILE",
            "AWS_ROLE_ARN",
            "AWS_ROLE_SESSION_NAME",
            "AWS_WEB_IDENTITY_TOKEN_FILE",
            "AWS_CONTAINER_CREDENTIALS_RELATIVE_URI",
            "AWS_CONTAINER_CREDENTIALS_FULL_URI",
            "AWS_CONTAINER_AUTHORIZATION_TOKEN",
            "AWS_CONTAINER_AUTHORIZATION_TOKEN_FILE",
        }
    ),
    "vertex": frozenset(
        {
            "GOOGLE_APPLICATION_CREDENTIALS",
            "GOOGLE_CLOUD_PROJECT",
            "CLOUDSDK_AUTH_ACCESS_TOKEN",
        }
    ),
    "foundry": frozenset(
        {
            "ANTHROPIC_FOUNDRY_API_KEY",
            "AZURE_CLIENT_ID",
            "AZURE_TENANT_ID",
            "AZURE_CLIENT_SECRET",
            "AZURE_CLIENT_CERTIFICATE_PATH",
        }
    ),
}
DSH_REASONING_EFFORTS = frozenset({"off", "high", "max"})
EXTERNAL_ADAPTER_IDS = (
    "codex",
    "claude-code",
    "opencode",
    "deepseek-harness",
)
ROLE_LAUNCH_MODES = frozenset({"headless", "interactive"})
DEFAULT_EXTERNAL_LAUNCH_PROFILE = "full-access"


@dataclass(frozen=True, slots=True)
class ObservabilityPolicy:
    audit_mode: str = "standard"
    redaction: str = "standard"
    max_trace_bytes: int = DEFAULT_MAX_TRACE_BYTES
    raw_retention: str = "redacted"
    required_payload_sections: tuple[str, ...] = ()

    def to_json(self) -> dict[str, Any]:
        return {
            "audit_mode": self.audit_mode,
            "redaction": self.redaction,
            "max_trace_bytes": self.max_trace_bytes,
            "raw_retention": self.raw_retention,
            "required_payload_sections": list(self.required_payload_sections),
        }


@dataclass(frozen=True, slots=True)
class WorkflowPolicy:
    """Small, immutable control-plane constraints for role-selected actions."""

    allowed_handoffs: dict[str, tuple[str, ...]] | None = None
    read_only_roles: tuple[str, ...] = ()

    def to_json(self) -> dict[str, Any]:
        return {
            "allowed_handoffs": (
                None
                if self.allowed_handoffs is None
                else {
                    role_id: list(self.allowed_handoffs[role_id])
                    for role_id in sorted(self.allowed_handoffs)
                }
            ),
            "read_only_roles": list(self.read_only_roles),
        }


@dataclass(frozen=True, slots=True)
class Role:
    role_id: str
    binding: str
    adapter: str | None = None
    session_policy: str | None = None
    launch_profile: str | None = None
    launch_profile_sha256: str | None = None
    model: str | None = None
    reasoning_effort: str | None = None
    fast_mode: bool | None = None
    launch_mode: str | None = None
    dsh_plugin: str | None = None
    model_provider: str | None = None
    model_provider_config: dict[str, Any] | None = None

    def to_json(self) -> dict[str, Any]:
        if self.binding == "origin":
            return {"binding": "origin"}
        model_provider = self.model_provider
        model_provider_config = self.model_provider_config
        if self.adapter == "codex" and model_provider is None:
            model_provider = "openai"
        elif self.adapter == "claude-code" and model_provider is None:
            model_provider = "anthropic"
        if (
            self.adapter == "claude-code"
            and model_provider == "anthropic"
            and model_provider_config is None
        ):
            model_provider_config = {
                "settings": {},
                "credential_environment_names": [],
            }
        return {
            "binding": "external",
            "adapter": self.adapter,
            "session_policy": self.session_policy,
            "launch_profile": self.launch_profile,
            "launch_profile_sha256": self.launch_profile_sha256,
            "launch_mode": (
                "interactive" if self.launch_mode is None else self.launch_mode
            ),
            "harness_options": {
                "model": self.model,
                "reasoning_effort": self.reasoning_effort,
                "fast_mode": self.fast_mode,
                "model_provider": model_provider,
                "model_provider_config": model_provider_config,
            },
            "dsh_plugin": self.dsh_plugin,
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
    observability: ObservabilityPolicy = field(default_factory=ObservabilityPolicy)
    workflow: WorkflowPolicy = field(default_factory=WorkflowPolicy)
    config_schema_version: int = 8

    def allows_handoff(self, from_role: str, to_role: str) -> bool:
        allowed = self.workflow.allowed_handoffs
        return allowed is None or to_role in allowed.get(from_role, ())

    def workspace_is_read_only(self, role_id: str) -> bool:
        return role_id in self.workflow.read_only_roles

    def to_json(self) -> dict[str, Any]:
        return {
            "schema_version": 8,
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
            "observability": self.observability.to_json(),
            "workflow": self.workflow.to_json(),
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


def valid_model_id(value: object) -> bool:
    return (
        isinstance(value, str)
        and bool(value)
        and value == value.strip()
        and not value.startswith("-")
        and len(value) <= MAX_MODEL_ID_LENGTH
        and not any(ord(char) < 32 or ord(char) == 127 for char in value)
    )


def valid_model_provider_id(value: object) -> bool:
    return isinstance(value, str) and MODEL_PROVIDER_ID_RE.fullmatch(value) is not None


def codex_model_provider_config_error(value: object) -> str | None:
    """Return why a frozen Codex provider config is unsafe or malformed."""

    if not isinstance(value, dict):
        return "must be an object"
    if not all(isinstance(key, str) for key in value):
        return "contains a non-string field name"
    unknown = set(value) - CODEX_MODEL_PROVIDER_CONFIG_KEYS
    if unknown:
        return "contains unsupported fields: " + ", ".join(sorted(unknown))
    base_url = value.get("base_url")
    if (
        not isinstance(base_url, str)
        or not base_url
        or base_url != base_url.strip()
        or any(ord(char) < 32 or ord(char) == 127 for char in base_url)
    ):
        return "must contain a non-empty base_url"
    try:
        parsed_url = urlsplit(base_url)
        hostname = parsed_url.hostname
    except ValueError:
        return "base_url is invalid"
    if (
        parsed_url.scheme not in {"http", "https"}
        or not parsed_url.netloc
        or hostname is None
        or parsed_url.username is not None
        or parsed_url.password is not None
        or parsed_url.query
        or parsed_url.fragment
    ):
        return "base_url must be an HTTP(S) URL without credentials, query, or fragment"
    name = value.get("name")
    if name is not None and (
        not isinstance(name, str)
        or not name
        or len(name) > 256
        or any(ord(char) < 32 or ord(char) == 127 for char in name)
    ):
        return "name must be a non-empty printable string of at most 256 characters"
    env_key = value.get("env_key")
    if env_key is not None and (
        not isinstance(env_key, str) or ENVIRONMENT_NAME_RE.fullmatch(env_key) is None
    ):
        return "env_key must be an environment variable name"
    headers = value.get("env_http_headers")
    if headers is not None:
        if not isinstance(headers, dict):
            return "env_http_headers must be an object"
        for header, environment_name in headers.items():
            if (
                not isinstance(header, str)
                or not header
                or any(ord(char) < 32 or ord(char) == 127 for char in header)
            ):
                return "env_http_headers contains an invalid header name"
            if (
                not isinstance(environment_name, str)
                or ENVIRONMENT_NAME_RE.fullmatch(environment_name) is None
            ):
                return "env_http_headers must reference environment variable names"
    requires_openai_auth = value.get("requires_openai_auth")
    if requires_openai_auth is not None and not isinstance(
        requires_openai_auth, bool
    ):
        return "requires_openai_auth must be a boolean"
    wire_api = value.get("wire_api")
    if wire_api is not None and wire_api != "responses":
        return "wire_api must be responses"
    for field_name in (
        "request_max_retries",
        "stream_max_retries",
        "stream_idle_timeout_ms",
    ):
        item = value.get(field_name)
        if item is not None and (
            isinstance(item, bool)
            or not isinstance(item, int)
            or item < 0
            or item > MAX_LIMIT_VALUE
        ):
            return f"{field_name} must be an integer in 0..{MAX_LIMIT_VALUE}"
    for field_name in (
        "supports_standalone_web_search",
        "supports_websockets",
    ):
        item = value.get(field_name)
        if item is not None and not isinstance(item, bool):
            return f"{field_name} must be a boolean"
    return None


def claude_model_provider_config_error(
    provider: object,
    value: object,
) -> str | None:
    """Return why a frozen Claude provider route is unsafe or malformed."""

    if not isinstance(provider, str) or provider not in CLAUDE_MODEL_PROVIDERS:
        return "provider must be one of: " + ", ".join(
            sorted(CLAUDE_MODEL_PROVIDERS)
        )
    if not isinstance(value, dict):
        return "must be an object"
    if set(value) != {"settings", "credential_environment_names"}:
        return (
            "must contain exactly settings and credential_environment_names"
        )
    settings = value["settings"]
    if not isinstance(settings, dict) or not all(
        isinstance(key, str) for key in settings
    ):
        return "settings must be an object with string field names"
    allowed_settings = CLAUDE_PROVIDER_SETTING_FIELDS[provider]
    unknown = set(settings) - allowed_settings
    if unknown:
        return "settings contains unsupported fields: " + ", ".join(
            sorted(unknown)
        )
    if provider == "gateway" and "base_url" not in settings:
        return "gateway settings must contain base_url"
    for field_name, item in settings.items():
        if field_name == "skip_auth":
            if not isinstance(item, bool):
                return "settings.skip_auth must be a boolean"
            continue
        if (
            not isinstance(item, str)
            or not item
            or item != item.strip()
            or len(item) > MAX_MODEL_ID_LENGTH
            or any(ord(char) < 32 or ord(char) == 127 for char in item)
        ):
            return (
                f"settings.{field_name} must be a non-empty printable string "
                f"of at most {MAX_MODEL_ID_LENGTH} characters"
            )
        if field_name == "base_url":
            try:
                parsed_url = urlsplit(item)
                hostname = parsed_url.hostname
            except ValueError:
                return "settings.base_url is invalid"
            if (
                parsed_url.scheme not in {"http", "https"}
                or not parsed_url.netloc
                or hostname is None
                or parsed_url.username is not None
                or parsed_url.password is not None
                or parsed_url.query
                or parsed_url.fragment
            ):
                return (
                    "settings.base_url must be an HTTP(S) URL without "
                    "credentials, query, or fragment"
                )
    credential_names = value["credential_environment_names"]
    if (
        not isinstance(credential_names, list)
        or not all(isinstance(name, str) for name in credential_names)
        or credential_names != sorted(set(credential_names))
    ):
        return "credential_environment_names must be a sorted unique string list"
    allowed_credentials = CLAUDE_PROVIDER_CREDENTIAL_ENVIRONMENTS[provider]
    unsupported_credentials = set(credential_names) - allowed_credentials
    if unsupported_credentials:
        return "credential_environment_names contains unsupported names: " + ", ".join(
            sorted(unsupported_credentials)
        )
    return None


def valid_opencode_model_id(value: object) -> bool:
    """Validate OpenCode's provider/model selector without guessing providers."""

    if not valid_model_id(value) or not isinstance(value, str):
        return False
    provider, separator, model = value.partition("/")
    return bool(
        separator
        and provider
        and model
        and "#" not in provider
        and "#" not in model
    )


def valid_dsh_model_id(value: object) -> bool:
    """Validate DeepSeek Harness' explicit provider/model route."""

    return valid_opencode_model_id(value)


def valid_opencode_variant(value: object) -> bool:
    """Accept provider-specific OpenCode variants as an opaque CLI value."""

    return bool(
        isinstance(value, str)
        and value
        and value == value.strip()
        and not value.startswith("-")
        and "#" not in value
        and len(value) <= 256
        and not any(ord(char) < 32 or ord(char) == 127 for char in value)
    )


def generate_run_id() -> str:
    import datetime as dt

    stamp = dt.datetime.now(dt.UTC).strftime("%Y%m%d-%H%M%S")
    return f"at-{stamp}-{secrets.token_hex(3)}"


def _require_exact(
    value: dict[str, Any],
    required: set[str],
    subject: str,
) -> None:
    require_keys(value, required=required, subject=subject)


def parse_team(value: dict[str, Any], *, run_dir: Path | None = None) -> Team:
    schema_version = require_schema_version(
        value,
        (1, 2, 3, 4, 5, 6, 7, 8),
        subject="team.json",
    )
    if schema_version == 1:
        _require_exact(value, TEAM_REQUIRED, "team.json")
    elif schema_version == 2:
        _require_exact(value, TEAM_V2_REQUIRED, "team.json")
    elif schema_version == 3:
        _require_exact(value, TEAM_V3_REQUIRED, "team.json")
    elif schema_version == 4:
        _require_exact(value, TEAM_V4_REQUIRED, "team.json")
    elif schema_version == 5:
        _require_exact(value, TEAM_V5_REQUIRED, "team.json")
    elif schema_version == 6:
        _require_exact(value, TEAM_V6_REQUIRED, "team.json")
    elif schema_version == 7:
        _require_exact(value, TEAM_V7_REQUIRED, "team.json")
    elif schema_version == 8:
        _require_exact(value, TEAM_V8_REQUIRED, "team.json")
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
            external_fields = {
                "binding",
                "adapter",
                "session_policy",
                "launch_profile",
                "launch_profile_sha256",
            }
            if schema_version >= 3:
                external_fields.add("harness_options")
            if schema_version >= 4:
                external_fields.add("launch_mode")
            if schema_version >= 5:
                external_fields.add("dsh_plugin")
            _require_exact(
                role_value,
                external_fields,
                f"role {role_id}",
            )
            adapter = role_value["adapter"]
            policy = role_value["session_policy"]
            profile = role_value["launch_profile"]
            fingerprint = role_value["launch_profile_sha256"]
            launch_mode = (
                role_value["launch_mode"] if schema_version >= 4 else "headless"
            )
            dsh_plugin = role_value["dsh_plugin"] if schema_version >= 5 else None
            if not isinstance(adapter, str) or adapter not in EXTERNAL_ADAPTER_IDS:
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
            if (
                not isinstance(launch_mode, str)
                or launch_mode not in ROLE_LAUNCH_MODES
            ):
                raise IntegrityError(f"invalid launch mode for {role_id}")
            if dsh_plugin is not None and (
                adapter != "deepseek-harness"
                or not isinstance(dsh_plugin, str)
                or not dsh_plugin
                or dsh_plugin.startswith("/")
                or "\\" in dsh_plugin
                or any(part in {"", ".", ".."} for part in dsh_plugin.split("/"))
            ):
                raise IntegrityError(f"invalid DSH plugin path for {role_id}")
            model: str | None = None
            reasoning_effort: str | None = None
            fast_mode: bool | None = None
            model_provider: str | None = None
            model_provider_config: dict[str, Any] | None = None
            if schema_version >= 3:
                options = role_value["harness_options"]
                if not isinstance(options, dict):
                    raise IntegrityError(
                        f"harness options for {role_id} must be an object"
                    )
                option_fields = {"model", "reasoning_effort", "fast_mode"}
                if schema_version >= 6:
                    option_fields.update(
                        {"model_provider", "model_provider_config"}
                    )
                _require_exact(options, option_fields, f"role {role_id} harness options")
                model = options["model"]
                reasoning_effort = options["reasoning_effort"]
                fast_mode = options["fast_mode"]
                if schema_version >= 6:
                    model_provider = options["model_provider"]
                    model_provider_config = options["model_provider_config"]
                if model is not None and not valid_model_id(model):
                    raise IntegrityError(f"invalid model for {role_id}")
                if adapter == "opencode" and not valid_opencode_model_id(model):
                    raise IntegrityError(
                        f"opencode model for {role_id} must use provider/model"
                    )
                if (
                    adapter == "deepseek-harness"
                    and model is not None
                    and not valid_dsh_model_id(model)
                ):
                    raise IntegrityError(
                        f"deepseek-harness model for {role_id} must use provider/model"
                    )
                if adapter == "codex":
                    valid_effort = reasoning_effort in CODEX_REASONING_EFFORTS
                elif adapter == "claude-code":
                    valid_effort = reasoning_effort in CLAUDE_REASONING_EFFORTS
                elif adapter == "deepseek-harness":
                    valid_effort = reasoning_effort in DSH_REASONING_EFFORTS
                else:
                    valid_effort = valid_opencode_variant(reasoning_effort)
                if reasoning_effort is not None and not valid_effort:
                    raise IntegrityError(
                        f"invalid reasoning effort for {role_id}"
                    )
                if adapter == "codex":
                    if fast_mode is not None and not isinstance(fast_mode, bool):
                        raise IntegrityError(f"invalid fast mode for {role_id}")
                    if model_provider is not None and not valid_model_provider_id(
                        model_provider
                    ):
                        raise IntegrityError(f"invalid model provider for {role_id}")
                    if schema_version >= 6 and model_provider is None:
                        raise IntegrityError(
                            f"model provider is required for Codex role {role_id}"
                        )
                    if model_provider_config is not None:
                        if model_provider is None:
                            raise IntegrityError(
                                f"model provider config has no provider for {role_id}"
                            )
                        provider_error = codex_model_provider_config_error(
                            model_provider_config
                        )
                        if provider_error is not None:
                            raise IntegrityError(
                                f"invalid model provider config for {role_id}: "
                                f"{provider_error}"
                            )
                    if (
                        model_provider in CODEX_BUILTIN_MODEL_PROVIDERS
                        and model_provider_config is not None
                    ):
                        raise IntegrityError(
                            f"built-in model provider cannot be overridden for "
                            f"{role_id}"
                        )
                    if (
                        model_provider is not None
                        and model_provider not in CODEX_BUILTIN_MODEL_PROVIDERS
                        and model_provider_config is None
                    ):
                        raise IntegrityError(
                            f"custom model provider has no definition for {role_id}"
                        )
                elif adapter == "claude-code" and schema_version >= 7:
                    if fast_mode is not None:
                        raise IntegrityError(
                            f"fast mode is not supported for {role_id}"
                        )
                    if model_provider not in CLAUDE_MODEL_PROVIDERS:
                        supported = ", ".join(sorted(CLAUDE_MODEL_PROVIDERS))
                        raise IntegrityError(
                            f"Claude model provider for {role_id} must be one of: "
                            f"{supported}"
                        )
                    provider_error = claude_model_provider_config_error(
                        model_provider,
                        model_provider_config,
                    )
                    if provider_error is not None:
                        raise IntegrityError(
                            f"invalid Claude model provider config for {role_id}: "
                            f"{provider_error}"
                        )
                elif fast_mode is not None:
                    raise IntegrityError(
                        f"fast mode is not supported for {role_id}"
                    )
                elif model_provider is not None or model_provider_config is not None:
                    raise IntegrityError(
                        "model provider is not a separate option for "
                        f"{adapter} role {role_id}"
                    )
            roles[role_id] = Role(
                role_id,
                "external",
                adapter,
                policy,
                profile,
                fingerprint,
                model,
                reasoning_effort,
                fast_mode,
                launch_mode,
                dsh_plugin,
                model_provider,
                model_provider_config,
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
    if schema_version == 1:
        observability = ObservabilityPolicy(
            redaction="none",
            raw_retention="keep",
        )
    else:
        observability_value = value["observability"]
        if not isinstance(observability_value, dict):
            raise IntegrityError("team.json observability must be an object")
        _require_exact(
            observability_value,
            {
                "audit_mode",
                "redaction",
                "max_trace_bytes",
                "raw_retention",
                "required_payload_sections",
            },
            "team.json observability",
        )
        audit_mode = observability_value["audit_mode"]
        redaction = observability_value["redaction"]
        max_trace_bytes = observability_value["max_trace_bytes"]
        raw_retention = observability_value["raw_retention"]
        required_sections = observability_value["required_payload_sections"]
        if audit_mode not in {"standard", "full"}:
            raise IntegrityError("observability audit_mode is invalid")
        if redaction not in {"standard", "none"}:
            raise IntegrityError("observability redaction is invalid")
        if (
            isinstance(max_trace_bytes, bool)
            or not isinstance(max_trace_bytes, int)
            or max_trace_bytes < 1024
            or max_trace_bytes > MAX_LIMIT_VALUE
        ):
            raise IntegrityError(
                "observability max_trace_bytes must be an integer in "
                f"1024..{MAX_LIMIT_VALUE}"
            )
        if raw_retention not in {"redacted", "keep", "delete"}:
            raise IntegrityError("observability raw_retention is invalid")
        if raw_retention == "redacted" and redaction != "standard":
            raise IntegrityError(
                "redacted raw retention requires the standard redaction policy"
            )
        if (
            not isinstance(required_sections, list)
            or not all(
                isinstance(section, str) and section.strip()
                for section in required_sections
            )
            or len({section.casefold() for section in required_sections})
            != len(required_sections)
        ):
            raise IntegrityError(
                "observability required_payload_sections is invalid"
            )
        observability = ObservabilityPolicy(
            audit_mode=audit_mode,
            redaction=redaction,
            max_trace_bytes=max_trace_bytes,
            raw_retention=raw_retention,
            required_payload_sections=tuple(required_sections),
        )
        if audit_mode == "full":
            origin_roles = sorted(
                role_id for role_id, role in roles.items() if role.binding == "origin"
            )
            if origin_roles:
                raise IntegrityError(
                    "full audit mode requires every business role to use an "
                    f"External binding: {', '.join(origin_roles)}"
                )
            folded_sections = {
                section.casefold()
                for section in observability.required_payload_sections
            }
            # The exact payload contract is frozen in every Run. Historical
            # Runs used the two-section contract and must remain readable and
            # recoverable after an upgrade; new Runs are strengthened by
            # ``make_team`` below.
            missing_sections = [
                section
                for section in LEGACY_AUDIT_PAYLOAD_SECTIONS
                if section.casefold() not in folded_sections
            ]
            if missing_sections:
                raise IntegrityError(
                    "full audit mode requires payload sections: "
                    + ", ".join(LEGACY_AUDIT_PAYLOAD_SECTIONS)
                )
            if raw_retention == "delete":
                raise IntegrityError(
                    "full audit mode cannot delete the retained Harness stream"
                )
    workflow = WorkflowPolicy()
    if schema_version >= 8:
        workflow_value = value["workflow"]
        if not isinstance(workflow_value, dict):
            raise IntegrityError("team.json workflow must be an object")
        _require_exact(
            workflow_value,
            {"allowed_handoffs", "read_only_roles"},
            "team.json workflow",
        )
        allowed_value = workflow_value["allowed_handoffs"]
        allowed_handoffs: dict[str, tuple[str, ...]] | None
        if allowed_value is None:
            allowed_handoffs = None
        else:
            if not isinstance(allowed_value, dict) or set(allowed_value) != set(roles):
                raise IntegrityError(
                    "workflow.allowed_handoffs must contain every configured role"
                )
            allowed_handoffs = {}
            for role_id, targets in allowed_value.items():
                if (
                    not isinstance(targets, list)
                    or not all(isinstance(target, str) for target in targets)
                    or targets != sorted(set(targets))
                    or any(target not in roles for target in targets)
                ):
                    raise IntegrityError(
                        f"workflow.allowed_handoffs for {role_id} is invalid"
                    )
                allowed_handoffs[role_id] = tuple(targets)
        read_only_value = workflow_value["read_only_roles"]
        if (
            not isinstance(read_only_value, list)
            or not all(isinstance(role_id, str) for role_id in read_only_value)
            or read_only_value != sorted(set(read_only_value))
            or any(role_id not in roles for role_id in read_only_value)
        ):
            raise IntegrityError("workflow.read_only_roles is invalid")
        workflow = WorkflowPolicy(
            allowed_handoffs=allowed_handoffs,
            read_only_roles=tuple(read_only_value),
        )
    return Team(
        run_id,
        workspace,
        origin["harness"],
        roles,
        initial,
        max_turns,
        wall,
        observability,
        workflow,
        schema_version,
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
    observability: ObservabilityPolicy | None = None,
    workflow: WorkflowPolicy | None = None,
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
    effective_observability = observability or ObservabilityPolicy()
    if (
        effective_observability.audit_mode == "full"
        or effective_observability.required_payload_sections
    ):
        folded_sections = {
            section.casefold()
            for section in effective_observability.required_payload_sections
        }
        missing_sections = [
            section
            for section in REQUIRED_AUDIT_PAYLOAD_SECTIONS
            if section.casefold() not in folded_sections
        ]
        if missing_sections:
            raise InvalidArgument(
                "new audited Runs require payload sections: "
                + ", ".join(REQUIRED_AUDIT_PAYLOAD_SECTIONS)
            )
    team = Team(
        run_id,
        workspace,
        origin_harness,
        roles,
        initial_role,
        max_turns,
        max_wall_time_seconds,
        effective_observability,
        workflow or WorkflowPolicy(),
        8,
    )
    try:
        return parse_team(team.to_json())
    except IntegrityError as exc:
        raise InvalidArgument(exc.message) from exc
