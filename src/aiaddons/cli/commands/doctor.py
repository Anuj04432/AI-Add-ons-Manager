"""CLI command for Phase 6 system health check and diagnostics (aiaddons doctor)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import typer
from rich.console import Console

from aiaddons.cli.exit_codes import ExitCode
from aiaddons.core.health.engine import HealthCheckEngine
from aiaddons.core.health.models import HealthCategory, HealthReport, HealthStatus

console = Console()


def _get_symbols() -> tuple[str, str, str]:
    """Return platform and encoding safe status symbols."""
    try:
        "✓".encode(sys.stdout.encoding or "utf-8")
        "→".encode(sys.stdout.encoding or "utf-8")
        return "✓", "✗", "→"
    except (UnicodeEncodeError, AttributeError, TypeError, Exception):
        return "+", "x", "->"


def _get_exit_code(status: HealthStatus) -> int:
    """Map health status to standardized CLI exit code."""
    if status == HealthStatus.PASS:
        return ExitCode.SUCCESS
    if status == HealthStatus.WARN:
        return ExitCode.WARNINGS_DETECTED
    return ExitCode.HEALTH_CHECK_FAILURE


def doctor_command(
    json_output: bool = typer.Option(
        False,
        "--json",
        "-j",
        help="Output health check results in structured JSON format.",
    ),
    project_path: Path | None = typer.Option(
        None,
        "--project-path",
        "-p",
        help="Specify custom project/workspace directory for health checks.",
    ),
) -> None:
    """Run comprehensive diagnostic health checks across all subsystems."""
    engine = HealthCheckEngine(workspace_dir=project_path)

    try:
        report: HealthReport = engine.run_all_checks()
    except Exception as exc:
        if json_output:
            err_json = {
                "overall_status": HealthStatus.FAIL.value,
                "error": str(exc),
                "summary": {"FAIL": 1},
                "items": [],
            }
            console.print(json.dumps(err_json, indent=2))
        else:
            console.print(f"\n[bold red]Operational Error during health check:[/bold red] {exc}\n")
        raise typer.Exit(code=ExitCode.OPERATIONAL_ERROR) from exc

    exit_code = _get_exit_code(report.overall_status)

    if json_output:
        console.print(report.model_dump_json(indent=2))
        raise typer.Exit(code=exit_code)

    # Rich formatted human-readable output
    check_sym, cross_sym, arrow_sym = _get_symbols()

    console.print("\n[bold cyan]AI Add-ons Doctor[/bold cyan]")
    console.print("[dim]-----------------[/dim]\n")

    for cat in HealthCategory:
        items = report.get_items_by_category(cat)
        if not items:
            continue

        console.print(f"[bold]{cat.display_name}[/bold]")
        for item in items:
            if item.status == HealthStatus.PASS:
                console.print(f"  [bold green]{check_sym}[/bold green] {item.message}")
            elif item.status == HealthStatus.WARN:
                console.print(f"  [bold yellow]![/bold yellow] {item.message}")
                if item.remediation:
                    console.print(f"  [dim]  {arrow_sym}[/dim] [cyan]{item.remediation}[/cyan]")
            elif item.status == HealthStatus.FAIL:
                console.print(f"  [bold red]{cross_sym}[/bold red] {item.message}")
                if item.remediation:
                    console.print(f"  [dim]  {arrow_sym}[/dim] [cyan]{item.remediation}[/cyan]")
            elif item.status == HealthStatus.SKIPPED:
                console.print(f"  [dim]- {item.message}[/dim]")
        console.print()

    # Summary footer
    pass_cnt = report.summary.get(HealthStatus.PASS.value, 0)
    warn_cnt = report.summary.get(HealthStatus.WARN.value, 0)
    fail_cnt = report.summary.get(HealthStatus.FAIL.value, 0)
    counts_str = f"({pass_cnt} passed, {warn_cnt} warning(s), {fail_cnt} failure(s))"

    if report.overall_status == HealthStatus.PASS:
        console.print(f"[bold green]Overall Status: PASS[/bold green] [dim]{counts_str}[/dim]\n")
    elif report.overall_status == HealthStatus.WARN:
        console.print(f"[bold yellow]Overall Status: WARN[/bold yellow] [dim]{counts_str}[/dim]\n")
    else:
        console.print(f"[bold red]Overall Status: FAIL[/bold red] [dim]{counts_str}[/dim]\n")

    raise typer.Exit(code=exit_code)
