from __future__ import annotations

import os
import shlex
import subprocess
import sys
from pathlib import Path

HELPER = Path(__file__).parents[2] / "scripts" / "dependencies.sh"


def _executable(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"#!/usr/bin/env bash\n{body}\n")
    path.chmod(0o755)


def _write_pyvenv_cfg(venv: Path, version: str) -> None:
    venv.mkdir(parents=True, exist_ok=True)
    (venv / "pyvenv.cfg").write_text(
        f"home = /usr/bin\ninclude-system-site-packages = false\nversion = {version}\n"
    )


def _live_version() -> str:
    return ".".join(str(part) for part in sys.version_info[:3])


def _fake_venv(venv: Path) -> Path:
    """Build a venv whose python behaves like the real one except for pip installs."""
    python = venv / "bin" / "python"
    _executable(
        python,
        'if [ "${1:-}" = "-m" ] && [ "${2:-}" = "pip" ]; then\n'
        '  if [ "${FAIL_INSTALL:-0}" = "1" ]; then exit 42; fi\n'
        '  if [ -n "${INSTALL_LOG:-}" ]; then printf "install\\n" >> "$INSTALL_LOG"; fi\n'
        "  exit 0\n"
        "fi\n"
        f'exec {shlex.quote(sys.executable)} "$@"',
    )
    for tool in ("alembic", "pytest", "ruff", "mypy"):
        _executable(venv / "bin" / tool, "exit 0")
    _write_pyvenv_cfg(venv, _live_version())
    return python


