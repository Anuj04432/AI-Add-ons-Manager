"""Structured data models for Phase 5B.2 secure external package execution."""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class ExternalRuntime(StrEnum):
    """Approved external runtimes for Phase 5B.2 execution."""

    NPX = "npx"
    UVX = "uvx"
    PIP = "pip"
    NPM = "npm"
    GIT = "git"
    PYTHON = "python"
    NODE = "node"


class ExternalExecutionRequest(BaseModel):
    """Structured request for external process execution."""

    model_config = ConfigDict(extra="forbid")

    runtime: ExternalRuntime
    package_name: str | None = None
    package_version: str | None = None
    args: list[str] = Field(default_factory=list)
    cwd: str | None = None
    env_vars: dict[str, str] = Field(default_factory=dict)
    timeout: float = 120.0


class ExternalExecutionResult(BaseModel):
    """Structured result of an external process execution."""

    model_config = ConfigDict(extra="forbid")

    success: bool
    runtime: ExternalRuntime
    executable_path: str
    command_vector: list[str]
    return_code: int
    stdout: str
    stderr: str
    duration: float
    error_message: str | None = None
