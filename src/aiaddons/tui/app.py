"""Textual interactive terminal UI for AI Add-ons Manager (Phase 5B.10)."""

from __future__ import annotations

from pathlib import Path

from textual.app import App, ComposeResult
from textual.containers import Container, Horizontal, Vertical, VerticalScroll
from textual.widgets import (
    Button,
    Footer,
    Header,
    Input,
    Label,
    ListItem,
    ListView,
    Markdown,
    Static,
)

from aiaddons.agents.manager import AgentDetectionManager
from aiaddons.core.compatibility.engine import CompatibilityEngine
from aiaddons.core.execution.engine import ExecutionEngine
from aiaddons.core.execution.external.security import mask_secrets_in_text
from aiaddons.core.execution.models import ExecutionStatus
from aiaddons.core.installer.engine import InstallationEngine
from aiaddons.core.installer.models import (
    InstallationPlan,
    InstallationTransaction,
    TransactionPhase,
)
from aiaddons.core.models.agent import AgentDetectionResult, Scope
from aiaddons.core.models.manifest import IntegrationManifest
from aiaddons.core.verification.engine import VerificationEngine
from aiaddons.core.verification.models import VerificationStatus
from aiaddons.registry.registry import Registry


class AIAddonsTUIApp(App[None]):
    """Textual TUI interface for add-on discovery, compatibility check, and installer."""

    CSS = """
    Screen {
        layout: vertical;
        background: $surface;
    }

    #main-container {
        layout: horizontal;
        height: 1fr;
    }

    #sidebar {
        width: 35%;
        height: 100%;
        border-right: heavy $primary;
        padding: 1;
    }

    #content-panel {
        width: 65%;
        height: 100%;
        padding: 1;
    }

    .section-title {
        text-style: bold;
        color: $accent;
        margin-bottom: 1;
    }

    .status-pass {
        color: green;
        text-style: bold;
    }

    .status-fail {
        color: red;
        text-style: bold;
    }

    #addon-list {
        height: 1fr;
        border: solid $primary;
    }

    #secrets-container {
        margin-top: 1;
        margin-bottom: 1;
    }

    #action-bar {
        height: auto;
        margin-top: 1;
        align: right middle;
    }

    Button {
        margin-left: 1;
    }
    """

    TITLE = "AI Add-ons Manager"
    SUB_TITLE = "Interactive Add-on Discovery & Transactional Installer"

    def __init__(self, registry_dir: Path | None = None) -> None:
        super().__init__()
        self.registry_dir = registry_dir or self._default_registry_dir()
        self.registry: Registry | None = None
        self.manifests: list[IntegrationManifest] = []
        self.selected_manifest: IntegrationManifest | None = None
        self.detected_agents: dict[str, AgentDetectionResult] = {}
        self.selected_agent: AgentDetectionResult | None = None
        self.selected_scope: Scope = Scope.WORKSPACE
        self.current_plan: InstallationPlan | None = None
        self.current_tx: InstallationTransaction | None = None
        self.secret_inputs: dict[str, Input] = {}
        self.resolved_secrets: dict[str, str] = {}

    @staticmethod
    def _default_registry_dir() -> Path:
        addons_dir = Path.cwd() / "registry" / "addons"
        if addons_dir.exists():
            return addons_dir
        reg_dir = Path.cwd() / "registry"
        if reg_dir.exists():
            return reg_dir
        return Path("registry")

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Container(id="main-container"):
            with Vertical(id="sidebar"):
                yield Label("Available Add-ons", classes="section-title")
                yield ListView(id="addon-list")
                yield Label("Target Configuration", classes="section-title")
                yield Static(id="config-summary", content="Select an add-on to begin.")
            with VerticalScroll(id="content-panel"):
                yield Label("Add-on Details & Plan Preview", classes="section-title")
                yield Markdown(id="detail-view", markdown="Select an add-on from the list.")
                with Vertical(id="secrets-container"):
                    yield Label(id="secrets-label", content="")
                with Horizontal(id="action-bar"):
                    yield Button("Check Compatibility", id="btn-compat", variant="default")
                    yield Button("Preview Plan", id="btn-plan", variant="default")
                    yield Button(
                        "Confirm Installation",
                        id="btn-confirm",
                        variant="primary",
                        disabled=True,
                    )
        yield Footer()

    def on_mount(self) -> None:
        """Initialize registry and agent detection on app startup."""
        if self.registry_dir.exists():
            self.registry, _ = Registry.from_directory(self.registry_dir)
            self.manifests = self.registry.list()

        manager = AgentDetectionManager()
        self.detected_agents = manager.detect_agents()

        # Populate Addon ListView
        list_view = self.query_one("#addon-list", ListView)
        list_view.clear()
        for m in self.manifests:
            list_view.append(ListItem(Label(f"{m.name} ({m.id})"), id=f"item-{m.id}"))

        if self.detected_agents:
            installed = [a for a in self.detected_agents.values() if a.installed]
            if installed:
                self.selected_agent = installed[0]
            else:
                self.selected_agent = list(self.detected_agents.values())[0]

        self._update_config_summary()

    def _update_config_summary(self) -> None:
        summary_widget = self.query_one("#config-summary", Static)
        agent_str = self.selected_agent.name if self.selected_agent else "None detected"
        scope_str = self.selected_scope.value.capitalize()
        text = f"Agent: [green]{agent_str}[/green]\nScope: [yellow]{scope_str}[/yellow]"
        summary_widget.update(text)

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        """Handle selection of an add-on item from the list."""
        if not event.item or not event.item.id:
            return
        addon_id = event.item.id.replace("item-", "")
        if self.registry:
            self.selected_manifest = self.registry.get(addon_id)
            self._update_details_view()

    def _update_details_view(self) -> None:
        detail_view = self.query_one("#detail-view", Markdown)
        confirm_btn = self.query_one("#btn-confirm", Button)
        confirm_btn.disabled = True

        if not self.selected_manifest:
            detail_view.update("No add-on selected.")
            return

        m = self.selected_manifest
        md_text = f"## {m.name} (`{m.id}`)\n\n"
        md_text += (
            f"**Version:** {m.version} | **License:** {m.license} | **Category:** {m.category}\n\n"
        )
        md_text += f"{m.description}\n\n"
        md_text += f"**Integration Type:** `{m.integration_type.value}`\n\n"

        if self.selected_agent:
            compat_engine = CompatibilityEngine(registry=self.registry)
            compat = compat_engine.evaluate(
                m, self.selected_agent, self.selected_scope, registry=self.registry
            )
            if compat.compatible:
                md_text += f"### Compatibility: ✅ Compatible with {self.selected_agent.name}\n\n"
                confirm_btn.disabled = False
            else:
                reasons = "\n".join([f"- {r}" for r in compat.reasons])
                md_text += (
                    f"### Compatibility: ❌ Incompatible with {self.selected_agent.name}\n"
                    f"{reasons}\n\n"
                )
        else:
            md_text += "### Compatibility: ⚠️ No AI Agent Detected\n\n"

        detail_view.update(md_text)
        self._prepare_secrets_ui()

    def _prepare_secrets_ui(self) -> None:
        container = self.query_one("#secrets-container", Vertical)
        container.remove_children()

        if not self.selected_manifest or not self.selected_manifest.handler_spec.mcp:
            return

        env_vars = self.selected_manifest.handler_spec.mcp.env_vars
        if not env_vars:
            return

        required_vars = [v for v in env_vars if v.required]
        if not required_vars:
            return

        container.mount(Label("Required Secrets Input", classes="section-title"))
        self.secret_inputs.clear()
        for spec in required_vars:
            lbl = Label(f"{spec.name} ({spec.description or 'Required secret'}):")
            inp = Input(placeholder=f"Enter {spec.name}", password=True, id=f"sec-{spec.name}")
            container.mount(lbl)
            container.mount(inp)
            self.secret_inputs[spec.name] = inp

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Dispatch action buttons to core application engine services."""
        if event.button.id == "btn-compat":
            self._run_compatibility_check()
        elif event.button.id == "btn-plan":
            self._run_plan_preview()
        elif event.button.id == "btn-confirm":
            self._execute_real_installation()

    def _run_compatibility_check(self) -> None:
        if not self.selected_manifest or not self.selected_agent:
            return
        compat_engine = CompatibilityEngine(registry=self.registry)
        compat = compat_engine.evaluate(
            self.selected_manifest,
            self.selected_agent,
            self.selected_scope,
            registry=self.registry,
        )
        detail_view = self.query_one("#detail-view", Markdown)

        status_sym = "✅" if compat.compatible else "❌"
        is_comp = "Compatible" if compat.compatible else "Incompatible"
        md = "## Compatibility Result\n\n"
        md += f"**Add-on:** {self.selected_manifest.name}\n"
        md += f"**Agent:** {self.selected_agent.name}\n"
        md += f"**Status:** {status_sym} {is_comp}\n\n"
        for r in compat.reasons:
            md += f"- {r}\n"
        detail_view.update(md)

    def _run_plan_preview(self) -> None:
        if not self.selected_manifest or not self.selected_agent or not self.registry:
            return
        inst_engine = InstallationEngine(registry=self.registry)
        try:
            plan = inst_engine.generate_plan(
                self.selected_manifest, self.selected_agent, self.selected_scope
            )
            self.current_plan = plan
            detail_view = self.query_one("#detail-view", Markdown)

            md = "## Installation Plan Preview (Dry-Run)\n\n"
            md += f"**Target Agent:** {self.selected_agent.name}\n"
            md += f"**Scope:** {self.selected_scope.value}\n\n"
            md += "### Operations to be executed:\n"
            for op in plan.planned_operations:
                md += f"- ✓ {op.description}\n"
            md += "\n*No changes were made to host system state.*\n"
            detail_view.update(md)
        except Exception as exc:
            detail_view = self.query_one("#detail-view", Markdown)
            detail_view.update(f"## Plan Preview Error\n\n❌ {exc}")

    def _execute_real_installation(self) -> None:
        """Delegate installation execution directly to core ExecutionEngine & VerificationEngine."""
        if not self.selected_manifest or not self.selected_agent or not self.registry:
            return

        detail_view = self.query_one("#detail-view", Markdown)

        # Collect secrets from hidden input fields
        secret_map: dict[str, str] = {}
        for name, inp in self.secret_inputs.items():
            val = inp.value.strip()
            if not val:
                detail_view.update(f"## Installation Error\n\n❌ Secret '{name}' is required.")
                return
            secret_map[name] = val

        inst_engine = InstallationEngine(registry=self.registry)
        execution_engine = ExecutionEngine()
        verification_engine = VerificationEngine()

        try:
            tx = inst_engine.create_transaction(
                self.selected_manifest, self.selected_agent, self.selected_scope
            )
            if not tx.plan:
                detail_view.update(f"## Planning Error\n\n❌ {tx.error_message}")
                return

            plan = tx.plan
            tx.phase = TransactionPhase.REVIEWED
            tx.phase = TransactionPhase.EXECUTING

            # Delegate execution to core engine
            res = execution_engine.execute_plan(
                plan, transaction=tx, dry_run=False, secret_values=secret_map
            )

            if res.status == ExecutionStatus.SUCCESS:
                tx.phase = TransactionPhase.COMMITTED
                ver_res = verification_engine.verify_plan(
                    plan, dry_run=False, secret_values=secret_map
                )

                md = "## Installation Successful! 🎉\n\n"
                md += f"**Add-on:** {self.selected_manifest.name}\n"
                md += f"**Agent:** {self.selected_agent.name}\n"
                md += f"**Transaction Phase:** `{TransactionPhase.COMMITTED.value}`\n\n"
                md += "### Verification Results:\n"
                for chk in ver_res.checks:
                    chk_desc = mask_secrets_in_text(chk.description, list(secret_map.values()))
                    sym = "✓" if chk.status == VerificationStatus.PASSED else "✗"
                    md += f"- {sym} {chk_desc}\n"
                detail_view.update(md)
            else:
                tx.phase = TransactionPhase.ROLLED_BACK
                raw_err = res.error_message or "Execution failed"
                err_msg = mask_secrets_in_text(raw_err, list(secret_map.values()))
                md = "## Verification / Execution Failed ❌\n\n"
                md += f"**Error:** {err_msg}\n\n"
                md += f"**Transaction Phase:** `{TransactionPhase.ROLLED_BACK.value}`\n\n"
                md += "### Rollback Actions Executed:\n"
                for rb in res.rolled_back_operations:
                    rb_desc = mask_secrets_in_text(rb.description, list(secret_map.values()))
                    md += f"- ↩ {rb_desc}\n"
                detail_view.update(md)

        except Exception as exc:
            err_msg = mask_secrets_in_text(str(exc), list(secret_map.values()))
            detail_view.update(f"## Installation Error\n\n❌ {err_msg}")
