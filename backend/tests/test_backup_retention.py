"""Shell-level regression for scripts/backup_retention.sh.

Regression for #174: a negative BACKUP_RETENTION_DAYS made GNU find's
`-mtime +N` match every snapshot, so a successful backup run deleted its own
output. These tests drive the helper against a throwaway directory only — they
never touch the real data/ tree.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
HELPER = REPO_ROOT / "scripts" / "backup_retention.sh"

pytestmark = pytest.mark.skipif(
    shutil.which("bash") is None, reason="bash is required for the backup shell tests"
)


def run_prune(backup_dir: Path, retention_days: str) -> subprocess.CompletedProcess[str]:
    """Source the helper and prune ``backup_dir`` with the given retention value."""
    script = f'source "{HELPER}"\nprune_backups "$1" "$2"\n'
    return subprocess.run(
        ["bash", "-c", script, "bash", str(backup_dir), retention_days],
        capture_output=True,
        text=True,
        check=False,
    )


def make_snapshots(backup_dir: Path) -> list[Path]:
    """One snapshot aged 30 days, one written just now."""
    old = backup_dir / "app-20250101-000000.db"
    new = backup_dir / "app-20260101-000000.db"
    old.write_text("old")
    new.write_text("new")
    thirty_days_ago = time.time() - 30 * 86400
    os.utime(old, (thirty_days_ago, thirty_days_ago))
    return [old, new]


@pytest.mark.parametrize("value", ["-1", "-14", "1.5", "abc", "", " 7", "+7", "7d"])
def test_invalid_retention_deletes_nothing(tmp_path: Path, value: str) -> None:
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    snapshots = make_snapshots(backup_dir)

    result = run_prune(backup_dir, value)

    assert result.returncode != 0
    assert "BACKUP_RETENTION_DAYS must be a non-negative integer" in result.stderr
    assert all(path.exists() for path in snapshots)


def test_positive_retention_prunes_only_old_snapshots(tmp_path: Path) -> None:
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    old, new = make_snapshots(backup_dir)

    result = run_prune(backup_dir, "14")

    assert result.returncode == 0, result.stderr
    assert not old.exists()
    assert new.exists()


def test_zero_retention_keeps_the_snapshot_written_today(tmp_path: Path) -> None:
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    old, new = make_snapshots(backup_dir)

    result = run_prune(backup_dir, "0")

    assert result.returncode == 0, result.stderr
    assert not old.exists()
    assert new.exists()


def test_non_snapshot_files_are_never_touched(tmp_path: Path) -> None:
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    make_snapshots(backup_dir)
    unrelated = backup_dir / "notes.txt"
    unrelated.write_text("keep me")
    ancient = time.time() - 365 * 86400
    os.utime(unrelated, (ancient, ancient))

    result = run_prune(backup_dir, "14")

    assert result.returncode == 0, result.stderr
    assert unrelated.exists()


def test_backup_db_rejects_invalid_retention_before_writing_anything(tmp_path: Path) -> None:
    """The real script must bail out before creating data/ or a snapshot."""
    fake_repo = tmp_path / "repo"
    (fake_repo / "scripts").mkdir(parents=True)
    for name in ("backup_db.sh", "backup_retention.sh"):
        shutil.copy(REPO_ROOT / "scripts" / name, fake_repo / "scripts" / name)
    (fake_repo / "data").mkdir()
    (fake_repo / "data" / "app.db").write_bytes(b"")

    result = subprocess.run(
        ["bash", str(fake_repo / "scripts" / "backup_db.sh")],
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "BACKUP_RETENTION_DAYS": "-1"},
    )

    assert result.returncode != 0
    assert "BACKUP_RETENTION_DAYS must be a non-negative integer" in result.stderr
    assert not (fake_repo / "data" / "backups").exists()
