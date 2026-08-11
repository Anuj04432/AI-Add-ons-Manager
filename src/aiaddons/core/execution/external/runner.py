"""Secure external runner for Phase 5B.2 process execution."""

import os
import shutil
import subprocess
import time
from pathlib import Path

from aiaddons.core.exceptions import (
    ExecutableNotFoundError,
    ProcessExecutionError,
    ProcessTimeoutError,
    SecurityValidationError,
)
from aiaddons.core.execution.external.models import (
    ExternalExecutionRequest,
    ExternalExecutionResult,
    ExternalRuntime,
)
from aiaddons.core.execution.external.runtimes import get_runtime_adapter
from aiaddons.core.execution.external.security import (
    ALLOWED_EXECUTABLES,
    mask_secrets_in_text,
    validate_argument_vector,
    validate_environment_dict,
    validate_runtime_name,
)


class ExternalRunner:
    """Secure runner for approved external package manager process execution."""

    def resolve_executable(self, runtime: ExternalRuntime) -> Path:
        """Locate approved runtime executable on system PATH using shutil.which.

        Returns:
            Path: Resolved executable path.

        Raises:
            ExecutableNotFoundError: If the executable is not found or is prohibited.
        """
        executable_name = runtime.value
        resolved = shutil.which(executable_name)

        if not resolved:
            msg = (
                f"Approved runtime executable '{executable_name}' not found on system PATH. "
                "Ensure the required tool is installed."
            )
            raise ExecutableNotFoundError(msg)

        resolved_path = Path(resolved).resolve()
        filename_lower = resolved_path.name.lower()

        if filename_lower not in ALLOWED_EXECUTABLES:
            msg = (
                f"Resolved binary '{resolved_path}' ({filename_lower}) is not in the approved "
                "executable allowlist."
            )
            raise SecurityValidationError(msg)

        return resolved_path

    def execute(
        self,
        request: ExternalExecutionRequest,
        dry_run: bool = False,
    ) -> ExternalExecutionResult:
        """Safely execute an external package manager process with timeout and output capture."""
        # 1. Validate runtime name against allowlist
        runtime = validate_runtime_name(request.runtime)

        # 2. Get typed runtime adapter and build command vector
        adapter = get_runtime_adapter(runtime)
        cmd_vector = adapter.build_command_vector(request)

        # 3. Validate command vector arguments against metacharacters and forbidden flags
        validate_argument_vector(cmd_vector)

        # 4. Resolve approved executable on PATH
        resolved_executable = self.resolve_executable(runtime)
        cmd_vector[0] = str(resolved_executable)

        # 5. Sanitize and validate environment dictionary
        custom_env = validate_environment_dict(request.env_vars)
        safe_env = dict(os.environ)
        safe_env.update(custom_env)

        # Handle working directory
        resolved_cwd: str | None = None
        if request.cwd:
            resolved_cwd = str(Path(request.cwd).expanduser().resolve())

        # Collect secrets for masking in logs/output
        secret_values = list(custom_env.values())

        # 6. Handle Dry-Run Mode (mandatory zero subprocess calls)
        if dry_run:
            cmd_summary = " ".join(cmd_vector)
            return ExternalExecutionResult(
                success=True,
                runtime=runtime,
                executable_path=str(resolved_executable),
                command_vector=cmd_vector,
                return_code=0,
                stdout=f"[DRY-RUN] Would execute: {cmd_summary}",
                stderr="",
                duration=0.0,
            )

        # 7. Real Subprocess Execution (shell=False strictly enforced)
        start_time = time.perf_counter()
        try:
            proc = subprocess.Popen(
                cmd_vector,
                shell=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=resolved_cwd,
                env=safe_env,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            try:
                stdout_raw, stderr_raw = proc.communicate(timeout=request.timeout)
                duration = time.perf_counter() - start_time
            except subprocess.TimeoutExpired as err:
                try:
                    proc.kill()
                    proc.communicate()
                except Exception:
                    pass
                msg = f"External process '{runtime}' timed out after {request.timeout} seconds."
                masked_msg = mask_secrets_in_text(msg, secret_values)
                raise ProcessTimeoutError(masked_msg) from err
        except ProcessTimeoutError:
            raise
        except (OSError, subprocess.SubprocessError) as err:
            msg = f"Subprocess launch error for '{runtime}': {err}"
            masked_msg = mask_secrets_in_text(msg, secret_values)
            raise ProcessExecutionError(masked_msg) from err

        stdout_masked = mask_secrets_in_text(stdout_raw, secret_values)
        stderr_masked = mask_secrets_in_text(stderr_raw, secret_values)
        success = proc.returncode == 0

        err_msg: str | None = None
        if not success:
            err_msg = (
                f"Process '{runtime}' failed with exit code {proc.returncode}: "
                f"{stderr_masked.strip()}"
            )

        return ExternalExecutionResult(
            success=success,
            runtime=runtime,
            executable_path=str(resolved_executable),
            command_vector=cmd_vector,
            return_code=proc.returncode,
            stdout=stdout_masked,
            stderr=stderr_masked,
            duration=duration,
            error_message=err_msg,
        )
