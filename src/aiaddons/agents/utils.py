"""Helper utilities for agent detection and CLI binary version parsing."""

import re
import shutil
import subprocess
from pathlib import Path


def find_executable(name: str) -> Path | None:
    """Find executable in system PATH using shutil.which, returning absolute Path or None."""
    found = shutil.which(name)
    if found:
        return Path(found)
    return None


def parse_version_output(output: str) -> str | None:
    """Extract a SemVer-like version string from raw command output."""
    if not output:
        return None
    match = re.search(r"v?(\d+\.\d+\.\d+(?:-[\w.-]+)?)", output)
    if match:
        return match.group(1)
    return None


def run_version_command(
    cmd: str, args: list[str] | None = None, timeout_seconds: float = 5.0
) -> str | None:
    """Safely run executable version command without shell=True and return parsed version."""
    executable_path = find_executable(cmd)
    if executable_path is None:
        return None

    full_cmd = [str(executable_path)] + (args if args is not None else ["--version"])

    try:
        res = subprocess.run(
            full_cmd,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
        output_text = res.stdout if res.stdout else res.stderr
        return parse_version_output(output_text)
    except (subprocess.SubprocessError, OSError, ValueError):
        return None
