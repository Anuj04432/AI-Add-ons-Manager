"""HealthScreen: Textual TUI screen for Phase 6 system diagnostics (aiaddons doctor)."""

from __future__ import annotations

from pathlib import Path

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical, VerticalScroll
from textual.screen import Screen
from textual.widgets import Button, Footer, Header, Label, ListItem, ListView, Markdown, Static

from aiaddons.core.health.engine import HealthCheckEngine
from aiaddons.core.health.models import (
    HealthCategory,
    HealthReport,
    HealthStatus,
)


class HealthScreen(Screen[None]):
    """Interactive diagnostic screen displaying system health checks across all 9 categories."""

    BINDINGS = [
        Binding("escape", "back", "Back to Browser"),
        Binding("q", "back", "Close"),
        Binding("r", "refresh_health", "Re-run Checks"),
    ]

    CSS = """
    HealthScreen {
        layout: vertical;
        background: $surface;
    }

    #health-main-container {
        layout: horizontal;
        height: 1fr;
    }

    #health-sidebar {
        width: 35%;
        height: 100%;
        border-right: heavy $primary;
        padding: 1;
    }

    #health-detail-panel {
        width: 65%;
        height: 100%;
        padding: 1;
    }

    .section-title {
        text-style: bold;
        color: $accent;
        margin-bottom: 1;
    }

    #health-summary-badge {
        margin-bottom: 1;
        padding: 1;
        background: $boost;
        border: solid $primary;
    }

    #category-list {
        height: 1fr;
        border: solid $primary;
    }

    #health-action-bar {
        height: 3;
        width: 100%;
        padding: 0 1;
        border-top: solid $primary;
        background: $surface;
        align: right middle;
    }

    Button {
        margin-left: 1;
    }
    """

    def __init__(
        self,
        workspace_dir: Path | None = None,
        store_dir: Path | None = None,
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        super().__init__(name=name, id=id, classes=classes)
        self.workspace_dir = workspace_dir or Path.cwd()
        self.store_dir = store_dir or (Path.home() / ".aiaddons")
        self.report: HealthReport | None = None
        self.selected_category: HealthCategory = HealthCategory.REGISTRY

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Container(id="health-main-container"):
            with Vertical(id="health-sidebar"):
                yield Label("System Diagnostics (Doctor)", classes="section-title")
                yield Static(id="health-summary-badge", content="Running diagnostic checks...")
                yield Label("Diagnostic Categories", classes="section-title")
                yield ListView(id="category-list")
            with VerticalScroll(id="health-detail-panel"):
                yield Label("Category Diagnostics & Details", classes="section-title")
                yield Markdown(id="health-detail-view", markdown="Select a category to view diagnostic details.")
        with Horizontal(id="health-action-bar"):
            yield Button("Re-run Checks [R]", id="btn-health-refresh", variant="default")
            yield Button("Back to Main [Esc]", id="btn-health-back", variant="primary")
        yield Footer()

    def on_mount(self) -> None:
        """Execute health checks when screen is mounted."""
        self._run_diagnostics()

    def action_back(self) -> None:
        """Dismiss the health screen and return to main screen."""
        self.app.pop_screen()

    def action_refresh_health(self) -> None:
        """Trigger re-run of diagnostic checks."""
        self._run_diagnostics()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle button press events."""
        if event.button.id == "btn-health-refresh":
            self._run_diagnostics()
        elif event.button.id == "btn-health-back":
            self.action_back()

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        """Handle selection of a diagnostic category from the list."""
        if not event.item:
            return
        cat_str = getattr(event.item, "cat_value", None)
        if not cat_str and event.item.id:
            cat_str = event.item.id.replace("cat-", "")
        if not cat_str and event.list_view.index is not None:
            categories = list(HealthCategory)
            if 0 <= event.list_view.index < len(categories):
                cat_str = categories[event.list_view.index].value
        if cat_str:
            try:
                self.selected_category = HealthCategory(cat_str)
                self._update_detail_view()
            except ValueError:
                pass

    def _run_diagnostics(self) -> None:
        """Run all diagnostic checks via HealthCheckEngine and refresh the UI."""
        engine = HealthCheckEngine(
            store_dir=self.store_dir,
            workspace_dir=self.workspace_dir,
        )
        try:
            self.report = engine.run_all_checks()
        except Exception as exc:
            summary_widget = self.query_one("#health-summary-badge", Static)
            summary_widget.update(f"[bold red]Operational Error:[/bold red] {exc}")
            return

        self._update_summary_badge()
        self._populate_category_list()
        self._update_detail_view()

    def _update_summary_badge(self) -> None:
        if not self.report:
            return
        summary_widget = self.query_one("#health-summary-badge", Static)
        pass_cnt = self.report.summary.get(HealthStatus.PASS.value, 0)
        warn_cnt = self.report.summary.get(HealthStatus.WARN.value, 0)
        fail_cnt = self.report.summary.get(HealthStatus.FAIL.value, 0)
        counts_str = f"{pass_cnt} pass, {warn_cnt} warn, {fail_cnt} fail"

        if self.report.overall_status == HealthStatus.PASS:
            summary_widget.update(f"[bold green]Overall Status: PASS[/bold green]\n[dim]({counts_str})[/dim]")
        elif self.report.overall_status == HealthStatus.WARN:
            summary_widget.update(f"[bold yellow]Overall Status: WARN[/bold yellow]\n[dim]({counts_str})[/dim]")
        else:
            summary_widget.update(f"[bold red]Overall Status: FAIL[/bold red]\n[dim]({counts_str})[/dim]")

    def _populate_category_list(self) -> None:
        if not self.report:
            return
        category_list = self.query_one("#category-list", ListView)
        categories = list(HealthCategory)

        if len(category_list.children) == len(categories):
            for idx, cat in enumerate(categories):
                cat_items = self.report.get_items_by_category(cat)
                if any(it.status == HealthStatus.FAIL for it in cat_items):
                    status_tag = "[bold red]FAIL[/bold red]"
                elif any(it.status == HealthStatus.WARN for it in cat_items):
                    status_tag = "[bold yellow]WARN[/bold yellow]"
                else:
                    status_tag = "[bold green]PASS[/bold green]"

                label_text = f"{cat.display_name} ({status_tag})"
                item = category_list.children[idx]
                item.query_one(Label).update(label_text)
        else:
            category_list.clear()
            for cat in categories:
                cat_items = self.report.get_items_by_category(cat)
                if any(it.status == HealthStatus.FAIL for it in cat_items):
                    status_tag = "[bold red]FAIL[/bold red]"
                elif any(it.status == HealthStatus.WARN for it in cat_items):
                    status_tag = "[bold yellow]WARN[/bold yellow]"
                else:
                    status_tag = "[bold green]PASS[/bold green]"

                label_text = f"{cat.display_name} ({status_tag})"
                list_item = ListItem(Label(label_text))
                setattr(list_item, "cat_value", cat.value)
                category_list.append(list_item)

    def _update_detail_view(self) -> None:
        detail_view = self.query_one("#health-detail-view", Markdown)
        if not self.report:
            detail_view.update("No health report available.")
            return

        cat = self.selected_category
        items = self.report.get_items_by_category(cat)

        cat_fails = sum(1 for it in items if it.status == HealthStatus.FAIL)
        cat_warns = sum(1 for it in items if it.status == HealthStatus.WARN)
        cat_passes = sum(1 for it in items if it.status == HealthStatus.PASS)

        if cat_fails > 0:
            status_header = f"❌ **Category Status:** In danger ({cat_fails} failure(s))"
        elif cat_warns > 0:
            status_header = f"⚠️ **Category Status:** Warnings detected ({cat_warns} warning(s))"
        else:
            status_header = f"✅ **Category Status:** All checks passed ({cat_passes} passed)"

        md = f"## {cat.display_name} Diagnostics\n\n"
        md += f"{status_header}\n\n"
        md += "### Diagnostic Checks:\n\n"

        if not items:
            md += "*No checks recorded for this category.*\n"
        else:
            for item in items:
                if item.status == HealthStatus.PASS:
                    md += f"- **✓ PASS:** {item.message}\n"
                elif item.status == HealthStatus.WARN:
                    md += f"- **! WARN:** {item.message}\n"
                    if item.remediation:
                        md += f"  - 💡 **Remediation:** `{item.remediation}`\n"
                elif item.status == HealthStatus.FAIL:
                    md += f"- **✗ FAIL:** {item.message}\n"
                    if item.remediation:
                        md += f"  - 🚨 **Remediation:** `{item.remediation}`\n"
                elif item.status == HealthStatus.SKIPPED:
                    md += f"- **- SKIPPED:** {item.message}\n"

                if item.diagnostic_details:
                    details_str = ", ".join(f"`{k}`: {v}" for k, v in item.diagnostic_details.items())
                    md += f"  - *Details:* {details_str}\n"

        detail_view.update(md)
