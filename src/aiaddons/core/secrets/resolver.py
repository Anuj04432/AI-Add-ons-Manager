"""Secret resolution layer for process environment and interactive prompting."""

import getpass
import os
import sys
from collections.abc import Callable
from typing import Protocol

from aiaddons.core.exceptions import SecretResolutionError
from aiaddons.core.models.manifest import EnvVarSpec, IntegrationManifest
from aiaddons.core.secrets.models import (
    ResolvedSecret,
    SecretResolutionSummary,
    SecretStatus,
)


class InputProvider(Protocol):
    """Protocol for secure interactive secret input prompting."""

    def prompt_secret(self, env_name: str, description: str | None = None) -> str: ...


_original_getpass = getpass.getpass


def get_windows_clipboard_text() -> str | None:
    """Retrieve unicode text from Windows clipboard via Win32 API without external dependencies."""
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes
        import time

        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32

        user32.OpenClipboard.argtypes = [wintypes.HWND]
        user32.OpenClipboard.restype = wintypes.BOOL
        user32.CloseClipboard.argtypes = []
        user32.CloseClipboard.restype = wintypes.BOOL
        user32.GetClipboardData.argtypes = [wintypes.UINT]
        user32.GetClipboardData.restype = wintypes.HANDLE
        kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
        kernel32.GlobalLock.restype = wintypes.LPVOID
        kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
        kernel32.GlobalUnlock.restype = wintypes.BOOL

        cf_unicodetext = 13
        opened = False
        for _ in range(3):
            if user32.OpenClipboard(None):
                opened = True
                break
            time.sleep(0.01)

        if not opened:
            return None

        try:
            h_data = user32.GetClipboardData(cf_unicodetext)
            if not h_data:
                return None
            p_text = kernel32.GlobalLock(h_data)
            if not p_text:
                return None
            try:
                return str(ctypes.wstring_at(p_text))
            finally:
                kernel32.GlobalUnlock(h_data)
        finally:
            user32.CloseClipboard()
    except Exception:
        return None


def win_getpass_with_paste(prompt: str = "Password: ") -> str:
    """Prompt for secret with echo off on Windows, supporting Ctrl+V clipboard pasting."""
    import msvcrt

    stdin = sys.stdin
    orig = sys.__stdin__
    if stdin is None or orig is None or stdin is not orig:
        return getpass.getpass(prompt)
    if not stdin.isatty():
        return getpass.getpass(prompt)

    for c in prompt:
        msvcrt.putwch(c)

    pw = ""
    while True:
        c = msvcrt.getwch()
        if c in ("\r", "\n"):
            break
        if c == "\003":  # Ctrl+C
            raise KeyboardInterrupt
        if c in ("\004", "\032"):  # Ctrl+D or Ctrl+Z (EOF)
            if not pw:
                raise EOFError
            break
        if c == "\b":  # Backspace
            pw = pw[:-1]
        elif c == "\x16":  # Ctrl+V (paste from clipboard)
            clip = get_windows_clipboard_text()
            if clip:
                pw += clip.rstrip("\r\n")
        elif c in ("\x00", "\xe0"):  # Extended key prefix (arrows, insert, delete, F-keys)
            try:
                _ = msvcrt.getwch()
            except Exception:
                pass
        elif ord(c) >= 32:
            pw += c

    msvcrt.putwch("\r")
    msvcrt.putwch("\n")
    return pw


def secure_prompt(prompt_text: str) -> str:
    """Prompt user securely without echoing input to terminal output.

    On Windows interactive consoles, supports Ctrl+V clipboard pasting while
    preserving no-echo masking. Delegates to getpass.getpass when mocked or on
    non-Windows/non-TTY platforms.
    """
    if getpass.getpass is not _original_getpass:
        return getpass.getpass(prompt_text)

    stdin = sys.stdin
    orig = sys.__stdin__
    if (
        sys.platform == "win32"
        and stdin is not None
        and orig is not None
        and stdin is orig
        and stdin.isatty()
    ):
        try:
            return win_getpass_with_paste(prompt_text)
        except Exception:
            return getpass.getpass(prompt_text)

    return getpass.getpass(prompt_text)


class DefaultTTYInputProvider:
    """Default interactive prompt provider with Windows paste support and no-echo input."""

    def __init__(self, prompt_func: Callable[[str], str] | None = None) -> None:
        self._prompt_func = prompt_func

    def prompt_secret(self, env_name: str, description: str | None = None) -> str:
        """Prompt user securely without echoing input to terminal output."""
        prompt_lines = ["Secret required:", f"  {env_name}"]
        if description:
            prompt_lines.append(f"  ({description})")
        prompt_lines.append(f"  (Tip: Alternatively, pre-set $env:{env_name}=\"value\" before running)")
        prompt_lines.append("\nEnter value: ")
        prompt_text = "\n".join(prompt_lines)

        if self._prompt_func is not None:
            return self._prompt_func(prompt_text)

        return secure_prompt(prompt_text)


