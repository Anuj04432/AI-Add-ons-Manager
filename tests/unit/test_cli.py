"""Unit tests for the CLI module of aiaddons."""

from typer.testing import CliRunner

from aiaddons import __version__
from aiaddons.cli.main import app

runner = CliRunner()


def test_package_version() -> None:
    """Test package version variable."""
    assert __version__ == "0.1.0"


def test_cli_version_flag() -> None:
    """Test `aiaddons --version` flag."""
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert "aiaddons version 0.1.0" in result.stdout


def test_cli_version_short_flag() -> None:
    """Test `aiaddons -v` short flag."""
    result = runner.invoke(app, ["-v"])
    assert result.exit_code == 0
    assert "aiaddons version 0.1.0" in result.stdout


def test_cli_version_subcommand() -> None:
    """Test `aiaddons version` subcommand."""
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert "aiaddons version 0.1.0" in result.stdout


def test_cli_main_no_args() -> None:
    """Test running `aiaddons` with no arguments."""
    result = runner.invoke(app, [])
    assert result.exit_code == 0
    assert "AI Add-ons Manager (`aiaddons`)" in result.stdout
