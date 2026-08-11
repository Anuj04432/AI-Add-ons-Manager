"""Custom exception hierarchy for AI Add-ons Manager."""

from pathlib import Path


class AIAddonsError(Exception):
    """Base exception for all AI Add-ons Manager errors."""


class RegistryFetchError(AIAddonsError):
    """Error raised when fetching or querying the registry fails."""


class ManifestValidationError(AIAddonsError):
    """Exception raised when a manifest file is malformed or fails validation."""

    def __init__(self, file_path: Path | str, message: str) -> None:
        self.file_path = Path(file_path)
        self.message = message
        super().__init__(f"Validation failed for '{file_path}': {message}")


class IncompatibleAgentError(AIAddonsError):
    """Error raised when an add-on is incompatible with an AI agent."""


class SecurityValidationError(AIAddonsError):
    """Error raised when security checks or path traversal validation fails."""


class InstallationError(AIAddonsError):
    """Base exception for installation failures."""


class SecretResolutionError(InstallationError):
    """Error raised when resolving or validating required secrets fails."""


class UnsupportedScopeError(InstallationError):
    """Error raised when a requested configuration scope is not supported."""


class UnsupportedIntegrationTypeError(InstallationError):
    """Error raised when an integration type is not supported by an installer or agent."""


class InstallationPlanningError(InstallationError):
    """Error raised when generating an installation plan fails."""


class ExternalExecutionError(InstallationError):
    """Base exception for external process execution failures."""


class ExecutableNotFoundError(ExternalExecutionError):
    """Error raised when an approved runtime binary cannot be located on PATH."""


class ProcessExecutionError(ExternalExecutionError):
    """Error raised when an external process execution fails with a non-zero exit code."""


class ProcessTimeoutError(ExternalExecutionError):
    """Error raised when an external process execution times out."""
