from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class AgentTeamError(Exception):
    code: str
    message: str
    exit_code: int = 1
    evidence_paths: tuple[str, ...] = ()

    def __str__(self) -> str:
        return f"{self.code}: {self.message}"


class IntegrityError(AgentTeamError):
    def __init__(self, message: str, *paths: str) -> None:
        super().__init__("TEAM_CORRUPTED", message, 1, tuple(paths))


class RecoverableTurnArtifactError(IntegrityError):
    """Damage confined to a uniquely identified Turn's deferred artifacts."""

    def __init__(self, artifact: str, message: str, *paths: str) -> None:
        super().__init__(message, *paths)
        self.artifact = artifact


class RoutePreflightError(AgentTeamError):
    """A fixable target artifact rejected before a Handoff is staged."""


class InvalidArgument(AgentTeamError):
    def __init__(self, message: str) -> None:
        super().__init__("INVALID_ARGUMENT", message, 2)


class FullAccessConfirmationRequired(AgentTeamError):
    def __init__(self, roles: tuple[str, ...]) -> None:
        rendered = ", ".join(roles)
        super().__init__(
            "FULL_ACCESS_CONFIRMATION_REQUIRED",
            "this UNSTARTED Run grants full host filesystem and network "
            f"access without per-command approvals to: {rendered}; after "
            "the user confirms this once for the new Run, retry start with "
            "--confirm-full-access",
            2,
        )


class RunNotFound(AgentTeamError):
    def __init__(self, message: str = "run not found") -> None:
        super().__init__("RUN_NOT_FOUND", message, 3)


class ObservationIOError(AgentTeamError):
    def __init__(self, message: str) -> None:
        super().__init__("OBSERVATION_IO_ERROR", message, 4)


class ObservationInternalError(AgentTeamError):
    def __init__(self, message: str) -> None:
        super().__init__("OBSERVATION_INTERNAL_ERROR", message, 4)
