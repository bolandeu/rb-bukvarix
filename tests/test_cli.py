"""Tests of the CLI as a whole: the distribution name, root and group help, the output layer's errors and exit codes,
and the built-in commands.
"""

from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

import pytest
import typer
import yaml
from typer.testing import CliRunner

from bukvarix import output
from bukvarix._distribution import DIST_NAME
from bukvarix.cli import app
from bukvarix.core import errors, services
from bukvarix.core.errors import InvalidArguments

runner = CliRunner()


def invoke(*args: str, **kwargs):
    return runner.invoke(app, list(args), **kwargs)


def plain(text: str) -> str:
    """The text without ANSI colour codes: CI forces Rich colours the Mac does not."""
    return re.sub(r"\x1b\[[0-9;]*m", "", text)


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


# --- names ----------------------------------------------------------------------


def test_distribution_name_is_declared_once() -> None:
    """Only _distribution.py assigns DIST_NAME; a merge that re-declares it elsewhere would silently win."""
    src = Path(__file__).resolve().parents[1] / "src" / "bukvarix"
    declaring = sorted(
        str(path.relative_to(src))
        for path in src.rglob("*.py")
        if re.search(r"^DIST_NAME\s*[:=]", path.read_text(), re.M)
    )
    assert declaring == ["_distribution.py"]


def test_version_falls_back_to_legacy_distribution_name(monkeypatch: pytest.MonkeyPatch) -> None:
    from bukvarix.core import version

    installed = version.installed_version()
    monkeypatch.setattr(version, "DIST_NAME", "no-such-distribution-xyz")
    monkeypatch.setattr(version, "DIST_NAME_LEGACY", DIST_NAME)  # an install made under the old name
    assert version.installed_version() == installed
    monkeypatch.setattr(version, "DIST_NAME_LEGACY", None)
    assert version.installed_version() is None


def test_distribution_name_matches_pyproject() -> None:
    """_distribution.DIST_NAME is how importlib.metadata finds this package; it must equal [project] name."""
    pyproject = tomllib.loads((Path(__file__).resolve().parents[1] / "pyproject.toml").read_text())
    assert pyproject["project"]["name"] == DIST_NAME


def test_install_section_matches_copier_answers() -> None:
    """[tool.rb.install] is rendered from the copier answers: users split on commas, mac as a bool.

    extras is not a copier answer: a hand-added one only has to be a list of strings.
    """
    root = Path(__file__).resolve().parents[1]
    install = tomllib.loads((root / "pyproject.toml").read_text())["tool"]["rb"]["install"]
    answers = yaml.safe_load((root / ".copier-answers.yml").read_text())
    users = [name.strip() for name in answers["install_homeserver_users"].split(",") if name.strip()]
    assert install["homeserver"] == users
    assert install["mac"] is answers["install_mac"]
    extras = install.get("extras", [])
    assert isinstance(extras, list) and all(isinstance(extra, str) for extra in extras)


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
    # Pin the installed version: an untagged checkout builds as 0.0.0, which the gate skips.
    monkeypatch.setattr(services, "installed_version", lambda: "1.0.0")
    init(tmp_path)
    assert invoke("--project", str(tmp_path), "config", "set", "cli.min_version", "99.0.0").exit_code == 0

    code, payload = invoke_json("--project", str(tmp_path), "status")
    assert code == 6
    assert payload["error"]["code"] == "CLI_TOO_OLD"

    monkeypatch.setattr(services, "installed_version", lambda: "99.0.0")
    code, _ = invoke_json("--project", str(tmp_path), "status")
    assert code == 0


def test_init_help_names_the_project_folder_by_the_package() -> None:
    result = invoke("init", "--help")
    assert result.exit_code == 0
    assert ".bukvarix/config.toml" in result.output


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


def test_doctor_json() -> None:
    code, payload = invoke_json("doctor")
    assert code == 0
    names = [check["name"] for check in payload["checks"]]
    assert names[:2] == ["python", "cli_version"]
    assert payload["healthy"] is all(check["ok"] for check in payload["checks"])


def test_doctor_prints_one_line_per_check_for_a_human() -> None:
    result = invoke("doctor")
    assert result.exit_code == 0
    assert plain(result.stdout).splitlines()[0] == "✓ python"


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


