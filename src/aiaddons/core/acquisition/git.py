"""Git repository source acquisition fetcher using ExternalRunner."""

import re
import shutil
from pathlib import Path

from aiaddons.core.acquisition.base import BaseSourceFetcher
from aiaddons.core.acquisition.models import AcquiredSourceResult
from aiaddons.core.exceptions import GitAcquisitionError
from aiaddons.core.execution.external.models import (
    ExternalExecutionRequest,
    ExternalRuntime,
)
from aiaddons.core.execution.external.runner import ExternalRunner
from aiaddons.core.models.manifest import SourceSpec, SourceType

COMMIT_SHA_REGEX = re.compile(r"^[a-fA-F0-9]{40}$")


class GitSourceFetcher(BaseSourceFetcher):
    """Secure Git fetcher enforcing immutable 40-character commit SHAs and zero hook execution."""

    def __init__(self, runner: ExternalRunner | None = None) -> None:
        self.runner = runner or ExternalRunner()

    def supported_source_type(self) -> SourceType:
        return SourceType.GIT

    def fetch(
        self,
        manifest_id: str,
        source: SourceSpec,
        staging_dir: Path,
        dry_run: bool = False,
    ) -> AcquiredSourceResult:
        if source.source_type != SourceType.GIT:
            raise GitAcquisitionError(
                f"Expected source type 'git', got '{source.source_type.value}'."
            )

        # 1. Require repository URL
        repo_url = source.repository or source.url
        if not repo_url or not repo_url.strip():
            raise GitAcquisitionError(f"Git source for '{manifest_id}' requires a repository URL.")

        # 2. Require immutable 40-character commit_sha
        commit_sha = source.commit_sha
        if not commit_sha or not COMMIT_SHA_REGEX.match(commit_sha.strip()):
            raise GitAcquisitionError(
                f"Security violation: Add-on '{manifest_id}' git source requires an immutable "
                "40-character hexadecimal commit_sha."
            )
        commit_sha = commit_sha.strip().lower()

        if dry_run:
            return AcquiredSourceResult(
                manifest_id=manifest_id,
                source_type=SourceType.GIT,
                staging_path=staging_dir,
                commit_sha=commit_sha,
                is_staged=False,
                is_dry_run=True,
                acquired_files=[f"[DRY-RUN] git clone {repo_url} @ {commit_sha}"],
            )

        # Real git clone & checkout using ExternalRunner
        try:
            # 3. Clone repository with submodules disabled & hooks disabled
            clone_req = ExternalExecutionRequest(
                runtime=ExternalRuntime.GIT,
                args=[
                    "-c",
                    "core.hooksPath=/dev/null",
                    "clone",
                    "--no-checkout",
                    "--no-recurse-submodules",
                    repo_url,
                    str(staging_dir),
                ],
                timeout=120.0,
            )
            res_clone = self.runner.execute(clone_req, dry_run=False)
            if not res_clone.success:
                err_msg = res_clone.error_message or res_clone.stderr
                raise GitAcquisitionError(f"Git clone failed for '{repo_url}': {err_msg}")

            # 4. Checkout exact commit_sha (detached HEAD)
            checkout_req = ExternalExecutionRequest(
                runtime=ExternalRuntime.GIT,
                args=[
                    "-c",
                    "core.hooksPath=/dev/null",
                    "checkout",
                    "--detach",
                    commit_sha,
                ],
                cwd=str(staging_dir),
                timeout=60.0,
            )
            res_checkout = self.runner.execute(checkout_req, dry_run=False)
            if not res_checkout.success:
                raise GitAcquisitionError(
                    f"Git checkout of commit '{commit_sha}' failed: "
                    f"{res_checkout.error_message or res_checkout.stderr}"
                )

            # 5. Verify HEAD matches requested commit_sha
            rev_req = ExternalExecutionRequest(
                runtime=ExternalRuntime.GIT,
                args=["rev-parse", "HEAD"],
                cwd=str(staging_dir),
                timeout=10.0,
            )
            res_rev = self.runner.execute(rev_req, dry_run=False)
            if not res_rev.success:
                raise GitAcquisitionError(
                    f"Failed to verify Git HEAD commit SHA for '{manifest_id}'."
                )

            actual_sha = res_rev.stdout.strip().lower()
            if actual_sha != commit_sha:
                raise GitAcquisitionError(
                    f"Security violation: Git HEAD '{actual_sha}' does not match "
                    f"expected commit SHA '{commit_sha}'."
                )

            # Collect list of checked out files relative to staging_dir
            acquired_files = [
                str(p.relative_to(staging_dir)).replace("\\", "/")
                for p in staging_dir.rglob("*")
                if p.is_file() and not str(p.relative_to(staging_dir)).startswith(".git")
            ]

            return AcquiredSourceResult(
                manifest_id=manifest_id,
                source_type=SourceType.GIT,
                staging_path=staging_dir,
                commit_sha=commit_sha,
                is_staged=True,
                is_dry_run=False,
                acquired_files=acquired_files,
            )
        except Exception:
            if staging_dir.exists():
                try:
                    shutil.rmtree(staging_dir, ignore_errors=True)
                except Exception:
                    pass
            raise
