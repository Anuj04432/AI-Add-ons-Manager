"""Secret resolution and isolation models and components for Phase 5B.8."""

from aiaddons.core.secrets.models import (
    ResolvedSecret,
    SecretResolutionSummary,
    SecretStatus,
)
from aiaddons.core.secrets.resolver import (
    DefaultTTYInputProvider,
    InputProvider,
    SecretResolver,
    get_windows_clipboard_text,
    secure_prompt,
    win_getpass_with_paste,
)

__all__ = [
    "DefaultTTYInputProvider",
    "InputProvider",
    "ResolvedSecret",
    "SecretResolutionSummary",
    "SecretResolver",
    "SecretStatus",
    "get_windows_clipboard_text",
    "secure_prompt",
    "win_getpass_with_paste",
]
