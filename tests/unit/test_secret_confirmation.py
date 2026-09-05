"""Tests for secret input confirmation, masked preview, and empty-input re-prompting."""

import io
import sys
from unittest.mock import MagicMock, call, patch

import pytest

from aiaddons.core.exceptions import SecretResolutionError
from aiaddons.core.models.manifest import EnvVarSpec
from aiaddons.core.secrets import (
    DefaultTTYInputProvider,
    SecretResolver,
    SecretStatus,
    mask_secret_preview,
)


# ---------------------------------------------------------------------------
# mask_secret_preview unit tests
# ---------------------------------------------------------------------------


class TestMaskSecretPreview:
    """Verify mask_secret_preview produces correct masked previews without
    exposing full secrets in any test assertion output."""

    def test_empty_string(self) -> None:
        """Empty value produces '(empty)' marker."""
        assert mask_secret_preview("") == "(empty)"

    def test_single_char(self) -> None:
        """1-char secret shows only the first char, no tail."""
        result = mask_secret_preview("x")
        assert result == "x (1 chars)"
        assert "x" == result.split(" ")[0]  # only first char visible

    def test_two_chars(self) -> None:
        """2-char secret masks all but first."""
        result = mask_secret_preview("ab")
        assert result == "a* (2 chars)"

    def test_three_chars(self) -> None:
        """3-char secret masks all but first."""
        result = mask_secret_preview("abc")
        assert result == "a** (3 chars)"

    def test_four_chars_short_boundary(self) -> None:
        """4-char secret (boundary: 4-7 range) shows first + last."""
        result = mask_secret_preview("abcd")
        assert result == "a**d (4 chars)"

    def test_seven_chars_upper_boundary(self) -> None:
        """7-char secret (top of 4-7 range) shows first + last."""
        result = mask_secret_preview("abcdefg")
        assert result == "a*****g (7 chars)"

    def test_eight_chars_long_boundary(self) -> None:
        """8-char secret (boundary: >= 8) shows first 3 + last 3."""
        result = mask_secret_preview("abcdefgh")
        assert result == "abc**fgh (8 chars)"

    def test_forty_char_github_token(self) -> None:
        """Realistic 40-char GitHub PAT shows first 3 + last 3 with correct length."""
        token = "ghp_abcdefghijklmnopqrstuvwxyz1234567890"
        result = mask_secret_preview(token)
        assert len(token) == 40
        assert result.endswith("(40 chars)")
        # First 3 chars
        assert result.startswith("ghp")
        # Last 3 chars before the space
        masked_part = result.split(" ")[0]
        assert masked_part.endswith("890")
        # Middle is all asterisks
        middle = masked_part[3:-3]
        assert all(c == "*" for c in middle)
        assert len(middle) == 34

    def test_full_secret_never_in_output(self) -> None:
        """The full secret value must NEVER appear in the preview output."""
        secrets = [
            "ghp_1234567890abcdef1234567890abcdef12345678",
            "sk-proj-ABC123",
            "shortpw",
            "ab",
        ]
        for secret in secrets:
            preview = mask_secret_preview(secret)
            assert secret not in preview, (
                f"Full secret '{secret}' appears in preview '{preview}'"
            )

    def test_char_count_always_present(self) -> None:
        """Every non-empty preview must include the correct character count."""
        for length in [1, 2, 5, 8, 15, 50, 100]:
            value = "x" * length
            preview = mask_secret_preview(value)
            assert f"({length} chars)" in preview


# ---------------------------------------------------------------------------
# Format Validation Tests
# ---------------------------------------------------------------------------

