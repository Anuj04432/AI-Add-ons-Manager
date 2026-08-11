"""Verification package for AI Add-ons Manager (Phase 5B.9)."""

from aiaddons.core.verification.engine import VerificationEngine, verify_path_security
from aiaddons.core.verification.models import (
    VerificationCheck,
    VerificationResult,
    VerificationStatus,
)

__all__ = [
    "VerificationCheck",
    "VerificationEngine",
    "VerificationResult",
    "VerificationStatus",
    "verify_path_security",
]
