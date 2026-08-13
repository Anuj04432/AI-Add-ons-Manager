"""Unit tests for Phase 6B Source Acquisition, Verification & Staging Subsystem."""

import hashlib
import zipfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from aiaddons.core.acquisition.engine import AcquisitionEngine
from aiaddons.core.acquisition.git import GitSourceFetcher
from aiaddons.core.acquisition.local import LocalSourceFetcher
from aiaddons.core.acquisition.package import PackageSourceFetcher
from aiaddons.core.acquisition.staging import SourceStagingManager
from aiaddons.core.acquisition.url import UrlSourceFetcher
from aiaddons.core.exceptions import (
    ChecksumMismatchError,
    GitAcquisitionError,
    SourceAcquisitionError,
)
from aiaddons.core.execution.external.models import ExternalExecutionResult, ExternalRuntime
from aiaddons.core.execution.external.runner import ExternalRunner
from aiaddons.core.models.manifest import SourceSpec, SourceType

# -----------------------------------------------------------------------------
# GIT SOURCE FETCHER TESTS
# -----------------------------------------------------------------------------


def test_git_fetcher_valid_sha(tmp_path: Path) -> None:
    """Verify git fetcher clones and checks out valid 40-char commit SHA using ExternalRunner."""
    mock_runner = MagicMock(spec=ExternalRunner)
    # Return success for clone, checkout, rev-parse
    mock_runner.execute.side_effect = [
        ExternalExecutionResult(
            success=True,
            runtime=ExternalRuntime.GIT,
            executable_path="git",
            command_vector=["git"],
            return_code=0,
            stdout="cloning",
            stderr="",
            duration=0.1,
        ),
        ExternalExecutionResult(
            success=True,
            runtime=ExternalRuntime.GIT,
            executable_path="git",
            command_vector=["git"],
            return_code=0,
            stdout="checkout",
            stderr="",
            duration=0.1,
        ),
        ExternalExecutionResult(
            success=True,
            runtime=ExternalRuntime.GIT,
            executable_path="git",
            command_vector=["git"],
            return_code=0,
            stdout="a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0\n",
            stderr="",
            duration=0.1,
        ),
    ]

    fetcher = GitSourceFetcher(runner=mock_runner)
    staging_dir = tmp_path / "staging_git"
    staging_dir.mkdir()
    # Create a dummy file in staging for file listing
    (staging_dir / "SKILL.md").write_text("content")

    valid_sha = "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0"
    source = SourceSpec(
        source_type=SourceType.GIT,
        repository="https://github.com/example/repo.git",
        commit_sha=valid_sha,
    )

    res = fetcher.fetch("my-addon", source, staging_dir, dry_run=False)
    assert res.commit_sha == valid_sha
    assert res.is_staged is True

    # Verify ExternalRunner calls
    calls = mock_runner.execute.call_args_list
    assert len(calls) == 3
    # Verify submodules & hooks disabled in clone request
    clone_req = calls[0][0][0]
    assert "--no-recurse-submodules" in clone_req.args
    assert "core.hooksPath=/dev/null" in clone_req.args


def test_git_fetcher_missing_sha_fails(tmp_path: Path) -> None:
    """Verify git fetcher rejects mutable branch/tag without 40-char commit SHA."""
    fetcher = GitSourceFetcher()
    source = SourceSpec(
        source_type=SourceType.GIT,
        repository="https://github.com/example/repo.git",
        ref="main",  # Missing commit_sha
    )
    with pytest.raises(GitAcquisitionError, match="requires an immutable 40-character"):
        fetcher.fetch("my-addon", source, tmp_path / "staging", dry_run=False)


def test_git_fetcher_invalid_sha_format(tmp_path: Path) -> None:
    """Verify git fetcher rejects short or non-hex commit SHA."""
    fetcher = GitSourceFetcher()
    source = SourceSpec(
        source_type=SourceType.GIT,
        repository="https://github.com/example/repo.git",
        commit_sha="a1b2c3",  # Too short
    )
    with pytest.raises(GitAcquisitionError, match="requires an immutable 40-character"):
        fetcher.fetch("my-addon", source, tmp_path / "staging", dry_run=False)


def test_git_fetcher_dry_run(tmp_path: Path) -> None:
    """Verify git fetcher in dry-run mode executes zero subprocesses."""
    mock_runner = MagicMock(spec=ExternalRunner)
    fetcher = GitSourceFetcher(runner=mock_runner)
    valid_sha = "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0"
    source = SourceSpec(
        source_type=SourceType.GIT,
        repository="https://github.com/example/repo.git",
        commit_sha=valid_sha,
    )

    res = fetcher.fetch("my-addon", source, tmp_path / "staging", dry_run=True)
    assert res.is_dry_run is True
    assert res.is_staged is False
    assert mock_runner.execute.call_count == 0


# -----------------------------------------------------------------------------
# URL SOURCE FETCHER TESTS
# -----------------------------------------------------------------------------


def test_url_fetcher_checksum_pass(tmp_path: Path) -> None:
    """Verify UrlSourceFetcher streams download and verifies matching SHA-256 checksum."""
    zip_bytes = io_create_zip_bytes({"SKILL.md": "# Skill Content"})
    actual_hash = hashlib.sha256(zip_bytes).hexdigest()

    mock_httpx = MagicMock()
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.iter_bytes.return_value = [zip_bytes]
    mock_httpx.get.return_value = mock_response

    fetcher = UrlSourceFetcher(httpx_client=mock_httpx)
    source = SourceSpec(
        source_type=SourceType.URL,
        url="https://localhost/package.zip",
        checksum=f"sha256:{actual_hash}",
    )
    staging_dir = tmp_path / "staging_url"
    res = fetcher.fetch("my-addon", source, staging_dir, dry_run=False)

    assert res.checksum == f"sha256:{actual_hash}"
    assert res.is_staged is True
    assert (staging_dir / "SKILL.md").exists()