def _run_helper(script: str, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    command = f"source {shlex.quote(str(HELPER))}\n{script}"
    return subprocess.run(
        ["bash", "-c", command],
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )


def test_backend_reinstalls_when_either_dependency_input_changes(
    tmp_path: Path,
) -> None:
    backend = tmp_path / "backend"
    venv = backend / ".venv"
    backend.mkdir()
    (backend / "pyproject.toml").write_text("[project]\nname='test'\n")
    (backend / "requirements.lock").write_text("example==1\n")
    install_log = tmp_path / "backend-installs"
    fake_python = _fake_venv(venv)

    env = {
        **os.environ,
        "INSTALL_LOG": str(install_log),
    }
    variables = f"""
SYSTEM_PYTHON={shlex.quote(sys.executable)}
BACKEND_DIR={shlex.quote(str(backend))}
BACKEND_VENV={shlex.quote(str(venv))}
BACKEND_PYTHON={shlex.quote(str(fake_python))}
log() {{ :; }}
require_command() {{ command -v "$1" >/dev/null; }}
ensure_backend_deps
"""

    first = _run_helper(variables, env)
    assert first.returncode == 0, first.stderr
    assert install_log.read_text().splitlines() == ["install"]

    unchanged = _run_helper(variables, env)
    assert unchanged.returncode == 0, unchanged.stderr
    assert install_log.read_text().splitlines() == ["install"]

    (backend / "pyproject.toml").write_text("[project]\nname='changed'\n")
    pyproject_changed = _run_helper(variables, env)
    assert pyproject_changed.returncode == 0, pyproject_changed.stderr
    assert install_log.read_text().splitlines() == ["install", "install"]

    (backend / "requirements.lock").write_text("example==2\n")
    lock_changed = _run_helper(variables, env)
    assert lock_changed.returncode == 0, lock_changed.stderr
    assert install_log.read_text().splitlines() == ["install", "install", "install"]


def test_failed_backend_install_does_not_advance_stamp(tmp_path: Path) -> None:
    backend = tmp_path / "backend"
    venv = backend / ".venv"
    backend.mkdir()
    (backend / "pyproject.toml").write_text("[project]\nname='test'\n")
    (backend / "requirements.lock").write_text("example==1\n")
    fake_python = _fake_venv(venv)

    variables = f"""
SYSTEM_PYTHON={shlex.quote(sys.executable)}
BACKEND_DIR={shlex.quote(str(backend))}
BACKEND_VENV={shlex.quote(str(venv))}
BACKEND_PYTHON={shlex.quote(str(fake_python))}
log() {{ :; }}
require_command() {{ command -v "$1" >/dev/null; }}
ensure_backend_deps
"""
    first = _run_helper(variables, os.environ.copy())
    assert first.returncode == 0, first.stderr
    stamp = venv / ".pcc-deps.sha256"
    original_stamp = stamp.read_text()

    (backend / "requirements.lock").write_text("example==2\n")
    failed = _run_helper(variables, {**os.environ, "FAIL_INSTALL": "1"})
    assert failed.returncode == 42
    assert stamp.read_text() == original_stamp


def test_backend_venv_is_rebuilt_when_the_recorded_python_version_moves(
    tmp_path: Path,
) -> None:
    """An OS python3 upgrade orphans site-packages; reinstalling cannot fix it."""
    backend = tmp_path / "backend"
    venv = backend / ".venv"
    backend.mkdir()
    (backend / "pyproject.toml").write_text("[project]\nname='test'\n")
    (backend / "requirements.lock").write_text("example==1\n")
    install_log = tmp_path / "backend-installs"
    fake_python = _fake_venv(venv)

    env = {**os.environ, "INSTALL_LOG": str(install_log)}
    variables = f"""
SYSTEM_PYTHON={shlex.quote(sys.executable)}
BACKEND_DIR={shlex.quote(str(backend))}
BACKEND_VENV={shlex.quote(str(venv))}
BACKEND_PYTHON={shlex.quote(str(fake_python))}
log() {{ :; }}
require_command() {{ command -v "$1" >/dev/null; }}
rebuild_backend_venv() {{
  printf 'rebuild\\n' >> "$INSTALL_LOG"
  rm -f "$BACKEND_VENV/.pcc-deps.sha256"
  printf 'version = {_live_version()}\\n' > "$BACKEND_VENV/pyvenv.cfg"
}}
ensure_backend_deps
"""

    first = _run_helper(variables, env)
    assert first.returncode == 0, first.stderr
    assert install_log.read_text().splitlines() == ["install"]

    # The venv still matches its interpreter, so nothing is rebuilt or reinstalled.
    unchanged = _run_helper(variables, env)
    assert unchanged.returncode == 0, unchanged.stderr
    assert install_log.read_text().splitlines() == ["install"]

    # Now the tree belongs to an interpreter that is no longer what bin/python is.
    _write_pyvenv_cfg(venv, "3.9.6")
    upgraded = _run_helper(variables, env)
    assert upgraded.returncode == 0, upgraded.stderr
    assert install_log.read_text().splitlines() == ["install", "rebuild", "install"]

    # A rebuilt venv is fresh again, so a further run is a no-op.
    settled = _run_helper(variables, env)
    assert settled.returncode == 0, settled.stderr
    assert install_log.read_text().splitlines() == ["install", "rebuild", "install"]


def test_backend_venv_is_rebuilt_when_pyvenv_cfg_is_unreadable(tmp_path: Path) -> None:
    backend = tmp_path / "backend"
    venv = backend / ".venv"
    backend.mkdir()
    (backend / "pyproject.toml").write_text("[project]\nname='test'\n")
    (backend / "requirements.lock").write_text("example==1\n")
    fake_python = _fake_venv(venv)
    (venv / "pyvenv.cfg").unlink()

    variables = f"""
BACKEND_VENV={shlex.quote(str(venv))}
BACKEND_PYTHON={shlex.quote(str(fake_python))}
declare -F backend_venv_matches_interpreter > /dev/null || exit 3
if backend_venv_matches_interpreter; then echo matches; else echo mismatch; fi
"""
    result = _run_helper(variables, os.environ.copy())
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "mismatch"


def test_rebuild_backend_venv_discards_the_existing_tree(tmp_path: Path) -> None:
    venv = tmp_path / "backend" / ".venv"
    stale = venv / "lib" / "python3.9" / "site-packages"
    stale.mkdir(parents=True)
    (venv / ".pcc-deps.sha256").write_text("stale-fingerprint\n")
    venv_log = tmp_path / "venv-calls"
    fake_system_python = tmp_path / "bin" / "python3"
    _executable(
        fake_system_python,
        'printf "%s\\n" "$*" >> "$VENV_LOG"\nmkdir -p "$3/bin"',
    )

    variables = f"""
SYSTEM_PYTHON={shlex.quote(str(fake_system_python))}
BACKEND_VENV={shlex.quote(str(venv))}
rebuild_backend_venv
"""
    result = _run_helper(variables, {**os.environ, "VENV_LOG": str(venv_log)})
    assert result.returncode == 0, result.stderr
    assert venv_log.read_text().splitlines() == [f"-m venv {venv}"]
    assert not stale.exists()
    assert not (venv / ".pcc-deps.sha256").exists()


def test_frontend_reinstalls_when_either_dependency_input_changes(
    tmp_path: Path,
) -> None:
    frontend = tmp_path / "frontend"
    node_modules = frontend / "node_modules"
    fake_bin = tmp_path / "bin"
    frontend.mkdir()
    node_modules.mkdir()
    (frontend / "package.json").write_text('{"name":"test"}\n')
    (frontend / "package-lock.json").write_text('{"lockfileVersion":3}\n')
    install_log = tmp_path / "frontend-installs"
    _executable(
        fake_bin / "npm",
        'printf "install\\n" >> "$INSTALL_LOG"\nmkdir -p node_modules',
    )
    env = {
        **os.environ,
        "INSTALL_LOG": str(install_log),
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
    }
    variables = f"""
SYSTEM_PYTHON={shlex.quote(sys.executable)}
FRONTEND_DIR={shlex.quote(str(frontend))}
log() {{ :; }}
require_command() {{ command -v "$1" >/dev/null; }}
ensure_frontend_deps
"""

    first = _run_helper(variables, env)
    assert first.returncode == 0, first.stderr
    assert install_log.read_text().splitlines() == ["install"]

    unchanged = _run_helper(variables, env)
    assert unchanged.returncode == 0, unchanged.stderr
    assert install_log.read_text().splitlines() == ["install"]

    (frontend / "package.json").write_text('{"name":"changed"}\n')
    package_changed = _run_helper(variables, env)
    assert package_changed.returncode == 0, package_changed.stderr
    assert install_log.read_text().splitlines() == ["install", "install"]

    (frontend / "package-lock.json").write_text('{"lockfileVersion":3,"changed":true}\n')
    lock_changed = _run_helper(variables, env)
    assert lock_changed.returncode == 0, lock_changed.stderr
    assert install_log.read_text().splitlines() == ["install", "install", "install"]
