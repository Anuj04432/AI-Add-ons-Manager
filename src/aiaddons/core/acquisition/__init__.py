"""Secure Source Acquisition, Verification & Staging Subsystem (Phase 6B)."""

from aiaddons.core.acquisition.base import BaseSourceFetcher
from aiaddons.core.acquisition.engine import AcquisitionEngine
from aiaddons.core.acquisition.extractor import SafeArchiveExtractor
from aiaddons.core.acquisition.git import GitSourceFetcher
from aiaddons.core.acquisition.local import LocalSourceFetcher
from aiaddons.core.acquisition.models import (
    AcquiredSourceResult,
    ArchiveCheckResult,
    StagingContext,
)
from aiaddons.core.acquisition.package import PackageSourceFetcher
from aiaddons.core.acquisition.staging import SourceStagingManager
from aiaddons.core.acquisition.url import UrlSourceFetcher

__all__ = [
    "AcquisitionEngine",
    "BaseSourceFetcher",
    "GitSourceFetcher",
    "UrlSourceFetcher",
    "PackageSourceFetcher",
    "LocalSourceFetcher",
    "SafeArchiveExtractor",
    "SourceStagingManager",
    "AcquiredSourceResult",
    "StagingContext",
    "ArchiveCheckResult",
]
