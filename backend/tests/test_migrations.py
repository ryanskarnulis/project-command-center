import os
import subprocess
import sys
from pathlib import Path

from sqlalchemy import create_engine, inspect

# backend/ — where alembic.ini lives and the alembic CLI must run from.
BACKEND_DIR = Path(__file__).resolve().parents[1]


def test_alembic_upgrade_head(tmp_path: Path) -> None:
    """A real `alembic upgrade head` against a fresh SQLite file must succeed.

    The rest of the suite builds the schema with Base.metadata.create_all and so
    never exercises the migration chain. This guards that the chain itself applies
    cleanly from empty — the same thing a real deployment does.
    """
    db_path = tmp_path / "migration_smoke.db"

    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=BACKEND_DIR,
        env={**os.environ, "DATABASE_URL": f"sqlite:///{db_path}"},
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, (
        "alembic upgrade head failed:\n"
        f"--- stdout ---\n{result.stdout}\n"
        f"--- stderr ---\n{result.stderr}"
    )

    # Prove the upgrade actually built schema rather than no-opping.
    engine = create_engine(f"sqlite:///{db_path}")
    try:
        tables = set(inspect(engine).get_table_names())
    finally:
        engine.dispose()

    assert {"projects", "tasks"} <= tables, f"expected core tables, got: {sorted(tables)}"
