"""Shell-level regression for the atomic snapshot write in scripts/backup_db.sh.

Regression for #263: the snapshot used to be written straight to its final
``app-<stamp>.db`` name, so an interrupted copy left a truncated file that the
retention pruner keeps and that a restore ("take the newest snapshot") happily
restores. The script now writes ``app-<stamp>.db.tmp.<pid>`` and renames it into
place only after the copy completes, with a trap that unlinks the temporary on
any early exit. These tests drive a throwaway copy of the scripts against a
scratch database — they never touch the real data/ tree.
"""

from __future__ import annotations

import fnmatch
import os
import shutil
import signal
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_NAMES = ("backup_db.sh", "backup_retention.sh")
ROW_COUNT = 2000

# What prune_backups and a restore treat as a finished snapshot, and the
# deliberately non-matching name an in-progress copy is written under.
SNAPSHOT_GLOB = "app-*.db"
TEMP_GLOB = "app-*.db.tmp.*"
ANY_FILE = "*"

pytestmark = pytest.mark.skipif(
    shutil.which("bash") is None, reason="bash is required for the backup shell tests"
)


def make_repo(tmp_path: Path) -> Path:
    """Build a throwaway checkout: the two scripts plus a real scratch database."""
    repo = tmp_path / "repo"
    (repo / "scripts").mkdir(parents=True)
    for name in SCRIPT_NAMES:
        shutil.copy(REPO_ROOT / "scripts" / name, repo / "scripts" / name)
    (repo / "data").mkdir()

    connection = sqlite3.connect(repo / "data" / "app.db")
    try:
        connection.execute("CREATE TABLE note (id INTEGER PRIMARY KEY, body TEXT)")
        connection.executemany(
            "INSERT INTO note (body) VALUES (?)",
            [(f"note-{index}",) for index in range(ROW_COUNT)],
        )
        connection.commit()
    finally:
        connection.close()
    return repo


def backup_dir(repo: Path) -> Path:
    return repo / "data" / "backups"


def entries(repo: Path, pattern: str) -> list[Path]:
    """Files in the backup directory whose name matches ``pattern``."""
    directory = backup_dir(repo)
    if not directory.is_dir():
        return []
    return sorted(path for path in directory.iterdir() if fnmatch.fnmatch(path.name, pattern))


def backup_command(repo: Path) -> list[str]:
    return ["bash", str(repo / "scripts" / "backup_db.sh")]


def backup_env(**overrides: str) -> dict[str, str]:
    """Pin the interpreter so a stray PYTHON in the ambient env cannot skew a run."""
    return {**os.environ, "PYTHON": sys.executable, **overrides}


def run_backup(repo: Path, **overrides: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        backup_command(repo),
        capture_output=True,
        text=True,
        check=False,
        env=backup_env(**overrides),
    )


def write_fake_python(tmp_path: Path, name: str, body: str) -> Path:
    """Write a stand-in for the interpreter that performs the snapshot.

    backup_db.sh invokes it as ``PYTHON - <source> <destination>`` with the
    snapshot program on stdin, so ``$3`` is the path the real interpreter would
    have written and the heredoc has to be drained.
    """
    script = tmp_path / name
    script.write_text(f"#!/usr/bin/env bash\ncat > /dev/null\n{body}\n")
    script.chmod(0o755)
    return script


def assert_readable_snapshot(snapshot: Path) -> None:
    connection = sqlite3.connect(snapshot)
    try:
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert connection.execute("SELECT count(*) FROM note").fetchone()[0] == ROW_COUNT
    finally:
        connection.close()


def test_successful_run_publishes_one_complete_snapshot(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)

    result = run_backup(repo)

    assert result.returncode == 0, result.stderr
    snapshots = entries(repo, SNAPSHOT_GLOB)
    assert len(snapshots) == 1, result.stdout
    assert entries(repo, ANY_FILE) == snapshots, "the run left a temporary behind"
    assert result.stdout.strip().endswith(snapshots[0].name)
    assert_readable_snapshot(snapshots[0])


def test_failed_snapshot_leaves_nothing_that_looks_like_a_backup(tmp_path: Path) -> None:
    """A copy that dies mid-write must not surface under the snapshot name."""
    repo = make_repo(tmp_path)
    broken = write_fake_python(
        tmp_path, "broken-python.sh", 'printf \'truncated\' > "$3"\nexit 1'
    )

    result = run_backup(repo, PYTHON=str(broken))

    assert result.returncode != 0
    assert entries(repo, SNAPSHOT_GLOB) == []
    assert entries(repo, ANY_FILE) == [], "the trap must unlink the partial snapshot"


def test_hard_kill_mid_write_never_publishes_a_snapshot(tmp_path: Path) -> None:
    """SIGKILL runs no trap; the partial then has to be invisible to the pruner."""
    repo = make_repo(tmp_path)
    stalled = write_fake_python(
        tmp_path, "stalled-python.sh", 'printf \'truncated\' > "$3"\nsleep 30'
    )

    process = subprocess.Popen(
        backup_command(repo),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=backup_env(PYTHON=str(stalled)),
        start_new_session=True,
    )
    try:
        deadline = time.monotonic() + 30.0
        while not entries(repo, TEMP_GLOB) and time.monotonic() < deadline:
            time.sleep(0.05)
        assert entries(repo, TEMP_GLOB), "the copy never started; nothing was interrupted"
        os.killpg(os.getpgid(process.pid), signal.SIGKILL)
    finally:
        process.wait(timeout=30)

    stranded = entries(repo, ANY_FILE)
    assert stranded, "expected the killed run's temporary to survive"
    assert entries(repo, SNAPSHOT_GLOB) == []
    assert all(not fnmatch.fnmatch(path.name, SNAPSHOT_GLOB) for path in stranded)


def test_stranded_temporaries_are_pruned_once_they_age_out(tmp_path: Path) -> None:
    """Temporaries sit outside the snapshot glob, so a later run has to sweep them."""
    repo = make_repo(tmp_path)
    backup_dir(repo).mkdir(parents=True)
    stranded = backup_dir(repo) / "app-20250101-000000.db.tmp.4242"
    stranded.write_text("truncated")
    thirty_days_ago = time.time() - 30 * 86400
    os.utime(stranded, (thirty_days_ago, thirty_days_ago))

    result = run_backup(repo)

    assert result.returncode == 0, result.stderr
    assert not stranded.exists()
    assert len(entries(repo, SNAPSHOT_GLOB)) == 1


def test_fresh_temporary_survives_the_sweep(tmp_path: Path) -> None:
    """The sweep must never take out a concurrent run's in-flight copy."""
    repo = make_repo(tmp_path)
    backup_dir(repo).mkdir(parents=True)
    in_flight = backup_dir(repo) / "app-20260101-000000.db.tmp.4242"
    in_flight.write_text("in progress")

    result = run_backup(repo, BACKUP_RETENTION_DAYS="0")

    assert result.returncode == 0, result.stderr
    assert in_flight.exists()
    assert len(entries(repo, SNAPSHOT_GLOB)) == 1
