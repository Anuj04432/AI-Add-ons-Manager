"""Security utilities for target-root boundary enforcement and symlink protection."""

from pathlib import Path

from aiaddons.core.exceptions import SecurityValidationError


def verify_safe_target_path(target_root: str | Path, path: str | Path) -> tuple[Path, Path]:
    """Verify that a path remains strictly inside target_root, checking boundaries.

    Args:
        target_root: The trusted root directory boundary.
        path: The requested destination or file path (relative or absolute).

    Returns:
        tuple[Path, Path]: (resolved_root, resolved_destination)

    Raises:
        SecurityValidationError: If the path escapes target_root or follows dangerous symlinks.
    """
    root_obj = Path(target_root).expanduser()
    try:
        resolved_root = root_obj.resolve(strict=False)
    except Exception as err:
        raise SecurityValidationError(f"Invalid target_root '{target_root}': {err}") from err

    path_obj = Path(path)
    if path_obj.is_absolute():
        candidate = path_obj
    else:
        candidate = resolved_root / path_obj

    # Expand user and resolve target destination
    try:
        resolved_dest = candidate.expanduser().resolve(strict=False)
    except Exception as err:
        raise SecurityValidationError(f"Path resolution error for '{path}': {err}") from err

    # Check relative confinement against resolved target root
    try:
        if not resolved_dest.is_relative_to(resolved_root):
            msg = (
                f"Security violation: Destination '{resolved_dest}' "
                f"escapes target_root '{resolved_root}'."
            )
            raise SecurityValidationError(msg)
    except ValueError as err:
        msg = f"Security violation: Path '{resolved_dest}' outside '{resolved_root}'."
        raise SecurityValidationError(msg) from err

    # Inspect parent directory components for symlinks resolving outside target_root
    curr = candidate.parent.expanduser()
    while curr != resolved_root and curr != curr.parent:
        if curr.is_symlink():
            try:
                sym_target = curr.resolve()
                if not sym_target.is_relative_to(resolved_root):
                    msg = (
                        f"Security violation: Symlink '{curr}' resolves to '{sym_target}' "
                        f"outside target_root '{resolved_root}'."
                    )
                    raise SecurityValidationError(msg)
            except Exception as err:
                msg = f"Symlink resolution error on '{curr}': {err}"
                raise SecurityValidationError(msg) from err
        curr = curr.parent

    # Inspect the candidate destination itself if it exists and is a symlink
    if candidate.is_symlink():
        try:
            sym_target = candidate.resolve()
            if not sym_target.is_relative_to(resolved_root):
                msg = (
                    f"Security violation: Symlink '{candidate}' points to '{sym_target}' "
                    f"outside target_root '{resolved_root}'."
                )
                raise SecurityValidationError(msg)
        except Exception as err:
            msg = f"Symlink resolution error on destination '{candidate}': {err}"
            raise SecurityValidationError(msg) from err

    return resolved_root, resolved_dest
