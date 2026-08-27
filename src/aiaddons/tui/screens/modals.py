"""Modal screen components for confirmation, drift detection, and update previews."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Label, Markdown

from aiaddons.core.models.manifest import IntegrationManifest
from aiaddons.core.update.models import UpdatePlan


class DriftConfirmModal(ModalScreen[bool]):
    """Confirmation modal displayed when filesystem/configuration drift is detected before removal."""

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
    ]

    CSS = """
    DriftConfirmModal {
        align: center middle;
    }

    #drift-dialog {
        width: 70%;
        height: 60%;
        border: thick $error;
        background: $surface;
        padding: 1;
    }

    #drift-content {
        height: 1fr;
    }

    #drift-buttons {
        height: auto;
        align: right middle;
        margin-top: 1;
    }

    Button {
        margin-left: 1;
    }
    """

    def __init__(
        self,
        addon_id: str,
        addon_name: str,
        drifts: list[str],
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        super().__init__(name=name, id=id, classes=classes)
        self.addon_id = addon_id
        self.addon_name = addon_name
        self.drifts = drifts

    def compose(self) -> ComposeResult:
        with Container(id="drift-dialog"):
            yield Label("⚠️ Configuration Drift Detected", classes="section-title")
            with VerticalScroll(id="drift-content"):
                drift_md = (
                    f"Configuration or filesystem drift was detected for **{self.addon_name}** (`{self.addon_id}`).\n\n"
                    "The live host state differs from the recorded installed state:\n\n"
                )
                for d in self.drifts:
                    drift_md += f"- ⚠️ {d}\n"
                drift_md += (
                    "\n**Proceeding will force removal and clean up recorded state.**\n"
                    "Do you want to continue with removal?"
                )
                yield Markdown(drift_md)
            with Horizontal(id="drift-buttons"):
                yield Button("Cancel [Esc]", id="btn-cancel", variant="default")
                yield Button("Force Remove", id="btn-force-remove", variant="error")

    def action_cancel(self) -> None:
        self.dismiss(False)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-cancel":
            self.dismiss(False)
        elif event.button.id == "btn-force-remove":
            self.dismiss(True)


class RemoveConfirmModal(ModalScreen[bool]):
    """Standard confirmation modal for add-on removal."""

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
    ]

    CSS = """
    RemoveConfirmModal {
        align: center middle;
    }

    #remove-dialog {
        width: 60%;
        height: 50%;
        border: thick $primary;
        background: $surface;
        padding: 1;
    }

    #remove-content {
        height: 1fr;
    }

    #remove-buttons {
        height: auto;
        align: right middle;
        margin-top: 1;
    }

    Button {
        margin-left: 1;
    }
    """

    def __init__(
        self,
        manifest: IntegrationManifest,
        agent_name: str,
        scope: str,
        planned_ops: list[str],
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        super().__init__(name=name, id=id, classes=classes)
        self.manifest = manifest
        self.agent_name = agent_name
        self.scope = scope
        self.planned_ops = planned_ops

    def compose(self) -> ComposeResult:
        with Container(id="remove-dialog"):
            yield Label("Confirm Add-on Removal", classes="section-title")
            with VerticalScroll(id="remove-content"):
                md = (
                    f"Are you sure you want to remove **{self.manifest.name}** (`{self.manifest.id}`)?\n\n"
                    f"**Target Agent:** {self.agent_name}\n"
                    f"**Scope:** {self.scope}\n\n"
                    "### Operations to be executed:\n"
                )
                for op in self.planned_ops:
                    md += f"- ✓ {op}\n"
                md += "\n*This action will modify agent configuration and remove deployed assets.*"
                yield Markdown(md)
            with Horizontal(id="remove-buttons"):
                yield Button("Cancel [Esc]", id="btn-cancel", variant="default")
                yield Button("Confirm Remove", id="btn-confirm-remove", variant="error")

    def action_cancel(self) -> None:
        self.dismiss(False)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-cancel":
            self.dismiss(False)
        elif event.button.id == "btn-confirm-remove":
            self.dismiss(True)


class UpdatePlanModal(ModalScreen[bool]):
    """Modal displaying a dry-run update plan preview before confirming execution."""

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
    ]

    CSS = """
    UpdatePlanModal {
        align: center middle;
    }

    #update-dialog {
        width: 75%;
        height: 70%;
        border: thick $warning;
        background: $surface;
        padding: 1;
    }

    #update-content {
        height: 1fr;
    }

    #update-buttons {
        height: auto;
        align: right middle;
        margin-top: 1;
    }

    Button {
        margin-left: 1;
    }
    """

    def __init__(
        self,
        plan: UpdatePlan,
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        super().__init__(name=name, id=id, classes=classes)
        self.plan = plan

    def compose(self) -> ComposeResult:
        with Container(id="update-dialog"):
            yield Label("Update Plan Preview (Dry-Run)", classes="section-title")
            with VerticalScroll(id="update-content"):
                md = (
                    f"## Update Plan: {self.plan.target_agent_name} ({self.plan.target_scope.value})\n\n"
                    f"Targeting **{len(self.plan.items)}** add-on(s) for atomic version update:\n\n"
                )
                for item in self.plan.items:
                    md += f"### {item.addon_name} (`{item.addon_id}`)\n"
                    md += f"**Version:** `{item.current_version}` → `v{item.target_version}`\n\n"
                    md += "#### Phase 1: Removal Operations\n"
                    for op in item.removal_plan.planned_operations:
                        md += f"- 🗑️ {op.description}\n"
                    md += "\n#### Phase 2: Installation Operations\n"
                    for op in item.install_plan.planned_operations:
                        md += f"- 📦 {op.description}\n"
                    md += "\n"

                if self.plan.warnings:
                    md += "### ⚠️ Notices & Warnings:\n"
                    for w in self.plan.warnings:
                        md += f"- {w}\n"
                    md += "\n"

                md += "*All changes will be executed within a single crash-safe WAL transaction.*"
                yield Markdown(md)
            with Horizontal(id="update-buttons"):
                yield Button("Cancel [Esc]", id="btn-cancel", variant="default")
                yield Button("Confirm & Apply Update", id="btn-confirm-update", variant="primary")

    def action_cancel(self) -> None:
        self.dismiss(False)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-cancel":
            self.dismiss(False)
        elif event.button.id == "btn-confirm-update":
            self.dismiss(True)