def test_url_fetcher_checksum_mismatch_fails(tmp_path: Path) -> None:
    """Verify UrlSourceFetcher raises ChecksumMismatchError when hash does not match."""
    zip_bytes = io_create_zip_bytes({"SKILL.md": "# Skill Content"})

    mock_httpx = MagicMock()
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.iter_bytes.return_value = [zip_bytes]
    mock_httpx.get.return_value = mock_response

    fetcher = UrlSourceFetcher(httpx_client=mock_httpx)
    wrong_hash = "0" * 64
    source = SourceSpec(
        source_type=SourceType.URL,
        url="https://localhost/package.zip",
        checksum=f"sha256:{wrong_hash}",
    )
    staging_dir = tmp_path / "staging_url"

    with pytest.raises(ChecksumMismatchError, match="Checksum mismatch"):
        fetcher.fetch("my-addon", source, staging_dir, dry_run=False)


def test_url_fetcher_insecure_http_rejected(tmp_path: Path) -> None:
    """Verify UrlSourceFetcher rejects remote insecure http:// URL."""
    fetcher = UrlSourceFetcher()
    source = SourceSpec(
        source_type=SourceType.URL,
        url="http://insecure-server.com/addon.zip",
        checksum="sha256:" + "a" * 64,
    )
    with pytest.raises(SourceAcquisitionError, match="Insecure HTTP URL"):
        fetcher.fetch("my-addon", source, tmp_path / "staging", dry_run=False)


def test_url_fetcher_dry_run(tmp_path: Path) -> None:
    """Verify UrlSourceFetcher in dry-run performs zero HTTP requests."""
    mock_httpx = MagicMock()
    fetcher = UrlSourceFetcher(httpx_client=mock_httpx)
    source = SourceSpec(
        source_type=SourceType.URL,
        url="https://example.com/addon.zip",
        checksum="sha256:" + "a" * 64,
    )

    res = fetcher.fetch("my-addon", source, tmp_path / "staging", dry_run=True)
    assert res.is_dry_run is True
    assert mock_httpx.get.call_count == 0


# -----------------------------------------------------------------------------
# LOCAL SOURCE FETCHER TESTS
# -----------------------------------------------------------------------------


def test_local_fetcher_safe_copy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify LocalSourceFetcher safely copies local files into staging."""
    monkeypatch.chdir(tmp_path)
    src_dir = tmp_path / "local_addon"
    src_dir.mkdir()
    (src_dir / "SKILL.md").write_text("# Local Skill")

    fetcher = LocalSourceFetcher()
    source = SourceSpec(
        source_type=SourceType.LOCAL,
        path="local_addon",
    )
    staging_dir = tmp_path / "staging_local"

    res = fetcher.fetch("my-addon", source, staging_dir, dry_run=False)
    assert res.is_staged is True
    assert (staging_dir / "SKILL.md").exists()
    # Ensure original remains unmodified
    assert (src_dir / "SKILL.md").exists()


def test_local_fetcher_traversal_rejected() -> None:
    """Verify SourceSpec rejects relative path traversal for local sources."""
    with pytest.raises(ValueError, match="Traversal sequence"):
        SourceSpec(
            source_type=SourceType.LOCAL,
            path="../outside",
        )


# -----------------------------------------------------------------------------
# PACKAGE FETCHER TESTS
# -----------------------------------------------------------------------------


def test_package_fetcher_mcp_spec(tmp_path: Path) -> None:
    """Verify PackageSourceFetcher sanitizes package name and stages spec descriptor."""
    fetcher = PackageSourceFetcher()
    source = SourceSpec(
        source_type=SourceType.PACKAGE,
        package_name="@modelcontextprotocol/server-github",
    )
    staging_dir = tmp_path / "staging_pkg"

    res = fetcher.fetch("github-mcp", source, staging_dir, dry_run=False)
    assert res.is_staged is True
    assert (staging_dir / "package_spec.json").exists()


# -----------------------------------------------------------------------------
# STAGING MANAGER & ENGINE TESTS
# -----------------------------------------------------------------------------


def test_staging_manager_lifecycle(tmp_path: Path) -> None:
    """Verify SourceStagingManager creates isolated staging dir and cleans up."""
    mgr = SourceStagingManager(base_staging_dir=tmp_path / "staging_base")
    tx_id = "tx_123456"

    stg = mgr.get_staging_dir(tx_id, create=True)
    assert stg.exists()
    (stg / "temp.txt").write_text("data")

    mgr.cleanup_staging(tx_id)
    assert not stg.exists()


def test_acquisition_engine_dry_run(tmp_path: Path) -> None:
    """Verify AcquisitionEngine under dry-run performs zero mutations."""
    engine = AcquisitionEngine(staging_manager=SourceStagingManager(tmp_path / "stg"))
    valid_sha = "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0"
    source = SourceSpec(
        source_type=SourceType.GIT,
        repository="https://github.com/example/repo.git",
        commit_sha=valid_sha,
    )

    res = engine.acquire_source("test-addon", source, "tx_999", dry_run=True)
    assert res.is_dry_run is True
    assert not (tmp_path / "stg" / "tx_999").exists()


# Helper to build in-memory zip bytes
def io_create_zip_bytes(files: dict[str, str]) -> bytes:
    import io

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for fname, content in files.items():
            zf.writestr(fname, content)
    return buf.getvalue()
