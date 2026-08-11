"""Pydantic domain models for Installation Verification & Post-Install Integrity (Phase 5B.9)."""

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from aiaddons.core.models.agent import Scope


class VerificationStatus(StrEnum):
    """Lifecycle and evaluation status of an individual verification check or overall result."""

    PENDING = "pending"
    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"


class VerificationCheck(BaseModel):
    """Representation of an individual verification check.

    CRITICAL: Must never store secret values in expected_value or actual_value.
    """

    model_config = ConfigDict(extra="forbid")

    check_id: str
    description: str
    status: VerificationStatus
    expected_value: str | None = None
    actual_value: str | None = None
    warning_info: str | None = None
    error_info: str | None = None


class VerificationResult(BaseModel):
    """Representation of the complete post-install or rollback verification decision."""

    model_config = ConfigDict(extra="forbid")

    addon_id: str
    addon_name: str
    target_agent: str
    target_scope: Scope
    status: VerificationStatus
    verified: bool
    checks: list[VerificationCheck] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    timestamp: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
