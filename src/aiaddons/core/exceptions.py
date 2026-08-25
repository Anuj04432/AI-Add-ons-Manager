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


class StateLockTimeoutError(InstallationError):
    """Error raised when acquiring an exclusive file lock on state/lockfile times out."""

    def __init__(self, lock_path: Path | str, timeout: float, message: str | None = None) -> None:
        self.lock_path = Path(lock_path)
        self.timeout = timeout
        default_msg = (
            f"Timed out after {timeout:.1f} seconds waiting to acquire lock '{self.lock_path}'. "
            "Another aiaddons process appears to be running on this target. "
            "Please wait for it to finish and retry."
        )
        super().__init__(message or default_msg)


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


class VerificationError(InstallationError):
    """Base exception for installation verification failures."""


class VerificationFailedError(VerificationError):
    """Exception raised when one or more verification checks fail."""


class VerificationPathSecurityError(VerificationError, SecurityValidationError):
    """Exception raised when verification detects path traversal or target root escape."""


class SourceAcquisitionError(InstallationError):
    """Base exception for add-on source acquisition failures."""


class ChecksumMismatchError(SourceAcquisitionError):
    """Error raised when downloaded content hash does not match manifest checksum."""


class ArchiveSecurityError(SourceAcquisitionError, SecurityValidationError):
    """Error raised when archive extraction detects security violations (ZipSlip, symlinks)."""


class GitAcquisitionError(SourceAcquisitionError):
    """Error raised when git clone, fetch, or checkout fails."""