def test_error_message_and_action_in_square_brackets_reach_stderr_whole() -> None:
    def fail(ctx: typer.Context) -> None:
        raise errors.CliError("PATH_NOT_FOUND", "[library] path not found", action="use [a-z0-9-]")

    result = runner.invoke(_app_with(fail), ["cmd"])
    assert result.exit_code == 1
    assert "[library] path not found" in plain(result.stderr)
    assert "use [a-z0-9-]" in plain(result.stderr)


def test_verbose_line_in_square_brackets_reaches_stderr_whole() -> None:
    def progress(ctx: typer.Context) -> None:
        output.vecho(ctx, "step [1/3] of [a-z0-9-]")

    result = runner.invoke(_app_with(progress), ["--verbose", "cmd"])
    assert "step [1/3] of [a-z0-9-]" in plain(result.stderr)


@pytest.mark.parametrize(
    ("error", "code", "exit_code"),
    [
        (errors.InvalidArguments("bad"), "INVALID_ARGUMENTS", 2),
        (errors.AuthRequired("demo"), "AUTH_REQUIRED", 3),
        (errors.CredentialsUnhealthy({"demo": "invalid"}), "CREDENTIALS_UNHEALTHY", 3),
        (errors.ProjectNotInitialized("/work"), "PROJECT_NOT_INITIALIZED", 4),
        (errors.ProviderError("demo", "down", status=503), "PROVIDER_ERROR", 5),
        (errors.DatabaseUnavailable("down"), "DATABASE_UNAVAILABLE", 5),
        (errors.CliTooOld("1.0.0", "2.0.0"), "CLI_TOO_OLD", 6),
        (errors.DatabaseTooNew("0009"), "CLI_TOO_OLD", 6),
        (errors.DatabaseNotMigrated(None, "0002"), "DATABASE_NOT_MIGRATED", 1),
        (errors.DependencyTooOld("serp", None, "0.3"), "DEPENDENCY_TOO_OLD", 1),
        (errors.CredentialStoreUnavailable("demo", "locked"), "CREDENTIAL_STORE_UNAVAILABLE", 1),
        (errors.Aborted("Delete everything"), "ABORTED", 1),
    ],
    ids=lambda value: type(value).__name__ if isinstance(value, errors.CliError) else None,
)
def test_each_error_has_the_code_and_exit_code_of_the_standard(
    error: errors.CliError, code: str, exit_code: int
) -> None:
    """The error codes table of the Standard (Errors): the code in the envelope and the exit code."""
    payload = error.to_dict()
    assert (payload["code"], error.exit_code) == (code, exit_code)
    assert payload["message"]


def test_report_with_a_problem_keeps_the_payload() -> None:
    """output.emit_report: data plus ok=false + error, exit code from the problem (auth status uses it)."""

    def report(ctx: typer.Context) -> None:
        render = lambda d: output.human.echo(f"items: {d['items']}")  # noqa: E731
        output.emit_report(ctx, {"items": [1]}, render, InvalidArguments("bad"))

    result = runner.invoke(_app_with(report), ["--json", "cmd"])
    payload = json.loads(result.stdout)
    assert (result.exit_code, payload["ok"], payload["items"], payload["error"]["code"]) == (
        2,
        False,
        [1],
        "INVALID_ARGUMENTS",
    )
    result = runner.invoke(_app_with(report), ["cmd"])
    assert result.exit_code == 2
    assert "items: [1]" in plain(result.stdout)
    assert "bad" in result.stderr


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


def test_unexpected_exception_prints_traceback_only_with_verbose() -> None:
    def boom(ctx: typer.Context) -> None:
        raise RuntimeError("kaboom")

    quiet = runner.invoke(_app_with(boom), ["cmd"])
    assert quiet.exit_code == 1
    assert "Traceback" not in quiet.stderr
    assert "kaboom" in quiet.stderr  # human error goes to stderr, not stdout
    assert quiet.stdout == ""

    loud = runner.invoke(_app_with(boom), ["--verbose", "cmd"])
    assert "Traceback" in loud.stderr or "RuntimeError" in loud.stderr


