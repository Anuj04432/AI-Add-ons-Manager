"""Pydantic domain models for secret management and resolution."""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class SecretStatus(StrEnum):
    """Resolution status for an environment variable or secret."""

    CONFIGURED = "configured"
    REQUIRED_MISSING = "required_missing"
    OPTIONAL_MISSING = "optional_missing"
    NOT_SECRET = "not_secret"


class ResolvedSecret(BaseModel):
    """Model representing a resolved secret's metadata and non-persistent value."""

    model_config = ConfigDict(extra="forbid")

    name: str
    is_secret: bool = True
    is_required: bool = True
    description: str | None = None
    status: SecretStatus = SecretStatus.CONFIGURED

    # Secret value is stored ONLY in memory and strictly excluded from serialization and repr.
    value: str | None = Field(default=None, exclude=True, repr=False)

    def safe_summary(self) -> str:
        """Return safe text for display or plan output without exposing secret values."""
        if not self.is_secret:
            return self.value if self.value is not None else "<not set>"
        if self.status == SecretStatus.CONFIGURED:
            return "<secret configured>"
        elif self.status == SecretStatus.REQUIRED_MISSING:
            return "<secret required (missing)>"
        else:
            return "<secret optional (missing)>"


class SecretResolutionSummary(BaseModel):
    """Metadata summary of secret resolution results safe for logging and persistence."""

    model_config = ConfigDict(extra="forbid")

    specs_evaluated: int = 0
    secrets_resolved: list[str] = Field(default_factory=list)
    missing_required: list[str] = Field(default_factory=list)
    missing_optional: list[str] = Field(default_factory=list)
    is_complete: bool = True
