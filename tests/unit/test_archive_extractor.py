"""Security unit tests for SafeArchiveExtractor (ZipSlip, TarSlip, symlinks, zip bombs)."""

import io
import tarfile
import zipfile
from pathlib import Path

import pytest

from aiaddons.core.acquisition.extractor import SafeArchiveExtractor
from aiaddons.core.exceptions import ArchiveSecurityError


def test_zip_extractor_safe(tmp_path: Path) -> None:
    """Verify safe ZIP archive extraction."""
    archive_path = tmp_path / "safe.zip"
    staging_path = tmp_path / "staging"
    staging_path.mkdir()

    with zipfile.ZipFile(archive_path, "w") as zf:
        zf.writestr("SKILL.md", "# Test Skill\n")
        zf.writestr("sub/helper.py", "print('hello')\n")

    extractor = SafeArchiveExtractor()
    extracted = extractor.extract_zip(archive_path, staging_path)

    assert "SKILL.md" in extracted
    assert "sub/helper.py" in extracted
    assert (staging_path / "SKILL.md").exists()
    assert (staging_path / "sub/helper.py").exists()


def test_zip_extractor_zip_slip_relative_blocked(tmp_path: Path) -> None:
    """Verify ZIP containing ../ path traversal is blocked."""
    archive_path = tmp_path / "slip.zip"
    staging_path = tmp_path / "staging"
    staging_path.mkdir()

    with zipfile.ZipFile(archive_path, "w") as zf:
        zf.writestr("../escaped.txt", "pwned")

    extractor = SafeArchiveExtractor()
    with pytest.raises(ArchiveSecurityError, match="ZipSlip traversal violation"):
        extractor.extract_zip(archive_path, staging_path)

    assert not (tmp_path / "escaped.txt").exists()


def test_zip_extractor_zip_slip_absolute_blocked(tmp_path: Path) -> None:
    """Verify ZIP containing absolute path is blocked."""
    archive_path = tmp_path / "abs_slip.zip"
    staging_path = tmp_path / "staging"

    with zipfile.ZipFile(archive_path, "w") as zf:
        zf.writestr("/etc/passwd", "pwned")

    extractor = SafeArchiveExtractor()
    with pytest.raises(ArchiveSecurityError, match="ZipSlip traversal violation"):
        extractor.extract_zip(archive_path, staging_path)


def test_zip_extractor_windows_drive_blocked(tmp_path: Path) -> None:
    """Verify ZIP containing Windows drive letter is blocked."""
    archive_path = tmp_path / "win_drive.zip"
    staging_path = tmp_path / "staging"

    with zipfile.ZipFile(archive_path, "w") as zf:
        zf.writestr("C:/Windows/System32/evil.dll", "pwned")

    extractor = SafeArchiveExtractor()
    with pytest.raises(ArchiveSecurityError, match="ZipSlip traversal violation"):
        extractor.extract_zip(archive_path, staging_path)


def test_zip_extractor_unc_path_blocked(tmp_path: Path) -> None:
    """Verify ZIP containing UNC network path is blocked."""
    archive_path = tmp_path / "unc.zip"
    staging_path = tmp_path / "staging"

    with zipfile.ZipFile(archive_path, "w") as zf:
        zf.writestr("//server/share/evil.exe", "pwned")

    extractor = SafeArchiveExtractor()
    with pytest.raises(ArchiveSecurityError, match="ZipSlip traversal violation"):
        extractor.extract_zip(archive_path, staging_path)


def test_zip_extractor_null_byte_blocked(tmp_path: Path) -> None:
    """Verify ZIP member with null byte is rejected."""
    extractor = SafeArchiveExtractor()
    zinfo = zipfile.ZipInfo()
    zinfo.filename = "file.txt\0.exe"
    with pytest.raises(ArchiveSecurityError, match="ZipSlip traversal violation"):
        extractor._validate_zip_member(zinfo, tmp_path)


def test_zip_extractor_file_count_cap(tmp_path: Path) -> None:
    """Verify ZIP exceeding file count cap raises ArchiveSecurityError."""
    archive_path = tmp_path / "many_files.zip"
    staging_path = tmp_path / "staging"

    with zipfile.ZipFile(archive_path, "w") as zf:
        for i in range(15):
            zf.writestr(f"file_{i}.txt", "data")

    extractor = SafeArchiveExtractor(max_file_count=10)
    with pytest.raises(ArchiveSecurityError, match="exceeding maximum limit"):
        extractor.extract_zip(archive_path, staging_path)


def test_zip_extractor_uncompressed_size_cap(tmp_path: Path) -> None:
    """Verify ZIP exceeding total uncompressed size cap raises ArchiveSecurityError."""
    archive_path = tmp_path / "large.zip"
    staging_path = tmp_path / "staging"

    with zipfile.ZipFile(archive_path, "w") as zf:
        zf.writestr("big.txt", "A" * 2000)

    extractor = SafeArchiveExtractor(max_total_size=1000)
    with pytest.raises(ArchiveSecurityError, match="total uncompressed size"):
        extractor.extract_zip(archive_path, staging_path)


def test_tar_extractor_safe(tmp_path: Path) -> None:
    """Verify safe TAR archive extraction."""
    archive_path = tmp_path / "safe.tar.gz"
    staging_path = tmp_path / "staging"
    staging_path.mkdir()

    with tarfile.open(name=archive_path, mode="w:gz") as tf:
        content = b"header data\n"
        tinfo = tarfile.TarInfo(name="header.h")
        tinfo.size = len(content)
        tf.addfile(tinfo, io.BytesIO(content))

    extractor = SafeArchiveExtractor()
    extracted = extractor.extract_tar(archive_path, staging_path)

    assert "header.h" in extracted
    assert (staging_path / "header.h").exists()


def test_tar_extractor_tarslip_blocked(tmp_path: Path) -> None:
    """Verify TAR containing TarSlip traversal is blocked."""
    archive_path = tmp_path / "slip.tar"
    staging_path = tmp_path / "staging"

    with tarfile.open(name=archive_path, mode="w") as tf:
        content = b"evil"
        tinfo = tarfile.TarInfo(name="../etc/passwd")
        tinfo.size = len(content)
        tf.addfile(tinfo, io.BytesIO(content))

    extractor = SafeArchiveExtractor()
    with pytest.raises(ArchiveSecurityError, match="TarSlip traversal violation"):
        extractor.extract_tar(archive_path, staging_path)


def test_tar_extractor_symlink_blocked(tmp_path: Path) -> None:
    """Verify TAR containing symlink is blocked."""
    archive_path = tmp_path / "sym.tar"
    staging_path = tmp_path / "staging"

    with tarfile.open(name=archive_path, mode="w") as tf:
        tinfo = tarfile.TarInfo(name="link_to_passwd")
        tinfo.type = tarfile.SYMTYPE
        tinfo.linkname = "target.txt"
        tf.addfile(tinfo)

    extractor = SafeArchiveExtractor()
    with pytest.raises(ArchiveSecurityError, match="symlink/hardlink"):
        extractor.extract_tar(archive_path, staging_path)


def test_unknown_archive_format_fails_closed(tmp_path: Path) -> None:
    """Verify unknown file extension fails closed."""
    archive_path = tmp_path / "unknown.rar"
    archive_path.write_bytes(b"dummy")

    extractor = SafeArchiveExtractor()
    with pytest.raises(ArchiveSecurityError, match="Unsupported or unsafe archive format"):
        extractor.extract(archive_path, tmp_path / "staging")
