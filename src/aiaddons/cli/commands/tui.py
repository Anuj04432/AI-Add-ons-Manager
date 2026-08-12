"""CLI command to launch the interactive Textual TUI interface."""

from pathlib import Path

import typer

from aiaddons.tui.app import AIAddonsTUIApp


def tui_command(
    registry_path: Path | None = typer.Option(
        None,
        "--registry",
        "-r",
        help="Path to local registry directory",
    ),
) -> None:
    """Launch the interactive terminal user interface (TUI)."""
    app = AIAddonsTUIApp(registry_dir=registry_path)
    app.run()
