import time
import pytest
import yaml
import subprocess
import httpx
from pathlib import Path

REGISTRY_DIR = Path(__file__).parent.parent.parent / "registry" / "addons"


def get_git_manifests():
    manifests = []
    if not REGISTRY_DIR.exists():
        return manifests

    for yaml_file in REGISTRY_DIR.glob("*.yaml"):
        with open(yaml_file, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)

        source = data.get("source", {})
        if source.get("source_type") == "git":
            manifests.append(yaml_file)
    return manifests


def get_package_manifests():
    manifests = []
    if not REGISTRY_DIR.exists():
        return manifests

    for yaml_file in REGISTRY_DIR.glob("*.yaml"):
        with open(yaml_file, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)

        source = data.get("source", {})
        if source.get("source_type") == "package":
            manifests.append(yaml_file)
    return manifests


@pytest.mark.parametrize("manifest_path", get_git_manifests(), ids=lambda p: p.name)
def test_git_manifest_sha_and_path_valid(manifest_path, tmp_path):
    """
    Validates that every git-sourced manifest in the registry has a real commit_sha
    and that the specified path (if any) exists in the repository.
    """
    with open(manifest_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    source = data["source"]
    url = source.get("url") or source.get("repository")
    sha = source["commit_sha"]
    path = source.get("path", "")

    # Check if SHA exists by trying to fetch it
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "remote", "add", "origin", url], cwd=tmp_path, check=True, capture_output=True)

    # Try fetching the specific SHA with retries in case of transient connection resets
    res = None
    for attempt in range(3):
        res = subprocess.run(["git", "fetch", "--depth", "1", "origin", sha], cwd=tmp_path, capture_output=True, text=True)
        if res.returncode == 0:
            break
        time.sleep(1.0)
    assert res is not None and res.returncode == 0, f"Manifest {manifest_path.name}: Failed to fetch commit_sha {sha}. Error: {res.stderr if res else 'Unknown error'}"

    # If a path is specified, check if it exists in the fetched commit
    if path:
        subprocess.run(["git", "checkout", "FETCH_HEAD"], cwd=tmp_path, check=True, capture_output=True)
        target_path = tmp_path / path
        assert target_path.exists(), f"Manifest {manifest_path.name}: Path '{path}' does not exist in commit {sha}"


@pytest.mark.parametrize("manifest_path", get_package_manifests(), ids=lambda p: p.name)
def test_package_manifest_resolves_on_npm(manifest_path):
    """
    Validates that every package-sourced manifest in the live registry specifies
    a package_name that actually exists on the public npm registry (not 404).
    """
    with open(manifest_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    source = data.get("source", {})
    package_name = source.get("package_name")
    assert package_name, f"Manifest {manifest_path.name}: Missing package_name"

    url = f"https://registry.npmjs.org/{package_name}"
    headers = {"User-Agent": "aiaddons-registry-validator"}

    last_err = None
    for attempt in range(3):
        try:
            with httpx.Client(headers=headers, timeout=15.0) as client:
                res = client.get(url)
                assert res.status_code == 200, (
                    f"Manifest {manifest_path.name}: Package '{package_name}' failed to resolve on npm "
                    f"(HTTP {res.status_code})."
                )
                return
        except (httpx.ConnectError, httpx.TimeoutException) as exc:
            last_err = exc
            time.sleep(1.0)
    if last_err:
        raise last_err