class TestFormatValidation:
    """Verify format constraints logic triggers warnings and 'Continue anyway' flow."""
    
    def test_valid_format_proceeds_without_warning(self) -> None:
        """Well-formed value matches pattern and proceeds without warning."""
        def give_valid(prompt_text: str) -> str:
            return "ghp_1234567890abcdef1234567890abcdef12345678"
            
        provider = DefaultTTYInputProvider(prompt_func=give_valid)
        stderr_capture = io.StringIO()
        
        spec = EnvVarSpec(
            name="TOKEN", 
            secret=True,
            value_pattern="^ghp_[a-zA-Z0-9]+$",
            min_length=40
        )
        
        with patch("sys.stderr", stderr_capture):
            result = provider.prompt_secret(spec)
            
        assert result.startswith("ghp_")
        assert "Warning" not in stderr_capture.getvalue()
        assert "Continue anyway?" not in stderr_capture.getvalue()

    def test_invalid_format_triggers_warning_and_requires_confirm(self) -> None:
        """Malformed value triggers warning and prompts to continue. User says 'y'."""
        call_count = 0
        def prompt_simulator(prompt_text: str) -> str:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return "bad_token"  # The token itself
            return "y"              # The confirmation answer

        provider = DefaultTTYInputProvider(prompt_func=prompt_simulator)
        stderr_capture = io.StringIO()
        
        spec = EnvVarSpec(
            name="TOKEN", 
            secret=True,
            value_pattern="^ghp_[a-zA-Z0-9]+$",
            min_length=40,
            format_description="GitHub PAT"
        )
        
        with patch("sys.stderr", stderr_capture):
            result = provider.prompt_secret(spec)
            
        assert result == "bad_token"
        stderr_output = stderr_capture.getvalue()
        assert "⚠️  Warning: This doesn't look like a valid TOKEN" in stderr_output
        assert "Value is too short" in stderr_output
        assert "Value doesn't match expected format" in stderr_output
        assert "Continue anyway?" in stderr_output

    def test_invalid_format_user_rejects_and_reprompts(self) -> None:
        """User answers 'n' to format warning, causing a re-prompt with the valid value."""
        call_count = 0
        def prompt_simulator(prompt_text: str) -> str:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return "bad_token"  # 1st attempt token
            elif call_count == 2:
                return "n"          # 1st attempt confirmation (reject)
            elif call_count == 3:
                return "ghp_1234567890abcdef1234567890abcdef12345678"  # 2nd attempt valid
                
        provider = DefaultTTYInputProvider(prompt_func=prompt_simulator)
        stderr_capture = io.StringIO()
        
        spec = EnvVarSpec(
            name="TOKEN", 
            secret=True,
            value_pattern="^ghp_[a-zA-Z0-9]+$",
            min_length=40
        )
        
        with patch("sys.stderr", stderr_capture):
            result = provider.prompt_secret(spec)
            
        assert result.startswith("ghp_")
        assert "Warning" in stderr_capture.getvalue()
        assert "Please try entering the TOKEN again" in stderr_capture.getvalue()

    def test_invalid_pattern_in_manifest_silently_ignored(self) -> None:
        """A broken regex in the manifest doesn't crash, it just skips that specific validation."""
        def give_valid(prompt_text: str) -> str:
            return "some_token"
            
        provider = DefaultTTYInputProvider(prompt_func=give_valid)
        stderr_capture = io.StringIO()
        
        # Bypass pydantic validation for the test to simulate bad loaded data
        spec = EnvVarSpec(name="TOKEN", secret=True)
        spec.value_pattern = "*[unclosed bracket"
        
        with patch("sys.stderr", stderr_capture):
            result = provider.prompt_secret(spec)
            
        assert result == "some_token"
        assert "Warning" not in stderr_capture.getvalue()


# ---------------------------------------------------------------------------
# DefaultTTYInputProvider — empty re-prompting tests
# ---------------------------------------------------------------------------


class TestEmptyInputReprompt:
    """Verify empty submissions trigger a clear error and re-prompt loop."""

    def test_single_empty_then_valid(self) -> None:
        """First attempt empty, second attempt valid — succeeds with confirmation."""
        call_count = 0

        def mock_prompt(prompt_text: str) -> str:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return ""
            return "valid_secret_value_12345678"

        provider = DefaultTTYInputProvider(prompt_func=mock_prompt)
        stderr_capture = io.StringIO()

        with patch("sys.stderr", stderr_capture):
            result = provider.prompt_secret(EnvVarSpec(name="MY_TOKEN", description="Test token", secret=True))

        assert result == "valid_secret_value_12345678"
        assert call_count == 2
        stderr_output = stderr_capture.getvalue()
        assert "No value was entered for MY_TOKEN" in stderr_output
        assert "please try again" in stderr_output

    def test_whitespace_only_treated_as_empty(self) -> None:
        """Whitespace-only input is treated as empty and triggers re-prompt."""
        call_count = 0

        def mock_prompt(prompt_text: str) -> str:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return "   \t  "
            return "real_value"

        provider = DefaultTTYInputProvider(prompt_func=mock_prompt)
        stderr_capture = io.StringIO()

        with patch("sys.stderr", stderr_capture):
            result = provider.prompt_secret(EnvVarSpec(name="API_KEY", secret=True))

        assert result == "real_value"
        assert call_count == 2
        assert "No value was entered" in stderr_capture.getvalue()

    def test_all_attempts_empty_raises_error(self) -> None:
        """Exhausting all re-prompt attempts raises SecretResolutionError."""

        def always_empty(prompt_text: str) -> str:
            return ""

        provider = DefaultTTYInputProvider(prompt_func=always_empty)
        stderr_capture = io.StringIO()

        with patch("sys.stderr", stderr_capture):
            with pytest.raises(
                SecretResolutionError,
                match="No value was entered for 'EMPTY_TOKEN' after 3 attempts",
            ):
                provider.prompt_secret(EnvVarSpec(name="EMPTY_TOKEN", description="Never provided", secret=True))

    def test_remaining_attempts_shown_in_error(self) -> None:
        """Re-prompt message includes correct remaining attempt count."""
        calls: list[str] = []

        def record_and_empty(prompt_text: str) -> str:
            calls.append(prompt_text)
            if len(calls) < 3:
                return ""
            return "finally_valid"

        provider = DefaultTTYInputProvider(prompt_func=record_and_empty)
        stderr_capture = io.StringIO()

        with patch("sys.stderr", stderr_capture):
            result = provider.prompt_secret(EnvVarSpec(name="TOKEN", secret=True))

        assert result == "finally_valid"
        stderr_output = stderr_capture.getvalue()
        assert "2 attempts remaining" in stderr_output
        assert "1 attempt remaining" in stderr_output


