"""SyncScreen: Textual TUI screen for Phase 6E workspace lockfile synchronization."""

from __future__ import annotations

from pathlib import Path

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical, VerticalScroll
from textual.screen import Screen
from textual.widgets import Button, Checkbox, Footer, Header, Label, Markdown, Static

from aiaddons.agents.manager import AgentDetectionManager
from aiaddons.core.exceptions import LockfileNotFoundError
from aiaddons.core.models.agent import AgentCapability, AgentDetectionResult, Scope
from aiaddons.core.sync.engine import SyncEngine
from aiaddons.core.sync.models import SyncDiff, SyncPlan, SyncTargetSpec
from aiaddons.registry.registry import Registry
from aiaddons.state.lockfile import LockfileManager
from aiaddons.state.store import InstalledStateStore
from aiaddons.state.transaction import TransactionWALManager


class SyncScreen(Screen[None]):
    """Interactive synchronization screen for inspecting lockfile diffs and reconciling environments."""

    BINDINGS = [
        Binding("escape", "back", "Back to Browser"),
        Binding("q", "back", "Close"),
        Binding("r", "refresh_sync", "Refresh Diff"),
        Binding("x", "execute_sync", "Execute Sync"),
    ]

    CSS = """
    SyncScreen {
        layout: vertical;
        background: $surface;
    }

    #sync-main-container {
        layout: horizontal;
        height: 1fr;
    }

    #sync-sidebar {
        width: 38%;
        height: 100%;
        border-right: heavy $primary;
        padding: 1;
    }

    #sync-content-panel {
        width: 62%;
        height: 100%;
        padding: 1;
    }

    .section-title {
        text-style: bold;
        color: $accent;
        margin-bottom: 1;
    }

    #sync-status-badge {
        margin-bottom: 1;
        padding: 1;
        background: $boost;
        border: solid $primary;
    }

    #sync-options-box {
        margin-top: 1;
        margin-bottom: 1;
        padding: 1;
        border: solid $secondary;
    }

    #sync-action-bar {
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
        target_agent: AgentDetectionResult | None = None,
        scope: Scope = Scope.WORKSPACE,
        registry: Registry | None = None,
        store_dir: Path | None = None,
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        super().__init__(name=name, id=id, classes=classes)
        self.workspace_dir = (workspace_dir or Path.cwd()).resolve()
        self.target_agent = target_agent
        self.scope = scope
        self.registry = registry
        self.store_dir = (store_dir or (Path.home() / ".aiaddons")).resolve()
        self.expected_specs: list[SyncTargetSpec] = []
        self.diff: SyncDiff | None = None
        self.current_plan: SyncPlan | None = None
        self.prune_enabled: bool = False
        self.update_enabled: bool = False

        state_store = InstalledStateStore(store_dir=self.store_dir)
        lockfile_mgr = LockfileManager()
        wal_mgr = TransactionWALManager(transactions_dir=self.store_dir / "transactions")
        self.sync_engine = SyncEngine(
            state_store=state_store,
            lockfile_manager=lockfile_mgr,
            registry=self.registry,
            wal_manager=wal_mgr,
            workspace_dir=self.workspace_dir,
        )

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Container(id="sync-main-container"):
            with Vertical(id="sync-sidebar"):
                yield Label("Workspace Synchronization", classes="section-title")
                yield Static(id="sync-status-badge", content="Analyzing workspace...")
                with Vertical(id="sync-options-box"):
                    yield Label("Reconciliation Flags:", classes="section-title")
                    yield Checkbox(
                        "Prune unmanaged add-ons (--prune)",
                        value=self.prune_enabled,
                        id="cb-sync-prune",
                    )
                    yield Checkbox(
                        "Update mismatched versions (--update)",
                        value=self.update_enabled,
                        id="cb-sync-update",
                    )
                yield Label("Diff Summary", classes="section-title")
                yield Static(id="sync-diff-summary", content="Calculating diff...")
            with VerticalScroll(id="sync-content-panel"):
                yield Label("Synchronization Plan Preview", classes="section-title")
                yield Markdown(id="sync-plan-view", markdown="Computing synchronization plan...")
        with Horizontal(id="sync-action-bar"):
            yield Button("Refresh Diff [R]", id="btn-sync-refresh", variant="default")
            yield Button("Execute Sync [X]", id="btn-sync-execute", variant="primary")
            yield Button("Back to Main [Esc]", id="btn-sync-back", variant="default")
        yield Footer()

    def on_mount(self) -> None:
        """Initialize target agent if needed and run initial diff."""
        if not self.target_agent:
            mgr = AgentDetectionManager()
            agents = mgr.detect_agents(project_path=self.workspace_dir)
            installed = [a for a in agents.values() if a.installed]
            if installed:
                self.target_agent = installed[0]
            else:
                self.target_agent = AgentDetectionResult(
                    agent_id="claude-code",
                    name="Claude Code",
                    installed=True,
                    capabilities=[AgentCapability.MCP, AgentCapability.SKILL],
                )

        if not self.registry:
            self.registry, _ = Registry.load_auto()

        self._refresh_diff_and_plan()

    def action_back(self) -> None:
        """Return to main screen."""
        self.app.pop_screen()

    def action_refresh_sync(self) -> None:
        """Refresh diff and plan."""
        self._refresh_diff_and_plan()

    def action_execute_sync(self) -> None:
        """Execute synchronization plan."""
        self._execute_sync()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-sync-refresh":
            self._refresh_diff_and_plan()
        elif event.button.id == "btn-sync-execute":
            self._execute_sync()
        elif event.button.id == "btn-sync-back":
            self.action_back()

    def on_checkbox_changed(self, event: Checkbox.Changed) -> None:
        """Handle toggle of prune / update checkboxes."""
        if event.checkbox.id == "cb-sync-prune":
            self.prune_enabled = event.value
        elif event.checkbox.id == "cb-sync-update":
            self.update_enabled = event.value
        self._regenerate_plan_and_view()

    def _refresh_diff_and_plan(self) -> None:
        """Load lockfile specs, compute diff, and generate plan."""
        status_badge = self.query_one("#sync-status-badge", Static)
        diff_summary = self.query_one("#sync-diff-summary", Static)
        plan_view = self.query_one("#sync-plan-view", Markdown)

        agent_aid = self.target_agent.agent_id if self.target_agent else None
        try:
            self.expected_specs = self.sync_engine.load_expected_specs(
                lockfile_path=None,
                workspace_dir=self.workspace_dir,
                target_agent=agent_aid,
                registry=self.registry,
            )
        except LockfileNotFoundError:
            status_badge.update("[bold yellow]No lockfile found (aiaddons.lock)[/bold yellow]")
            diff_summary.update("No lockfile present in current workspace.")
            plan_view.update(
                "## No Lockfile Detected\n\n"
                "There is no `aiaddons.lock` file in this workspace directory.\n"
                "Install add-ons first to create a workspace lockfile."
            )
            return
        except Exception as exc:
            status_badge.update(f"[bold red]Lockfile Error:[/bold red] {exc}")
            diff_summary.update(f"Error: {exc}")
            plan_view.update(f"## Lockfile Error\n\n❌ {exc}")
            return

        self.diff = self.sync_engine.compute_diff(
            expected_specs=self.expected_specs,
            workspace_dir=self.workspace_dir,
            target_agent=agent_aid,
            scope=self.scope,
            registry=self.registry,
        )

        # Update status badge
        if self.diff.is_clean:
            status_badge.update(
                f"[bold green]✓ In Sync[/bold green]\n"
                f"Agent: {self.target_agent.name if self.target_agent else 'Unknown'}\n"
                f"All {len(self.diff.synced)} add-on(s) match lockfile."
            )
        else:
            mismatches = len(self.diff.missing) + len(self.diff.extra) + len(self.diff.mismatched)
            status_badge.update(
                f"[bold yellow]⚠️ Drift Detected ({mismatches} item(s))[/bold yellow]\n"
                f"Agent: {self.target_agent.name if self.target_agent else 'Unknown'}\n"
                f"Scope: {self.scope.value.capitalize()}"
            )

        # Update diff summary sidebar
        summary_text = (
            f"• [green]In sync:[/green] {len(self.diff.synced)}\n"
            f"• [cyan]Missing (to install):[/cyan] {len(self.diff.missing)}\n"
            f"• [yellow]Unmanaged (extra):[/yellow] {len(self.diff.extra)}\n"
            f"• [magenta]Version mismatch:[/magenta] {len(self.diff.mismatched)}"
        )
        diff_summary.update(summary_text)

        self._regenerate_plan_and_view()

    def _regenerate_plan_and_view(self) -> None:
        """Regenerate the sync plan based on current prune/update toggle settings and render markdown."""
        if not self.diff or not self.target_agent:
            return

        plan_view = self.query_one("#sync-plan-view", Markdown)

        self.current_plan = self.sync_engine.generate_sync_plan(
            diff=self.diff,
            target_agent=self.target_agent,
            scope=self.scope,
            lockfile_path=self.workspace_dir / "aiaddons.lock",
            prune=self.prune_enabled,
            update=self.update_enabled,
            registry=self.registry,
        )

        md = f"## Lockfile Diff & Reconciliation Plan\n\n"
        md += f"**Target Agent:** `{self.target_agent.name}` | **Scope:** `{self.scope.value}`\n\n"

        if self.diff.synced:
            md += f"### ✅ In Sync ({len(self.diff.synced)})\n"
            for s in self.diff.synced:
                md += f"- **{s.name}** (`{s.addon_id}`) v{s.installed_version}\n"
            md += "\n"

        if self.diff.missing:
            md += f"### 📦 Missing to Install ({len(self.diff.missing)})\n"
            for m in self.diff.missing:
                ver_str = f" v{m.expected_version}" if m.expected_version else ""
                md += f"- ➕ **{m.name}** (`{m.addon_id}`){ver_str}\n"
            md += "\n"

        if self.diff.extra:
            status_tag = "*(Will be pruned)*" if self.prune_enabled else "*(Ignored — check Prune flag to remove)*"
            md += f"### ⚠️ Unmanaged Locally ({len(self.diff.extra)}) {status_tag}\n"
            for e in self.diff.extra:
                ver_str = f" v{e.installed_version}" if e.installed_version else ""
                md += f"- 🗑️ **{e.name}** (`{e.addon_id}`){ver_str}\n"
            md += "\n"

        if self.diff.mismatched:
            status_tag = "*(Will be updated)*" if self.update_enabled else "*(Ignored — check Update flag to update)*"
            md += f"### 🔄 Version Mismatches ({len(self.diff.mismatched)}) {status_tag}\n"
            for mm in self.diff.mismatched:
                md += (
                    f"- 🔁 **{mm.name}** (`{mm.addon_id}`): "
                    f"installed `v{mm.installed_version}` → expected `v{mm.expected_version}`\n"
                )
            md += "\n"

        # Action summary
        actions_count = (
            len(self.current_plan.to_install)
            + len(self.current_plan.to_prune)
            + len(self.current_plan.to_update)
        )
        if actions_count == 0:
            if self.diff.is_clean:
                md += "### 🎉 Environment is fully in sync with `aiaddons.lock`!\n"
            else:
                md += "### ℹ️ No mutating actions scheduled.\nEnable `--prune` or `--update` flags to resolve remaining drift.\n"
        else:
            md += f"### 🚀 Planned Mutating Actions ({actions_count} total):\n"
            if self.current_plan.to_install:
                md += f"- **Install ({len(self.current_plan.to_install)}):** {', '.join(i.addon_id for i in self.current_plan.to_install)}\n"
            if self.current_plan.to_prune:
                md += f"- **Prune ({len(self.current_plan.to_prune)}):** {', '.join(i.addon_id for i in self.current_plan.to_prune)}\n"
            if self.current_plan.to_update:
                md += f"- **Update ({len(self.current_plan.to_update)}):** {', '.join(i.addon_id for i in self.current_plan.to_update)}\n"

        if self.current_plan.warnings:
            md += "\n### ⚠️ Warnings:\n"
            for w in self.current_plan.warnings:
                md += f"- {w}\n"

        plan_view.update(md)

    def _execute_sync(self) -> None:
        """Execute the planned synchronization."""
        if not self.current_plan or not self.target_agent:
            self.notify("No synchronization plan available.", severity="warning")
            return

        actions_count = (
            len(self.current_plan.to_install)
            + len(self.current_plan.to_prune)
            + len(self.current_plan.to_update)
        )

        if actions_count == 0:
            if self.diff and self.diff.is_clean:
                self.notify("Workspace is already fully in sync with lockfile.", severity="information")
                self.action_back()
            else:
                self.notify(
                    "No mutating actions scheduled. Check Prune or Update flags to apply changes.",
                    severity="warning",
                )
            return

        try:
            sync_res = self.sync_engine.execute_sync(
                plan=self.current_plan,
                expected_specs=self.expected_specs,
                target_agent=self.target_agent,
                workspace_dir=self.workspace_dir,
                registry=self.registry,
                dry_run=False,
            )
            msg = (
                f"Sync complete! Installed: {len(sync_res.installed_addons)}, "
                f"Pruned: {len(sync_res.pruned_addons)}, "
                f"Updated: {len(sync_res.updated_addons)}"
            )
            self.notify(msg, title="Sync Success", severity="information")
            self._refresh_diff_and_plan()
        except Exception as exc:
            self.notify(f"Sync failed: {exc}", title="Sync Error", severity="error")
            plan_view = self.query_one("#sync-plan-view", Markdown)
            plan_view.update(f"## Synchronization Error ❌\n\n**Error:** {exc}")
