"""Pydantic domain models for Phase 5B.1 execution results and status tracking."""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from aiaddons.core.models.agent import Scope


class ExecutionStatus(StrEnum):
    """Lifecycle status for Phase 5B.1 operation execution."""

    SUCCESS = "success"
    FAILED = "failed"
    ROLLED_BACK = "rolled_back"
    PARTIAL_FAILURE = "partial_failure"
    UNSUPPORTED = "unsupported"


class OperationExecutionResult(BaseModel):
    """Result of an individual operation execution step."""

    model_config = ConfigDict(extra="forbid")

    op_type: str
    description: str
    status: ExecutionStatus
    target_root: str
    target_path: str | None = None
    error_message: str | None = None


class ExecutionResult(BaseModel):
    """Aggregate result of an installation plan execution."""

    model_config = ConfigDict(extra="forbid")

    addon_id: str
    target_agent: str
    scope: Scope
    status: ExecutionStatus
    executed_operations: list[OperationExecutionResult] = Field(default_factory=list)
    rolled_back_operations: list[OperationExecutionResult] = Field(default_factory=list)
    error_message: str | None = None