@pytest.mark.parametrize(
    "args",
    [
        ("doctor", "--json", "--no-such-option"),
        ("--json", "--no-such-option"),
        ("--json", "auth", "status", "--provider"),
    ],
)
def test_usage_error_under_json_is_the_error_envelope(args: tuple[str, ...]) -> None:
    """An unknown option or a missing value (Click's usage errors) under --json: the envelope on stdout, exit 2."""
    result = invoke(*args)
    assert result.exit_code == 2
    payload = json.loads(result.stdout)
    assert (payload["ok"], payload["error"]["code"]) == (False, "INVALID_ARGUMENTS")
    assert payload["error"]["message"]


def test_usage_error_without_json_stays_on_stderr() -> None:
    result = invoke("doctor", "--no-such-option")
    assert result.exit_code == 2
    assert result.stdout == ""
    assert "--no-such-option" in plain(result.stderr)


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


def test_declined_confirmation_is_a_structured_abort() -> None:
    def destroy(ctx: typer.Context) -> None:
        output.require_confirmation(ctx, "Delete everything", confirm=False)
        raise AssertionError("must not get here")

    result = runner.invoke(_app_with(destroy), ["--json", "cmd"], input="n\n")
    assert result.exit_code == 1
    # The prompt itself precedes the envelope on stdout; the envelope is the last line.
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    assert payload["error"]["code"] == "ABORTED"


# --- skill install -----------------------------------------------------------


def test_skill_install_writes_to_project_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    code, payload = invoke_json("skill", "install", "--tool", "claude-code", "--project")
    assert code == 0
    installed = tmp_path / ".claude" / "skills" / "bukvarix" / "SKILL.md"
    assert installed.is_file()
    assert "name: bukvarix" in installed.read_text()
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


# --- freq: batch, provenance (source/region/data_date) ------------------------


