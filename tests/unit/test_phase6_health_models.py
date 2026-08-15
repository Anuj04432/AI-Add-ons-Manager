"""Unit tests for Phase 6 Health Check models."""

from aiaddons.core.health.models import (
    HealthCategory,
    HealthCheckItem,
    HealthReport,
    HealthStatus,
)


def test_health_status_enum() -> None:
    """Verify HealthStatus enum values."""
    assert HealthStatus.PASS == "PASS"
    assert HealthStatus.WARN == "WARN"
    assert HealthStatus.FAIL == "FAIL"
    assert HealthStatus.SKIPPED == "SKIPPED"


def test_health_category_display_names() -> None:
    """Verify HealthCategory enum values and human-readable titles."""
    assert HealthCategory.REGISTRY.display_name == "Registry"
    assert HealthCategory.INSTALLED_STATE.display_name == "Installed State"
    assert HealthCategory.LOCKFILE.display_name == "Lockfile"
    assert HealthCategory.TRANSACTIONS.display_name == "Transactions"
    assert HealthCategory.STAGING.display_name == "Staging"
    assert HealthCategory.RUNTIMES.display_name == "Runtimes"
    assert HealthCategory.AGENTS.display_name == "AI Coding Agents"
    assert HealthCategory.CONSISTENCY.display_name == "Add-on Consistency"
    assert HealthCategory.SECURITY.display_name == "Security"


def test_health_check_item_validation() -> None:
    """Verify HealthCheckItem model validation and serialization."""
    item = HealthCheckItem(
        check_id="test_check_1",
        category=HealthCategory.REGISTRY,
        status=HealthStatus.PASS,
        message="Registry cache exists and is valid.",
        remediation=None,
        diagnostic_details={"cache_size": 1024},
    )
    assert item.check_id == "test_check_1"
    assert item.status == HealthStatus.PASS
    assert item.diagnostic_details == {"cache_size": 1024}

    # Model dump round-trip
    dumped = item.model_dump()
    assert dumped["check_id"] == "test_check_1"
    assert dumped["status"] == "PASS"
    assert dumped["category"] == "registry"


def test_health_report_aggregation() -> None:
    """Verify HealthReport category filtering and summary counts."""
    item1 = HealthCheckItem(
        check_id="check_1",
        category=HealthCategory.REGISTRY,
        status=HealthStatus.PASS,
        message="Registry cache valid.",
    )
    item2 = HealthCheckItem(
        check_id="check_2",
        category=HealthCategory.RUNTIMES,
        status=HealthStatus.WARN,
        message="uvx unavailable.",
        remediation="Install uv/uvx.",
    )
    item3 = HealthCheckItem(
        check_id="check_3",
        category=HealthCategory.SECURITY,
        status=HealthStatus.FAIL,
        message="Suspicious path detected.",
    )

    report = HealthReport(
        overall_status=HealthStatus.FAIL,
        summary={"PASS": 1, "WARN": 1, "FAIL": 1, "SKIPPED": 0},
        items=[item1, item2, item3],
        workspace_dir="/workspace",
    )

    assert report.overall_status == HealthStatus.FAIL
    assert len(report.get_items_by_category(HealthCategory.REGISTRY)) == 1
    assert len(report.get_items_by_category(HealthCategory.RUNTIMES)) == 1
    assert len(report.get_items_by_category(HealthCategory.SECURITY)) == 1
    assert len(report.get_items_by_category(HealthCategory.AGENTS)) == 0

    json_str = report.model_dump_json()
    assert "check_1" in json_str
    assert "uvx unavailable." in json_str
    assert "Suspicious path detected." in json_str
