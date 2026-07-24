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
    fake_python = venv / "bin" / "python"
    _executable(
        fake_python,
        'if [ "${FAIL_INSTALL:-0}" = "1" ]; then exit 42; fi\n'
        'printf "install\\n" >> "$INSTALL_LOG"',
    )
    for tool in ("alembic", "pytest", "ruff", "mypy"):
        _executable(venv / "bin" / tool, "exit 0")

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
    fake_python = venv / "bin" / "python"
    _executable(
        fake_python,
        'if [ "${FAIL_INSTALL:-0}" = "1" ]; then exit 42; fi\nexit 0',
    )
    for tool in ("alembic", "pytest", "ruff", "mypy"):
        _executable(venv / "bin" / tool, "exit 0")

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
