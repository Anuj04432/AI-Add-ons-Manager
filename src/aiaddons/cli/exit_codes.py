"""Standard exit codes for AI Add-ons Manager CLI."""

from enum import IntEnum


class ExitCode(IntEnum):
    """Standardized CLI exit codes."""

    SUCCESS = 0
    INVALID_INPUT = 1
    COMPATIBILITY_FAILURE = 3
    SECURITY_FAILURE = 4
    EXECUTION_FAILURE = 5
    VERIFICATION_FAILURE = 6
