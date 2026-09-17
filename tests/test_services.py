from __future__ import annotations

from pathlib import Path

import pytest

from bukvarix.core import services
from bukvarix.core.errors import CliTooOld, DatabaseNotReady, ProjectNotInitialized
from bukvarix.core.models import ProjectConfig

# --- project scaffolding (generic template shape) --------------------------


def test_find_project_root_walks_up(tmp_path: Path) -> None:
    services.init_project(tmp_path, "demo")
    nested = tmp_path / "a" / "b"
    nested.mkdir(parents=True)
    assert services.find_project_root(nested) == tmp_path.resolve()


def test_require_project_root_raises_when_absent(tmp_path: Path) -> None:
    with pytest.raises(ProjectNotInitialized):
        services.require_project_root(tmp_path)


def test_config_round_trip(tmp_path: Path) -> None:
    config = services.init_project(tmp_path, "demo")
    config.provider.name = "custom"
    services.save_config(tmp_path, config)
    assert services.load_config(tmp_path).provider.name == "custom"
    assert services.config_path(tmp_path).is_file()


def test_check_cli_compat(monkeypatch: pytest.MonkeyPatch) -> None:
    config = ProjectConfig(name="demo")
    config.cli.min_version = "1.2.0"

    monkeypatch.setattr(services, "installed_version", lambda: "1.2.0")
    services.check_cli_compat(config)

    monkeypatch.setattr(services, "installed_version", lambda: "1.1.9")
    with pytest.raises(CliTooOld) as excinfo:
        services.check_cli_compat(config)
    assert excinfo.value.exit_code == 6

    monkeypatch.setattr(services, "installed_version", lambda: None)  # bare source run: nothing to compare
    services.check_cli_compat(config)


# --- database path resolution ----------------------------------------------


def test_resolve_db_path_prefers_env_var(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(services, "user_config_path", lambda: tmp_path / "user-config-unused.toml")
    monkeypatch.setattr(services, "default_db_path", lambda: tmp_path / "default.duckdb")
    monkeypatch.setenv("BUKVARIX_DB_PATH", str(tmp_path / "from-env.duckdb"))
    assert services.resolve_db_path() == tmp_path / "from-env.duckdb"


def test_resolve_db_path_falls_back_to_user_config_then_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("BUKVARIX_DB_PATH", raising=False)
    monkeypatch.setattr(services, "user_config_path", lambda: tmp_path / "config.toml")
    monkeypatch.setattr(services, "default_db_path", lambda: tmp_path / "default.duckdb")

    assert services.resolve_db_path() == tmp_path / "default.duckdb"

    services.set_user_db_path(tmp_path / "persisted.duckdb")
    assert services.get_user_db_path() == tmp_path / "persisted.duckdb"
    assert services.resolve_db_path() == tmp_path / "persisted.duckdb"


def test_set_user_db_path_preserves_other_keys(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text('other_key = "keep-me"\n')
    monkeypatch.setattr(services, "user_config_path", lambda: config_path)

    services.set_user_db_path(tmp_path / "db.duckdb")

    import tomllib

    data = tomllib.loads(config_path.read_text())
    assert data["other_key"] == "keep-me"
    assert data["db_path"] == str(tmp_path / "db.duckdb")


def test_require_db_ready(tmp_path: Path) -> None:
    missing = tmp_path / "nope.duckdb"
    with pytest.raises(DatabaseNotReady):
        services.require_db_ready(missing)

    present = tmp_path / "here.duckdb"
    present.touch()
    services.require_db_ready(present)  # does not raise