class SecretResolver:
    """Resolver for managing secret and environment variable requirements."""

    def __init__(
        self,
        input_provider: InputProvider | None = None,
        custom_env: dict[str, str] | None = None,
    ) -> None:
        self.input_provider = input_provider or DefaultTTYInputProvider()
        self.custom_env = custom_env

    def resolve_spec(
        self,
        spec: EnvVarSpec,
        allow_interactive: bool = True,
        override_env: dict[str, str] | None = None,
    ) -> ResolvedSecret:
        """Resolve a single EnvVarSpec against environment or interactive prompt."""
        # 1. Lookup in override_env, custom_env, or os.environ
        env_sources = [override_env, self.custom_env, os.environ]
        val: str | None = None
        for source in env_sources:
            if source is not None and spec.name in source:
                candidate = source[spec.name]
                if candidate:
                    val = candidate
                    break

        if val is not None:
            status = SecretStatus.CONFIGURED if spec.secret else SecretStatus.NOT_SECRET
            return ResolvedSecret(
                name=spec.name,
                is_secret=spec.secret,
                is_required=spec.required,
                description=spec.description,
                status=status,
                value=val,
            )

        # 2. Variable missing or empty
        if spec.required:
            if allow_interactive:
                can_prompt = True
                if isinstance(self.input_provider, DefaultTTYInputProvider):
                    if self.input_provider._prompt_func is None and not sys.stdin.isatty():
                        can_prompt = False

                if can_prompt:
                    try:
                        prompted_val = self.input_provider.prompt_secret(
                            spec.name, spec.description
                        )
                        if prompted_val and prompted_val.strip():
                            status = (
                                SecretStatus.CONFIGURED if spec.secret else SecretStatus.NOT_SECRET
                            )
                            return ResolvedSecret(
                                name=spec.name,
                                is_secret=spec.secret,
                                is_required=spec.required,
                                description=spec.description,
                                status=status,
                                value=prompted_val.strip(),
                            )
                    except SecretResolutionError:
                        raise
                    except Exception as err:
                        msg = f"Failed to prompt for secret '{spec.name}': {err}"
                        raise SecretResolutionError(msg) from err

            if allow_interactive:
                msg = f"Missing required environment variable '{spec.name}'."
            else:
                msg = (
                    f"Missing required environment variable '{spec.name}'. "
                    "Non-interactive mode cannot prompt for secrets."
                )
            raise SecretResolutionError(msg)

        # 3. Optional variable missing
        return ResolvedSecret(
            name=spec.name,
            is_secret=spec.secret,
            is_required=spec.required,
            description=spec.description,
            status=SecretStatus.OPTIONAL_MISSING,
            value=None,
        )

    def resolve_specs(
        self,
        specs: list[EnvVarSpec],
        allow_interactive: bool = True,
        override_env: dict[str, str] | None = None,
    ) -> list[ResolvedSecret]:
        """Resolve a list of EnvVarSpecs."""
        resolved: list[ResolvedSecret] = []
        for spec in specs:
            res = self.resolve_spec(
                spec, allow_interactive=allow_interactive, override_env=override_env
            )
            resolved.append(res)
        return resolved

    def resolve_manifest(
        self,
        manifest: IntegrationManifest,
        allow_interactive: bool = True,
        override_env: dict[str, str] | None = None,
    ) -> list[ResolvedSecret]:
        """Extract and resolve all EnvVarSpecs declared in an IntegrationManifest."""
        specs: list[EnvVarSpec] = []
        if manifest.handler_spec.mcp and manifest.handler_spec.mcp.env_vars:
            specs.extend(manifest.handler_spec.mcp.env_vars)
        return self.resolve_specs(
            specs, allow_interactive=allow_interactive, override_env=override_env
        )

    def summarize(self, resolved_secrets: list[ResolvedSecret]) -> SecretResolutionSummary:
        """Generate a safe, non-sensitive summary of resolution results."""
        resolved_names: list[str] = []
        missing_req: list[str] = []
        missing_opt: list[str] = []

        for r in resolved_secrets:
            if r.status in (SecretStatus.CONFIGURED, SecretStatus.NOT_SECRET):
                resolved_names.append(r.name)
            elif r.status == SecretStatus.REQUIRED_MISSING:
                missing_req.append(r.name)
            elif r.status == SecretStatus.OPTIONAL_MISSING:
                missing_opt.append(r.name)

        return SecretResolutionSummary(
            specs_evaluated=len(resolved_secrets),
            secrets_resolved=resolved_names,
            missing_required=missing_req,
            missing_optional=missing_opt,
            is_complete=len(missing_req) == 0,
        )
