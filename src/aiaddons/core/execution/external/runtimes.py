"""Typed runtime adapters for Phase 5B.2 external process argument vector construction."""

from typing import Protocol

from aiaddons.core.exceptions import SecurityValidationError
from aiaddons.core.execution.external.models import ExternalExecutionRequest, ExternalRuntime
from aiaddons.core.models.manifest import (
    validate_mcp_package_name,
    validate_safe_relative_path,
)


class BaseRuntimeAdapter(Protocol):
    """Protocol for runtime argument vector builders."""

    def build_command_vector(self, request: ExternalExecutionRequest) -> list[str]:
        """Construct a validated executable + argument vector for the process request."""
        ...


class NpxRuntimeAdapter:
    """Runtime adapter for npx execution."""

    def build_command_vector(self, request: ExternalExecutionRequest) -> list[str]:
        cmd = ["npx", "-y"]
        if request.package_name:
            validated_pkg = validate_mcp_package_name(request.package_name)
            if request.package_version:
                pkg_spec = f"{validated_pkg}@{request.package_version}"
            else:
                pkg_spec = validated_pkg
            cmd.append(pkg_spec)
        cmd.extend(request.args)
        return cmd


class UvxRuntimeAdapter:
    """Runtime adapter for uvx execution."""

    def build_command_vector(self, request: ExternalExecutionRequest) -> list[str]:
        cmd = ["uvx"]
        if request.package_name:
            validated_pkg = validate_mcp_package_name(request.package_name)
            if request.package_version:
                pkg_spec = f"{validated_pkg}@{request.package_version}"
            else:
                pkg_spec = validated_pkg
            cmd.append(pkg_spec)
        cmd.extend(request.args)
        return cmd


class PipRuntimeAdapter:
    """Runtime adapter for pip package operations."""

    def build_command_vector(self, request: ExternalExecutionRequest) -> list[str]:
        cmd = ["pip", "install", "--no-deps"]
        if request.package_name:
            validated_pkg = validate_mcp_package_name(request.package_name)
            if request.package_version:
                pkg_spec = f"{validated_pkg}=={request.package_version}"
            else:
                pkg_spec = validated_pkg
            cmd.append(pkg_spec)
        cmd.extend(request.args)
        return cmd


class NpmRuntimeAdapter:
    """Runtime adapter for npm package operations."""

    def build_command_vector(self, request: ExternalExecutionRequest) -> list[str]:
        cmd = ["npm", "install"]
        if request.package_name:
            validated_pkg = validate_mcp_package_name(request.package_name)
            if request.package_version:
                pkg_spec = f"{validated_pkg}@{request.package_version}"
            else:
                pkg_spec = validated_pkg
            cmd.append(pkg_spec)
        cmd.extend(request.args)
        return cmd


class GitRuntimeAdapter:
    """Runtime adapter for git repository operations."""

    def build_command_vector(self, request: ExternalExecutionRequest) -> list[str]:
        if not request.args or len(request.args) < 2:
            msg = "Git clone request requires repository URL and target destination path in args."
            raise SecurityValidationError(msg)

        repo_url = request.args[0].strip()
        target_dir = request.args[1].strip()

        # Validate URL and path
        if not (
            repo_url.startswith("https://")
            or repo_url.startswith("git://")
            or repo_url.startswith("http://")
        ):
            msg = f"Invalid git repository URL '{repo_url}'."
            raise SecurityValidationError(msg)

        validate_safe_relative_path(target_dir)
        return ["git", "clone", "--depth", "1", repo_url, target_dir]


class PythonRuntimeAdapter:
    """Runtime adapter for python module execution."""

    def build_command_vector(self, request: ExternalExecutionRequest) -> list[str]:
        cmd = ["python"]
        if request.package_name:
            cmd.extend(["-m", request.package_name])
        cmd.extend(request.args)
        return cmd


class NodeRuntimeAdapter:
    """Runtime adapter for node script execution."""

    def build_command_vector(self, request: ExternalExecutionRequest) -> list[str]:
        cmd = ["node"]
        if request.package_name:
            validate_safe_relative_path(request.package_name)
            cmd.append(request.package_name)
        cmd.extend(request.args)
        return cmd


def get_runtime_adapter(runtime: ExternalRuntime) -> BaseRuntimeAdapter:
    """Factory function returning the typed runtime adapter for a runtime."""
    adapters: dict[ExternalRuntime, BaseRuntimeAdapter] = {
        ExternalRuntime.NPX: NpxRuntimeAdapter(),
        ExternalRuntime.UVX: UvxRuntimeAdapter(),
        ExternalRuntime.PIP: PipRuntimeAdapter(),
        ExternalRuntime.NPM: NpmRuntimeAdapter(),
        ExternalRuntime.GIT: GitRuntimeAdapter(),
        ExternalRuntime.PYTHON: PythonRuntimeAdapter(),
        ExternalRuntime.NODE: NodeRuntimeAdapter(),
    }
    adapter = adapters.get(runtime)
    if not adapter:
        msg = f"No runtime adapter registered for '{runtime}'."
        raise SecurityValidationError(msg)
    return adapter
