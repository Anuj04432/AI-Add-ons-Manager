import pytest
import yaml
import subprocess
from pathlib import Path

REGISTRY_DIR = Path(__file__).parent.parent.parent / "registry" / "addons"

def get_git_manifests():
    manifests = []
    if not REGISTRY_DIR.exists():
        return manifests
        
    for yaml_file in REGISTRY_DIR.glob("*.yaml"):
        with open(yaml_file, 'r', encoding='utf-8') as f:
            data = yaml.safe_load(f)
            
        source = data.get("source", {})
        if source.get("source_type") == "git":
            manifests.append(yaml_file)
    return manifests

@pytest.mark.parametrize("manifest_path", get_git_manifests())
def test_git_manifest_sha_and_path_valid(manifest_path, tmp_path):
    """
    Validates that every git-sourced manifest in the registry has a real commit_sha
    and that the specified path (if any) exists in the repository.
    """
    with open(manifest_path, 'r', encoding='utf-8') as f:
        data = yaml.safe_load(f)
        
    source = data["source"]
    url = source.get("url") or source.get("repository")
    sha = source["commit_sha"]
    path = source.get("path", "")
    
    # Check if SHA exists by trying to fetch it
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "remote", "add", "origin", url], cwd=tmp_path, check=True, capture_output=True)
    
    # Try fetching the specific SHA
    res = subprocess.run(["git", "fetch", "--depth", "1", "origin", sha], cwd=tmp_path, capture_output=True, text=True)
    assert res.returncode == 0, f"Manifest {manifest_path.name}: Failed to fetch commit_sha {sha}. Error: {res.stderr}"
    
    # If a path is specified, check if it exists in the fetched commit
    if path:
        subprocess.run(["git", "checkout", "FETCH_HEAD"], cwd=tmp_path, check=True, capture_output=True)
        target_path = tmp_path / path
        assert target_path.exists(), f"Manifest {manifest_path.name}: Path '{path}' does not exist in commit {sha}"
