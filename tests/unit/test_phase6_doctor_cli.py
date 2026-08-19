"""Unit tests for `aiaddons doctor` CLI command."""

import json
from pathlib import Path
from unittest.mock import patch

from typer.testing import CliRunner

from aiaddons.cli.exit_codes import ExitCode
from aiaddons.cli.main import app
from aiaddons.core.health.models import (
    HealthCategory,
    HealthCheckItem,
    HealthReport,
    HealthStatus,
)

runner = CliRunner()


def test_cli_doctor_pass_rich_output() -> None:
    """Verify `aiaddons doctor` formatted output on PASS."""
    mock_report = HealthReport(
        overall_status=HealthStatus.PASS,
        summary={"PASS": 3, "WARN": 0, "FAIL": 0, "SKIPPED": 0},
        items=[
            HealthCheckItem(
                check_id="check_reg",
                category=HealthCategory.REGISTRY,
                status=HealthStatus.PASS,
                message="Registry cache valid.",
            ),
            HealthCheckItem(
                check_id="check_state",
                category=HealthCategory.INSTALLED_STATE,
                status=HealthStatus.PASS,
                message="State database valid.",
            ),
            HealthCheckItem(
                check_id="check_agents",
                category=HealthCategory.AGENTS,
                status=HealthStatus.PASS,
                message="Claude Code detected.",
            ),
        ],
    )

    with patch("aiaddons.cli.commands.doctor.HealthCheckEngine") as mock_cls:
        mock_instance = mock_cls.return_value
        mock_instance.run_all_checks.return_value = mock_report

        result = runner.invoke(app, ["doctor"])

    assert result.exit_code == ExitCode.SUCCESS
    assert "AI Add-ons Doctor" in result.stdout
    assert "Registry" in result.stdout
    assert "Registry cache valid." in result.stdout
    assert "Overall Status: PASS" in result.stdout


def test_cli_doctor_warn_exit_code() -> None:
    """Verify `aiaddons doctor` with warnings exits with ExitCode.WARNINGS_DETECTED."""
    mock_report = HealthReport(
        overall_status=HealthStatus.WARN,
        summary={"PASS": 2, "WARN": 1, "FAIL": 0, "SKIPPED": 0},
        items=[
            HealthCheckItem(
                check_id="check_reg",
                category=HealthCategory.REGISTRY,
                status=HealthStatus.WARN,
                message="Registry cache is stale.",
                remediation="Run: aiaddons registry update",
            ),
            HealthCheckItem(
                check_id="check_runtimes",
                category=HealthCategory.RUNTIMES,
                status=HealthStatus.PASS,
                message="python 3.12 available.",
            ),
        ],
    )

    with patch("aiaddons.cli.commands.doctor.HealthCheckEngine") as mock_cls:
        mock_instance = mock_cls.return_value
        mock_instance.run_all_checks.return_value = mock_report

        result = runner.invoke(app, ["doctor"])

    assert result.exit_code == ExitCode.WARNINGS_DETECTED
    assert "Overall Status: WARN" in result.stdout
    assert "Run: aiaddons registry update" in result.stdout


def test_cli_doctor_fail_exit_code() -> None:
    """Verify `aiaddons doctor` with failures exits with ExitCode.HEALTH_CHECK_FAILURE."""
    mock_report = HealthReport(
        overall_status=HealthStatus.FAIL,
        summary={"PASS": 1, "WARN": 0, "FAIL": 1, "SKIPPED": 0},
        items=[
            HealthCheckItem(
                check_id="check_reg",
                category=HealthCategory.REGISTRY,
                status=HealthStatus.FAIL,
                message="Registry cache is corrupted.",
                remediation="Run: aiaddons registry update",
            ),
        ],
    )

    with patch("aiaddons.cli.commands.doctor.HealthCheckEngine") as mock_cls:
        mock_instance = mock_cls.return_value
        mock_instance.run_all_checks.return_value = mock_report

        result = runner.invoke(app, ["doctor"])

    assert result.exit_code == ExitCode.HEALTH_CHECK_FAILURE
    assert "Overall Status: FAIL" in result.stdout
    assert "Registry cache is corrupted." in result.stdout


