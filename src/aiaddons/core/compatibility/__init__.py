"""Compatibility engine subpackage for evaluating add-on requirements against AI agents."""

from aiaddons.core.compatibility.engine import CompatibilityEngine
from aiaddons.core.compatibility.models import (
    CompatibilityResult,
    DependencyCheckResult,
    DependencyStatus,
)

__all__ = [
    "CompatibilityEngine",
    "CompatibilityResult",
    "DependencyCheckResult",
    "DependencyStatus",
]