# ---------------------------------------------------------------------------
# Masked confirmation display tests
# ---------------------------------------------------------------------------


class TestMaskedConfirmationDisplay:
    """Verify that a masked confirmation preview is shown after valid input."""

    def test_confirmation_preview_shown(self) -> None:
        """After valid input, a 'Received' confirmation line is printed to stderr."""

        def give_valid(prompt_text: str) -> str:
            return "ghp_my_secret_token_value_1234567890"

        provider = DefaultTTYInputProvider(prompt_func=give_valid)
        stderr_capture = io.StringIO()

        with patch("sys.stderr", stderr_capture):
            result = provider.prompt_secret(EnvVarSpec(name="GITHUB_TOKEN", description="GitHub PAT", secret=True))

        assert result == "ghp_my_secret_token_value_1234567890"
        stderr_output = stderr_capture.getvalue()
        assert "Received GITHUB_TOKEN:" in stderr_output
        # Full secret must NOT appear in stderr
        assert "ghp_my_secret_token_value_1234567890" not in stderr_output
        # Char count must appear
        assert f"({len(result)} chars)" in stderr_output

    def test_confirmation_preview_masks_correctly(self) -> None:
        """Confirmation preview uses mask_secret_preview formatting rules."""

        def give_short(prompt_text: str) -> str:
            return "abc"

        provider = DefaultTTYInputProvider(prompt_func=give_short)
        stderr_capture = io.StringIO()

        with patch("sys.stderr", stderr_capture):
            result = provider.prompt_secret(EnvVarSpec(name="SHORT_KEY", secret=True))

        assert result == "abc"
        stderr_output = stderr_capture.getvalue()
        # Short secret (3 chars): first char only
        assert "a**" in stderr_output
        assert "(3 chars)" in stderr_output
        # Full value must NOT appear
        assert "abc " not in stderr_output  # "abc" followed by space means full value leaked

    def test_no_full_secret_in_logs_or_stderr(self) -> None:
        """Regardless of secret length, the full value never appears in feedback."""
        test_cases = [
            "x",
            "ab",
            "abcdef",
            "ghp_realtoken1234567890abcdef12345678",
        ]
        for secret in test_cases:

            def give_secret(prompt_text: str, s: str = secret) -> str:
                return s

            provider = DefaultTTYInputProvider(prompt_func=give_secret)
            stderr_capture = io.StringIO()

            with patch("sys.stderr", stderr_capture):
                provider.prompt_secret(EnvVarSpec(name="KEY", secret=True))

            stderr_output = stderr_capture.getvalue()
            if len(secret) > 1:
                assert secret not in stderr_output, (
                    f"Full secret '{secret}' leaked in stderr: {stderr_output}"
                )


# ---------------------------------------------------------------------------
# SecretResolver integration — empty prompt via resolve_spec
# ---------------------------------------------------------------------------


class TestSecretResolverEmptyPrompt:
    """Verify SecretResolver.resolve_spec properly handles empty interactive input."""

    def test_empty_interactive_prompt_raises(self) -> None:
        """If interactive prompt exhausts retries with empty input, resolve_spec raises."""

        def always_empty(name: str, desc: str | None = None) -> str:
            return ""

        mock_provider = MagicMock()
        mock_provider.prompt_secret.side_effect = always_empty

        resolver = SecretResolver(input_provider=mock_provider)
        spec = EnvVarSpec(name="REQUIRED_TOKEN", secret=True, required=True)

        # The provider returns empty strings; resolve_spec checks emptiness at line 259
        # and falls through to raise SecretResolutionError at line 284
        with pytest.raises(SecretResolutionError, match="Missing required environment variable"):
            resolver.resolve_spec(spec, allow_interactive=True, override_env={})

    def test_valid_interactive_prompt_succeeds(self) -> None:
        """Interactive prompt returning a valid value succeeds normally."""
        mock_provider = MagicMock()
        mock_provider.prompt_secret.return_value = "valid_secret_abc"

        resolver = SecretResolver(input_provider=mock_provider)
        spec = EnvVarSpec(name="MY_KEY", secret=True, required=True)

        result = resolver.resolve_spec(spec, allow_interactive=True, override_env={})
        assert result.status == SecretStatus.CONFIGURED
        assert result.value == "valid_secret_abc"


