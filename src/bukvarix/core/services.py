"""Project services: find the project folder, read and write its config file, and the check that the installed CLI is
new enough for it.
"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path

import tomli_w
from packaging.version import Version
from platformdirs import user_config_dir, user_data_dir

from .errors import CliTooOld, DatabaseNotReady, ProjectNotInitialized
from .models import ProjectConfig
from .version import installed_version

# Derived at import time from this package's own name, so this module never
# needs template substitution: it just works under whatever package_name
# the project was generated with.
_PACKAGE_NAME = __name__.split(".")[0]
PROJECT_DIR_NAME = f".{_PACKAGE_NAME}"
CONFIG_FILE_NAME = "config.toml"


# --- project scaffolding (generic template shape, kept for fleet-wide
# consistency; bukvarix's actual database is NOT project-scoped, see
# resolve_db_path below) -----------------------------------------------


def find_project_root(start: Path) -> Path | None:
    current = start.resolve()
    for candidate in (current, *current.parents):
        if (candidate / PROJECT_DIR_NAME).is_dir():
            return candidate
    return None


def require_project_root(start: Path) -> Path:
    root = find_project_root(start)
    if root is None:
        raise ProjectNotInitialized(str(start))
    return root


def config_path(project_root: Path) -> Path:
    return project_root / PROJECT_DIR_NAME / CONFIG_FILE_NAME


def check_cli_compat(config: ProjectConfig) -> None:
    """Raise CliTooOld if this installed CLI is older than the project's `cli.min_version`.

    A project outlives any single CLI release; this is how it refuses to be
    driven by a CLI that predates a config field or command it relies on.
    Skipped for an uninstalled (bare source) run, which has no version, and
    for a build without a `v*` tag in reach (uv-dynamic-versioning's
    `0.0.0` fallback) — a dev checkout, not a release older than anything.
    """
    installed = installed_version()
    if installed is None or Version(installed).release == (0, 0, 0):
        return
    if Version(installed) < Version(config.cli.min_version):
        raise CliTooOld(installed, config.cli.min_version)


def load_config(project_root: Path, *, check_compat: bool = True) -> ProjectConfig:
    """Load and validate the project config, enforcing `cli.min_version` by default.

    `check_compat=False` reads the config without failing on a version
    mismatch; no command passes it today (`doctor` no longer checks the
    project config).
    """
    data = tomllib.loads(config_path(project_root).read_text())
    config = ProjectConfig.model_validate(data)
    if check_compat:
        check_cli_compat(config)
    return config


def save_config(project_root: Path, config: ProjectConfig) -> None:
    path = config_path(project_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(tomli_w.dumps(config.model_dump()))


def init_project(project_root: Path, name: str) -> ProjectConfig:
    config = ProjectConfig(name=name)
    save_config(project_root, config)
    return config


# --- database path resolution -------------------------------------------
#
# Unlike a typical project-scoped resource, bukvarix's keyword database is a
# single machine-wide file, not something that varies per working
# directory — so it is intentionally NOT part of .bukvarix/config.toml
# (that would force a `bukvarix init` before `bukvarix search` works
# anywhere, which regresses today's zero-setup usage). Resolution order:
#
#   1. BUKVARIX_DB_PATH env var — explicit override, works in any process
#      (a GUI app or a subprocess spawned by another tool won't see a value
#      only exported from ~/.zshrc, since that only loads for interactive
#      shells).
#   2. the user-level config file (`db set-path` writes it) — same "works
#      from any process" property as the env var, without needing it set in
#      every shell profile.
#   3. platformdirs' default per-user data location, for a fresh machine
#      with nothing configured yet.

_ENV_VAR = f"{_PACKAGE_NAME.upper()}_DB_PATH"


def user_config_path() -> Path:
    return Path(user_config_dir(_PACKAGE_NAME)) / CONFIG_FILE_NAME


def default_db_path() -> Path:
    return Path(user_data_dir(_PACKAGE_NAME)) / f"{_PACKAGE_NAME}.duckdb"


def get_user_db_path() -> Path | None:
    path = user_config_path()
    if not path.is_file():
        return None
    value = tomllib.loads(path.read_text()).get("db_path")
    return Path(value) if value else None


def set_user_db_path(db_path: Path) -> None:
    path = user_config_path()
    data = tomllib.loads(path.read_text()) if path.is_file() else {}
    data["db_path"] = str(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(tomli_w.dumps(data))


def resolve_db_path() -> Path:
    env = os.environ.get(_ENV_VAR)
    if env:
        return Path(env)
    return get_user_db_path() or default_db_path()


def require_db_ready(db_path: Path) -> None:
    if not db_path.exists():
        raise DatabaseNotReady(str(db_path))