def _freq_db(db_path: Path, small_tsv: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BUKVARIX_DB_PATH", str(db_path))
    code, _ = invoke_json("db", "import-keywords", str(small_tsv), "--data-date", "2026-08-01", "--confirm")
    assert code == 0


def test_freq_batch_envelope_keeps_input_order_and_reports_misses(
    small_tsv: Path, db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _freq_db(db_path, small_tsv, monkeypatch)
    code, payload = invoke_json("freq", "авто купить", "нет такой фразы", "продажа авто")
    assert code == 0
    assert payload["ok"] is True
    assert (payload["count"], payload["region"], payload["source"], payload["data_date"]) == (
        3,
        "213",
        "bukvarix",
        "2026-08-01",
    )
    assert payload["results"] == [
        {"query": "авто купить", "phrase": "купить авто", "freq_msk_exact": 300, "matched_via": "exact"},
        {"query": "нет такой фразы", "phrase": None, "freq_msk_exact": 0, "matched_via": None},
        {"query": "продажа авто", "phrase": "продажа авто", "freq_msk_exact": 500, "matched_via": "exact"},
    ]


def test_freq_single_phrase_uses_the_same_envelope(
    small_tsv: Path, db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _freq_db(db_path, small_tsv, monkeypatch)
    code, payload = invoke_json("freq", "купить авто")
    assert code == 0
    assert payload["count"] == 1
    assert payload["results"] == [
        {"query": "купить авто", "phrase": "купить авто", "freq_msk_exact": 300, "matched_via": "exact"}
    ]


def test_freq_reads_phrases_from_file_and_stdin(
    small_tsv: Path, db_path: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _freq_db(db_path, small_tsv, monkeypatch)
    phrases = tmp_path / "phrases.txt"
    phrases.write_text("купить авто\n\n  продажа авто  \n", encoding="utf-8")

    code, payload = invoke_json("freq", "--input", str(phrases))
    assert code == 0
    assert [r["query"] for r in payload["results"]] == ["купить авто", "продажа авто"]

    code, payload = invoke_json("freq", "растаможка авто", "--input", "-", input="купить авто\n")
    assert code == 0
    assert [r["query"] for r in payload["results"]] == ["растаможка авто", "купить авто"]


def test_freq_without_phrases_is_invalid_arguments(imported_db: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BUKVARIX_DB_PATH", str(imported_db))
    code, payload = invoke_json("freq")
    assert (code, payload["error"]["code"]) == (2, "INVALID_ARGUMENTS")
    code, payload = invoke_json("freq", "--input", "-", input="\n  \n")
    assert (code, payload["error"]["code"]) == (2, "INVALID_ARGUMENTS")
    code, payload = invoke_json("freq", "--input", "/no/such/phrases.txt")
    assert (code, payload["error"]["code"]) == (2, "INVALID_ARGUMENTS")


def test_freq_data_date_is_null_for_an_old_database(imported_db: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from bukvarix.core import db as bukvarix_db

    con = bukvarix_db.connect(imported_db)
    con.execute("DROP TABLE dataset_meta")
    con.close()
    monkeypatch.setenv("BUKVARIX_DB_PATH", str(imported_db))
    code, payload = invoke_json("freq", "купить авто")
    assert code == 0
    assert payload["data_date"] is None


def test_db_import_rejects_a_malformed_data_date(small_tsv: Path) -> None:
    code, payload = invoke_json("db", "import-keywords", str(small_tsv), "--data-date", "01.08.2026", "--confirm")
    assert (code, payload["error"]["code"]) == (2, "INVALID_ARGUMENTS")


def test_db_set_data_date_on_an_old_database_then_freq_reports_it(
    imported_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from bukvarix.core import db as bukvarix_db

    con = bukvarix_db.connect(imported_db)
    con.execute("DROP TABLE dataset_meta")
    con.close()
    monkeypatch.setenv("BUKVARIX_DB_PATH", str(imported_db))

    code, payload = invoke_json("db", "set-data-date", "2026-08-01", "--confirm")
    assert code == 0
    assert (payload["data_date"], payload["previous_data_date"]) == ("2026-08-01", None)
    _, payload = invoke_json("freq", "купить авто")
    assert payload["data_date"] == "2026-08-01"

    code, payload = invoke_json("db", "set-data-date", "2026-09-01", "--confirm")
    assert code == 0
    assert (payload["data_date"], payload["previous_data_date"]) == ("2026-09-01", "2026-08-01")
    _, payload = invoke_json("freq", "купить авто")
    assert payload["data_date"] == "2026-09-01"


def test_db_set_data_date_rejects_a_malformed_date(imported_db: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BUKVARIX_DB_PATH", str(imported_db))
    for bad in ("01.08.2026", "2026-13-01", "вчера"):
        code, payload = invoke_json("db", "set-data-date", bad, "--confirm")
        assert (code, payload["error"]["code"]) == (2, "INVALID_ARGUMENTS")


def test_db_set_data_date_gating_and_dry_run(imported_db: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BUKVARIX_DB_PATH", str(imported_db))
    code, payload = invoke_json("--non-interactive", "db", "set-data-date", "2026-09-01")
    assert (code, payload["error"]["code"]) == (2, "INVALID_ARGUMENTS")  # needs --confirm

    code, payload = invoke_json("db", "set-data-date", "2026-09-01", "--dry-run")
    assert code == 0
    assert payload["db_path"] == str(imported_db)
    _, payload = invoke_json("freq", "купить авто")
    assert payload["data_date"] != "2026-09-01"  # dry run wrote nothing


def test_db_set_data_date_requires_a_database() -> None:
    code, payload = invoke_json("db", "set-data-date", "2026-08-01", "--confirm")
    assert (code, payload["error"]["code"]) == (1, "DATABASE_NOT_READY")


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
    assert payload["results"][0]["phrase"] == "купить авто"

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
    assert payload["results"][0]["matched_via"] == "lemma"


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


# --- auth status without credentials (the Standard, Health: every CLI has it) --


def test_auth_status_without_credentials_is_empty_and_healthy() -> None:
    for args in (("auth", "status"), ("auth", "status", "--check")):
        code, payload = invoke_json(*args)
        assert code == 0
        assert list(payload) == ["ok", "tool", "version", "checked_at", "credentials"]
        assert payload["credentials"] == []
        assert payload["checked_at"].endswith("Z")
    code, payload = invoke_json("auth", "status", "--provider", "x")
    assert (code, payload["error"]["code"]) == (2, "INVALID_ARGUMENTS")
    assert invoke("auth", "login").exit_code == 2  # no such command without credentials
    assert "stores no credentials" in invoke("auth", "status").stdout