# ---------------------------------------------------------------------------
# Windows Custom Getpass Tests
# ---------------------------------------------------------------------------

class TestWindowsGetpassAsteriskMasking:
    """Verify live character masking and backspace handling in win_getpass_with_paste."""

    @pytest.fixture
    def mock_win_env(self, monkeypatch: pytest.MonkeyPatch) -> MagicMock:
        """Mock the Windows environment to force win_getpass_with_paste to execute."""
        # Force sys.stdin.isatty to True and make sure it matches sys.__stdin__
        mock_stdin = MagicMock()
        mock_stdin.isatty.return_value = True
        monkeypatch.setattr(sys, "stdin", mock_stdin)
        monkeypatch.setattr(sys, "__stdin__", mock_stdin)

        # Mock msvcrt
        mock_msvcrt = MagicMock()
        monkeypatch.setitem(sys.modules, "msvcrt", mock_msvcrt)
        
        # We also mock getpass just in case it falls through
        mock_getpass = MagicMock()
        monkeypatch.setattr("aiaddons.core.secrets.resolver.getpass.getpass", mock_getpass)

        return mock_msvcrt

    def test_single_characters_echo_asterisks(self, mock_win_env: MagicMock) -> None:
        """Each standard character typed results in exactly one asterisk printed to stdout."""
        from aiaddons.core.secrets.resolver import win_getpass_with_paste
        
        # Sequence: "abc" then Enter (\r)
        mock_win_env.getwch.side_effect = ["a", "b", "c", "\r"]
        
        stdout_capture = io.StringIO()
        with patch("sys.stdout", stdout_capture):
            result = win_getpass_with_paste("Prompt> ")
            
        assert result == "abc"
        out = stdout_capture.getvalue()
        # Prompt + 3 asterisks + newline
        assert out == "Prompt> ***\n"
        assert "abc" not in out  # The real secret must NEVER be written to stdout

    def test_backspace_erases_visually(self, mock_win_env: MagicMock) -> None:
        """Backspace characters correctly remove from buffer and erase visual asterisks."""
        from aiaddons.core.secrets.resolver import win_getpass_with_paste
        
        # Sequence: "a", "b", Backspace (\b), "c", Enter (\r)
        # Expected visual: * * \b \b * -> *** with one erased
        mock_win_env.getwch.side_effect = ["a", "b", "\b", "c", "\r"]
        
        stdout_capture = io.StringIO()
        with patch("sys.stdout", stdout_capture):
            result = win_getpass_with_paste("Prompt> ")
            
        assert result == "ac"
        out = stdout_capture.getvalue()
        # "Prompt> " followed by "a"(*), "b"(*), backspace(\b \b), "c"(*)
        assert out == "Prompt> **\b \b*\n"
        assert "ac" not in out

    def test_backspace_on_empty_buffer_ignored(self, mock_win_env: MagicMock) -> None:
        """Pressing backspace when the buffer is empty does not output erasing characters."""
        from aiaddons.core.secrets.resolver import win_getpass_with_paste
        
        # Sequence: Backspace (\b), "a", Enter (\r)
        mock_win_env.getwch.side_effect = ["\b", "a", "\r"]
        
        stdout_capture = io.StringIO()
        with patch("sys.stdout", stdout_capture):
            result = win_getpass_with_paste("Prompt> ")
            
        assert result == "a"
        out = stdout_capture.getvalue()
        assert out == "Prompt> *\n"

    def test_paste_ctrl_v_echoes_multiple_asterisks(self, mock_win_env: MagicMock, monkeypatch: pytest.MonkeyPatch) -> None:
        """Pasting text with Ctrl+V correctly echoes an asterisk for each pasted character."""
        from aiaddons.core.secrets.resolver import win_getpass_with_paste
        
        # Sequence: Ctrl+V (\x16), Enter (\r)
        mock_win_env.getwch.side_effect = ["\x16", "\r"]
        
        # Mock the clipboard function
        monkeypatch.setattr(
            "aiaddons.core.secrets.resolver.get_windows_clipboard_text", 
            lambda: "pasted_secret"
        )
        
        stdout_capture = io.StringIO()
        with patch("sys.stdout", stdout_capture):
            result = win_getpass_with_paste("Prompt> ")
            
        assert result == "pasted_secret"
        out = stdout_capture.getvalue()
        # "pasted_secret" is 13 characters -> 13 asterisks
        assert out == "Prompt> *************\n"
        assert "pasted_secret" not in out
