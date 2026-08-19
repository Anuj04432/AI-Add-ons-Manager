from pathlib import Path

from aiaddons.core.exceptions import SecurityValidationError
from aiaddons.core.execution.external.models import ExternalRuntime
from aiaddons.core.models.manifest import FORBIDDEN_SHELL_PATTERNS, validate_env_var_name

ALLOWED_RUNTIMES: set[str] = {"npx", "uvx", "pip", "npm", "git", "python", "node"}

ALLOWED_EXECUTABLES: set[str] = {
    "npx",
    "npx.cmd",
    "uvx",
    "uvx.exe",
    "pip",
    "pip.exe",
    "npm",
    "npm.cmd",
    "git",
    "git.exe",
    "python",
    "python.exe",
    "node",
    "node.exe",
}

FORBIDDEN_ARG_FLAGS: set[str] = {
    "--eval",
    "-e",
    "-c",
    "--exec",
    "--command",
    "--config-file",
}


def validate_runtime_name(runtime: str | ExternalRuntime) -> ExternalRuntime:
    """Validate that the requested runtime belongs to the approved allowlist."""
    val = str(runtime).strip().lower()
    if val not in ALLOWED_RUNTIMES:
        msg = f"Runtime '{runtime}' is not in the approved external execution allowlist."
        raise SecurityValidationError(msg)
    return ExternalRuntime(val)


def validate_argument_vector(
    args: list[str],
    runtime: str | ExternalRuntime | None = None,
) -> list[str]:
    """Validate argument vector elements against shell metacharacters and forbidden flags."""
    validated: list[str] = []
    is_git = False
    if runtime is not None:
        val = runtime.value if isinstance(runtime, ExternalRuntime) else str(runtime)
        is_git = val.strip().lower() == "git"
    elif args:
        first_token = Path(args[0]).stem.lower()
        is_git = first_token in {"git", "git.exe"}

    forbidden_flags = FORBIDDEN_ARG_FLAGS - {"-c"} if is_git else FORBIDDEN_ARG_FLAGS

    for idx, arg in enumerate(args):
        if not isinstance(arg, str):
            msg = f"Argument at index {idx} must be a string, got {type(arg).__name__}."
            raise SecurityValidationError(msg)

        if "\0" in arg:
            msg = f"Null byte detected in argument at index {idx}."
            raise SecurityValidationError(msg)

        # Check shell metacharacters
        for char in FORBIDDEN_SHELL_PATTERNS:
            if char in arg:
                msg = f"Shell operator '{char}' detected in argument at index {idx}: '{arg}'."
                raise SecurityValidationError(msg)

        # Check dangerous inline evaluation flags
        lowered = arg.strip().lower()
        if lowered in forbidden_flags:
            msg = f"Forbidden flag '{arg}' detected in argument vector."
            raise SecurityValidationError(msg)

        # Check for inline command execution flag formats like --eval=... or -e ...
        if any(lowered.startswith(f"{flag}=") for flag in forbidden_flags):
            msg = f"Forbidden inline code execution flag '{arg}' detected."
            raise SecurityValidationError(msg)

        validated.append(arg)
    return validated


def validate_environment_dict(env: dict[str, str]) -> dict[str, str]:
    """Validate environment variables, ensuring no restricted variables are set."""
    validated: dict[str, str] = {}
    for name, value in env.items():
        try:
            validated_name = validate_env_var_name(name)
        except ValueError as err:
            raise SecurityValidationError(str(err)) from err

        if "\0" in value:
            msg = f"Null byte detected in value for environment variable '{name}'."
            raise SecurityValidationError(msg)
        validated[validated_name] = value
    return validated


def mask_secrets_in_text(text: str, secrets: list[str]) -> str:
    """Mask secret values in stdout, stderr, exception messages, or logs."""
    if not text:
        return text
    masked = text
    for secret in secrets:
        if secret:
            masked = masked.replace(secret, "***MASKED***")
    return masked