def test_cli_doctor_json_output() -> None:
    """Verify `aiaddons doctor --json` outputs structured JSON."""
    mock_report = HealthReport(
        overall_status=HealthStatus.PASS,
        summary={"PASS": 1, "WARN": 0, "FAIL": 0, "SKIPPED": 0},
        items=[
            HealthCheckItem(
                check_id="check_reg",
                category=HealthCategory.REGISTRY,
                status=HealthStatus.PASS,
                message="Registry cache valid.",
                diagnostic_details={"cache_size": 2048},
            ),
        ],
    )

    with patch("aiaddons.cli.commands.doctor.HealthCheckEngine") as mock_cls:
        mock_instance = mock_cls.return_value
        mock_instance.run_all_checks.return_value = mock_report

        result = runner.invoke(app, ["doctor", "--json"])

    assert result.exit_code == ExitCode.SUCCESS
    parsed = json.loads(result.stdout)
    assert parsed["overall_status"] == "PASS"
    assert parsed["summary"]["PASS"] == 1
    assert len(parsed["items"]) == 1
    assert parsed["items"][0]["check_id"] == "check_reg"
    assert parsed["items"][0]["diagnostic_details"]["cache_size"] == 2048


def test_cli_doctor_json_no_secrets() -> None:
    """Verify `aiaddons doctor --json` contains no secret tokens or leaked keys."""
    mock_report = HealthReport(
        overall_status=HealthStatus.PASS,
        summary={"PASS": 1, "WARN": 0, "FAIL": 0, "SKIPPED": 0},
        items=[
            HealthCheckItem(
                check_id="check_sec",
                category=HealthCategory.SECURITY,
                status=HealthStatus.PASS,
                message="No plaintext secrets detected.",
                diagnostic_details={"env_var": "API_KEY"},
            ),
        ],
    )

    with patch("aiaddons.cli.commands.doctor.HealthCheckEngine") as mock_cls:
        mock_instance = mock_cls.return_value
        mock_instance.run_all_checks.return_value = mock_report

        result = runner.invoke(app, ["doctor", "-j"])

    assert result.exit_code == ExitCode.SUCCESS
    raw_output = result.stdout
    assert "sk-" not in raw_output
    assert "ghp_" not in raw_output


def test_cli_doctor_operational_error() -> None:
    """Verify unexpected operational exceptions exit with ExitCode.OPERATIONAL_ERROR."""
    with patch("aiaddons.cli.commands.doctor.HealthCheckEngine") as mock_cls:
        mock_instance = mock_cls.return_value
        mock_instance.run_all_checks.side_effect = RuntimeError("Disk I/O failed.")

        result = runner.invoke(app, ["doctor"])

    assert result.exit_code == ExitCode.OPERATIONAL_ERROR
    assert "Operational Error" in result.stdout


def test_cli_doctor_read_only_guarantee(tmp_path: Path) -> None:
    """Verify CLI doctor command execution never mutates filesystem."""
    dummy_file = tmp_path / "sample.txt"
    dummy_file.write_text("sample content", encoding="utf-8")
    before_mtime = dummy_file.stat().st_mtime_ns

    result = runner.invoke(app, ["doctor", "--project-path", str(tmp_path)])
    # Result depends on host environment health status
    assert result.exit_code in (
        ExitCode.SUCCESS,
        ExitCode.WARNINGS_DETECTED,
        ExitCode.HEALTH_CHECK_FAILURE,
    )

    assert dummy_file.exists()
    assert dummy_file.read_text(encoding="utf-8") == "sample content"
    assert dummy_file.stat().st_mtime_ns == before_mtime
