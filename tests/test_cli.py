from __future__ import annotations

import json
from pathlib import Path

import pytest
import typer
from typer.testing import CliRunner

from bukvarix import output
from bukvarix.cli import app
from bukvarix.core import services

runner = CliRunner()


def invoke(*args: str, **kwargs):
    return runner.invoke(app, list(args), **kwargs)


def invoke_json(*args: str, **kwargs) -> tuple[int, dict]:
    """Run a command with --json and parse stdout — the contract agents rely on."""
    result = invoke(*args, "--json", **kwargs)
    assert result.stdout.strip(), f"no stdout for {args!r}; stderr: {result.stderr}"
    return result.exit_code, json.loads(result.stdout)


def init(tmp_path: Path, name: str = "demo") -> None:
    assert invoke("--project", str(tmp_path), "init", "--name", name).exit_code == 0


@pytest.fixture(autouse=True)
def no_real_database(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Every test gets an isolated (initially missing) db path — never the
    real production database — unless it explicitly imports one.
    """
    monkeypatch.setenv("BUKVARIX_DB_PATH", str(tmp_path / "isolated.duckdb"))


# --- help-on-no-args -------------------------------------------------------


def test_bare_invocation_shows_help_and_exits_zero() -> None:
    result = invoke()
    assert result.exit_code == 0
    assert "Usage:" in result.output


@pytest.mark.parametrize("group", ["config", "skill", "auth", "db"])
def test_bare_group_shows_help_and_exits_zero(group: str) -> None:
    result = invoke(group)
    assert result.exit_code == 0
    assert "Usage:" in result.output
    assert "Missing command" not in result.output


# --- init / status / config (generic project scaffolding) ------------------


def test_init_and_status(tmp_path: Path) -> None:
    init(tmp_path)
    code, payload = invoke_json("--project", str(tmp_path), "status")
    assert code == 0
    assert payload["ok"] is True
    assert payload["project"] == "demo"


def test_status_without_init_fails(tmp_path: Path) -> None:
    code, payload = invoke_json("--project", str(tmp_path), "status")
    assert code == 4
    assert payload["ok"] is False
    assert payload["error"]["code"] == "PROJECT_NOT_INITIALIZED"
    assert "init" in payload["error"]["action"]


def test_config_get_set_round_trip(tmp_path: Path) -> None:
    init(tmp_path)
    assert invoke("--project", str(tmp_path), "config", "set", "provider.name", "custom").exit_code == 0
    code, payload = invoke_json("--project", str(tmp_path), "config", "get", "provider.name")
    assert code == 0
    assert payload["value"] == "custom"


def test_min_version_gate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    init(tmp_path)
    assert invoke("--project", str(tmp_path), "config", "set", "cli.min_version", "99.0.0").exit_code == 0

    code, payload = invoke_json("--project", str(tmp_path), "status")
    assert code == 6
    assert payload["error"]["code"] == "CLI_TOO_OLD"

    monkeypatch.setattr(services, "installed_version", lambda: "99.0.0")
    code, _ = invoke_json("--project", str(tmp_path), "status")
    assert code == 0


# --- doctor ------------------------------------------------------------------


def test_doctor_reports_missing_database(tmp_path: Path) -> None:
    code, payload = invoke_json("--project", str(tmp_path), "doctor")
    assert code == 0
    db_ready = next(c for c in payload["checks"] if c["name"] == "db_ready")
    assert db_ready["ok"] is False
    assert payload["healthy"] is False


def test_doctor_healthy_once_a_database_exists(imported_db: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BUKVARIX_DB_PATH", str(imported_db))
    code, payload = invoke_json("doctor")
    assert code == 0
    assert payload["healthy"] is True
    names = [c["name"] for c in payload["checks"]]
    assert "project" not in names  # bukvarix has no project concept — not reported


# --- error handling contract ---------------------------------------------------


def _app_with(command):
    """A throwaway app with the real ctx.obj plumbing, for exercising output.guard."""
    test_app = typer.Typer()

    @test_app.callback()
    def _root(
        ctx: typer.Context,
        json_output: bool = output.JsonOption,
        verbose: bool = output.VerboseOption,
        non_interactive: bool = output.NonInteractiveOption,
    ) -> None:
        ctx.ensure_object(dict)
        ctx.obj["json"] = json_output
        ctx.obj["verbose"] = verbose
        ctx.obj["non_interactive"] = non_interactive

    test_app.command("cmd")(output.guard(command))
    return test_app


def test_unexpected_exception_still_yields_json_envelope() -> None:
    def boom(ctx: typer.Context) -> None:
        raise RuntimeError("kaboom")

    result = runner.invoke(_app_with(boom), ["--json", "cmd"])
    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert payload["error"]["code"] == "UNEXPECTED"
    assert "kaboom" in payload["error"]["message"]
    assert "Traceback" not in result.stdout


def test_human_error_goes_to_stderr(tmp_path: Path) -> None:
    result = invoke("--project", str(tmp_path), "status")
    assert result.exit_code == 4
    assert result.stdout == ""
    assert "Error:" in result.stderr


def test_require_confirmation_blocks_non_interactive_without_confirm() -> None:
    def destroy(ctx: typer.Context, confirm: bool = output.ConfirmOption) -> None:
        output.require_confirmation(ctx, "Delete everything", confirm=confirm)
        print("destroyed")

    blocked = runner.invoke(_app_with(destroy), ["--json", "--non-interactive", "cmd"])
    assert blocked.exit_code == 2
    assert json.loads(blocked.stdout)["error"]["code"] == "INVALID_ARGUMENTS"

    allowed = runner.invoke(_app_with(destroy), ["--non-interactive", "cmd", "--confirm"])
    assert allowed.exit_code == 0
    assert "destroyed" in allowed.stdout


# --- skill install -----------------------------------------------------------


def test_skill_install_writes_to_project_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    code, payload = invoke_json("skill", "install", "--tool", "claude-code", "--project")
    assert code == 0
    installed = tmp_path / ".claude" / "skills" / "bukvarix" / "SKILL.md"
    assert installed.is_file()
    assert "bukvarix" in installed.read_text()
    assert payload["path"] == str(installed)


# --- stats/search/freq: no database needed to exist yet ---------------------


def test_stats_works_without_a_database() -> None:
    code, payload = invoke_json("stats")
    assert code == 0
    assert payload["keywords_rows"] == 0


def test_search_and_freq_fail_cleanly_without_a_database() -> None:
    code, payload = invoke_json("search", "растаможка авто")
    assert code == 1
    assert payload["error"]["code"] == "DATABASE_NOT_READY"

    code, payload = invoke_json("freq", "растаможка авто")
    assert code == 1
    assert payload["error"]["code"] == "DATABASE_NOT_READY"


def test_search_invalid_mode_is_rejected() -> None:
    code, payload = invoke_json("search", "авто", "--mode", "bogus")
    assert code == 2
    assert payload["error"]["code"] == "INVALID_ARGUMENTS"


# --- db group: end-to-end against a throwaway database ----------------------


def test_db_import_requires_confirm_or_dry_run(small_tsv: Path) -> None:
    result = invoke("db", "import-keywords", str(small_tsv), "--non-interactive")
    assert result.exit_code == 2  # INVALID_ARGUMENTS: needs --confirm

    code, payload = invoke_json("db", "import-keywords", str(small_tsv), "--dry-run")
    assert code == 0
    assert payload["would_import"] == str(small_tsv)


def test_db_import_then_search_and_freq_work(small_tsv: Path, db_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BUKVARIX_DB_PATH", str(db_path))

    code, payload = invoke_json("db", "import-keywords", str(small_tsv), "--confirm")
    assert code == 0
    assert payload["deduped_rows"] == 6
    assert payload["removed_permutations"] == 1

    code, payload = invoke_json("freq", "авто купить")
    assert code == 0
    assert payload["result"]["phrase"] == "купить авто"

    code, payload = invoke_json("search", "авто", "--limit", "3")
    assert code == 0
    assert payload["results"][0]["phrase"] == "продажа авто"


def test_db_set_path_persists_across_invocations(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BUKVARIX_DB_PATH", raising=False)
    fake_user_config = tmp_path / "user-config.toml"
    monkeypatch.setattr(services, "user_config_path", lambda: fake_user_config)

    target = tmp_path / "wherever.duckdb"
    code, payload = invoke_json("db", "set-path", str(target))
    assert code == 0
    assert payload["db_path"] == str(target.resolve())
    assert services.get_user_db_path() == target.resolve()


def test_db_normalize_and_reindex_require_a_database(tmp_path: Path) -> None:
    code, payload = invoke_json("db", "normalize", "--confirm")
    assert code == 1
    assert payload["error"]["code"] == "DATABASE_NOT_READY"

    code, payload = invoke_json("db", "reindex", "--confirm")
    assert code == 1
    assert payload["error"]["code"] == "DATABASE_NOT_READY"


def test_db_normalize_and_reindex_dry_run_and_gating(imported_db: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BUKVARIX_DB_PATH", str(imported_db))

    code, payload = invoke_json("db", "normalize", "--dry-run")
    assert code == 0
    assert payload["db_path"] == str(imported_db)
    code, payload = invoke_json("--non-interactive", "db", "normalize")
    assert code == 2  # needs --confirm

    code, payload = invoke_json("db", "reindex", "--dry-run")
    assert code == 0
    code, payload = invoke_json("--non-interactive", "db", "reindex")
    assert code == 2  # needs --confirm

    assert invoke("db", "normalize", "--confirm").exit_code == 0
    assert invoke("db", "reindex", "--confirm").exit_code == 0

    # normalize actually ran: freq now resolves via the lemma tier too.
    code, payload = invoke_json("freq", "растаможки авто")
    assert code == 0
    assert payload["result"]["matched_via"] == "lemma"


# --- human-mode rendering smoke tests ---------------------------------------


def test_search_freq_stats_doctor_human_mode_smoke(imported_db: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BUKVARIX_DB_PATH", str(imported_db))
    assert invoke("search", "авто", "--limit", "2").exit_code == 0
    assert invoke("search", "нет такой фразы вообще").exit_code == 0
    assert invoke("freq", "авто купить").exit_code == 0
    assert invoke("freq", "нет такой фразы вообще").exit_code == 0
    assert invoke("stats").exit_code == 0
    assert invoke("doctor").exit_code == 0


def test_search_csv_export(imported_db: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BUKVARIX_DB_PATH", str(imported_db))
    csv_path = tmp_path / "out.csv"
    code, payload = invoke_json("search", "авто", "--mode", "words", "--csv", str(csv_path))
    assert code == 0
    assert payload["saved_to"] == str(csv_path)
    assert csv_path.is_file()
