"""--source api and `site`: the Bukvarix web API behind a fake urlopen — no network in the gate."""

from __future__ import annotations

import csv
import io
import json
import urllib.error
import urllib.parse
from pathlib import Path

import pytest

from rb_bukvarix.core import api

from .test_cli import invoke, invoke_json, plain

# keywords: phrase, words, chars, broad, !exact — shortest first, like the real API
_KEYWORDS = {
    "растаможка авто": "растаможка авто\t2\t15\t75991\t2492\nкалькулятор растаможки авто\t3\t27\t24211\t4918\n",
    "!авто !растаможка": "растаможка авто\t2\t15\t75991\t2492\n",
    "!купить !окна": "окна купить\t2\t11\t409181\t2938\nкупить окна\t2\t11\t411491\t2897\n",
}
# site: phrase, serp results, words, chars, broad, !exact, position
_SITE = "дром\t3000000\t1\t4\t4337381\t1061158\t1\nдром барнаул\t5000\t2\t12\t75683\t48618\t1\n"
# site_mcmp: ... + domains found, avg position, a position per domain (0 = not ranking)
_MCMP = "авто\t13000000\t1\t4\t16888468\t102234\t2\t1,5\t2\t1\nтойота\t1000\t1\t6\t6838268\t57031\t1\t5\t5\t0\r\n"


@pytest.fixture
def fake_api(monkeypatch: pytest.MonkeyPatch):
    """Serve canned TSV per endpoint; `calls` records each request, `errors` queues HTTP statuses to raise first."""
    calls: list[tuple[str, dict]] = []
    errors: list[int] = []
    sleeps: list[float] = []

    def urlopen(req, timeout=None):
        url = req.full_url
        endpoint = url.split("/v1/")[1].split("/")[0]
        query = req.data.decode() if req.data else url.split("?", 1)[1]
        params = {k: v[0] for k, v in urllib.parse.parse_qs(query).items()}
        calls.append((endpoint, params))
        if errors:
            raise urllib.error.HTTPError(url, errors.pop(0), "error", {}, None)  # ty: ignore[invalid-argument-type]
        if params.get("result_count") == "1":
            return io.BytesIO("﻿16373".encode())
        body = {"keywords": _KEYWORDS.get(params["q"], ""), "site": _SITE}.get(endpoint, _MCMP)
        return io.BytesIO(body.encode())

    monkeypatch.setattr(api.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(api.time, "sleep", sleeps.append)
    monkeypatch.setattr(api, "_last_call", {})
    monkeypatch.delenv(api.KEY_ENV, raising=False)
    return {"calls": calls, "errors": errors, "sleeps": sleeps}


def test_search_api_keeps_the_search_envelope_with_world_fields(fake_api) -> None:
    code, payload = invoke_json("search", "растаможка авто", "--source", "api", "--limit", "5")
    assert code == 0
    assert (payload["source"], payload["region"], payload["engine"]) == ("bukvarix-api", "world", "api")
    assert payload["self"] == {
        "phrase": "растаможка авто",
        "freq_world_exact": 2492,
        "freq_world_broad": 75991,
        "matched_via": "exact",
    }
    # sorted by exact frequency, not in the API's shortest-first order
    assert [r["phrase"] for r in payload["results"]] == ["калькулятор растаможки авто", "растаможка авто"]
    assert (payload["total_count"], payload["total_freq_sum"], payload["fetched"], payload["truncated"]) == (
        16373,
        None,
        2,
        True,
    )
    endpoint, params = fake_api["calls"][0]
    assert (endpoint, params["num"], params["api_key"], params["format"]) == ("keywords", "200", "free", "tsv")


def test_search_api_falls_back_to_a_lookup_for_self(fake_api) -> None:
    code, payload = invoke_json("search", "авто растаможка", "--source", "api")
    assert code == 0
    assert payload["self"]["phrase"] == "растаможка авто"
    assert [c[1]["q"] for c in fake_api["calls"]][-1] == "!авто !растаможка"


def test_search_api_self_is_null_with_an_excluded_word(fake_api) -> None:
    code, payload = invoke_json("search", "растаможка авто -калькулятор", "--source", "api")
    assert code == 0
    assert payload["self"] is None


def test_search_api_csv_and_human_output(fake_api, tmp_path: Path) -> None:
    out = tmp_path / "r.csv"
    code, payload = invoke_json("search", "растаможка авто", "--source", "api", "--csv", str(out))
    assert (code, payload["rows"]) == (0, 2)
    assert next(csv.reader(out.open(encoding="utf-8"))) == ["phrase", "freq_world_exact", "freq_world_broad"]
    result = invoke("search", "растаможка авто", "--source", "api")
    assert result.exit_code == 0
    assert "растаможка авто: 2492" in plain(result.stdout)


def test_search_api_rejects_mode_and_bad_values(fake_api) -> None:
    assert invoke_json("search", "x", "--source", "api", "--mode", "exact")[0] == 2
    assert invoke_json("search", "x", "--source", "web")[0] == 2
    assert invoke_json("search", "x", "--source", "api", "--limit", "0")[0] == 2
    assert fake_api["calls"] == []


def test_freq_api_same_envelope_world_fields_and_progress(fake_api) -> None:
    result = invoke("freq", "купить окна", "нет такой фразы", "--source", "api", "--json")
    assert result.exit_code == 0
    assert "[1/2] купить окна" in plain(result.stderr)
    payload = json.loads(result.stdout)
    assert (payload["count"], payload["region"], payload["source"], payload["data_date"]) == (
        2,
        "world",
        "bukvarix-api",
        None,
    )
    assert payload["results"] == [
        {
            "query": "купить окна",
            "phrase": "окна купить",
            "freq_world_exact": 2938,
            "freq_world_broad": 409181,
            "matched_via": "exact",
        },
        {
            "query": "нет такой фразы",
            "phrase": None,
            "freq_world_exact": 0,
            "freq_world_broad": 0,
            "matched_via": None,
        },
    ]
    # the 2 s pause between keyword calls
    assert fake_api["sleeps"] and all(s <= 2.0 for s in fake_api["sleeps"])
    assert "живые данные" in invoke("freq", "купить окна", "--source", "api").stdout


def test_freq_api_rejects_unknown_source(fake_api) -> None:
    assert invoke_json("freq", "x", "--source", "web")[0] == 2


def test_site_single_domain(fake_api) -> None:
    code, payload = invoke_json("site", "https://Drom.ru/catalog", "--region", "spb", "--limit", "2")
    assert code == 0
    assert (payload["domains"], payload["region"], payload["freq_region"], payload["truncated"]) == (
        ["drom.ru"],
        "spb",
        "world",
        True,
    )
    assert payload["results"][0] == {
        "phrase": "дром",
        "freq_world_exact": 1061158,
        "freq_world_broad": 4337381,
        "serp_results": 3000000,
        "position": 1,
    }
    endpoint, params = fake_api["calls"][0]
    assert (endpoint, params["q"], params["region"], params["num"]) == ("site", "drom.ru", "spb", "2")
    assert invoke("site", "drom.ru").exit_code == 0


def test_site_competitors_core(fake_api, tmp_path: Path) -> None:
    code, payload = invoke_json("site", "avito.ru", "auto.ru")
    assert code == 0
    assert payload["results"][0]["positions"] == {"avito.ru": 2, "auto.ru": 1}
    assert payload["results"][0]["avg_position"] == 1.5
    assert payload["results"][1]["positions"] == {"avito.ru": 5, "auto.ru": None}
    endpoint, params = fake_api["calls"][0]
    assert (endpoint, params["q"]) == ("site_mcmp", "avito.ru\r\nauto.ru")

    out = tmp_path / "core.csv"
    code, payload = invoke_json("site", "avito.ru", "auto.ru", "--csv", str(out))
    assert (code, payload["rows"], payload["total_count"]) == (0, 2, 16373)
    header = next(csv.reader(out.open(encoding="utf-8")))
    assert header[-2:] == ["position avito.ru", "position auto.ru"]
    assert invoke("site", "avito.ru", "auto.ru").exit_code == 0


def test_site_rejects_bad_arguments(fake_api) -> None:
    assert invoke_json("site", "drom.ru", "--region", "xx")[0] == 2
    assert invoke_json("site", "drom.ru", "--limit", "0")[0] == 2
    assert invoke_json("site", "not-a-domain")[0] == 2
    assert invoke_json("site", *[f"d{i}.ru" for i in range(11)])[0] == 2
    assert fake_api["calls"] == []


def test_rate_limit_is_retried_then_succeeds(fake_api) -> None:
    fake_api["errors"].extend([429, 503])
    code, _ = invoke_json("site", "drom.ru")
    assert code == 0
    assert [s for s in fake_api["sleeps"] if s >= 5] == [5.0, 10.0]


@pytest.mark.parametrize(
    ("statuses", "error", "exit_code"),
    [
        ([429, 429, 429, 429], "RATE_LIMITED", 1),
        ([401], "CREDENTIALS_INVALID", 3),
        ([402], "QUOTA_EXCEEDED", 1),
        ([500], "API_ERROR", 1),
    ],
)
def test_api_errors_are_structured(fake_api, statuses: list[int], error: str, exit_code: int) -> None:
    fake_api["errors"].extend(statuses)
    code, payload = invoke_json("site", "drom.ru")
    assert (code, payload["error"]["code"]) == (exit_code, error)


def test_network_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def urlopen(req, timeout=None):
        raise urllib.error.URLError("no route")

    monkeypatch.setattr(api.urllib.request, "urlopen", urlopen)
    code, payload = invoke_json("freq", "x", "--source", "api")
    assert (code, payload["error"]["code"]) == (1, "NETWORK_ERROR")


def test_api_key_comes_from_env_and_doctor_names_only_its_source(fake_api, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(api.KEY_ENV, "secret-key")
    invoke_json("site", "drom.ru")
    assert fake_api["calls"][0][1]["api_key"] == "secret-key"
    _, payload = invoke_json("doctor")
    check = next(c for c in payload["checks"] if c["name"] == "api_key")
    assert check == {"name": "api_key", "ok": True, "detail": api.KEY_ENV}
    assert "secret-key" not in str(payload)


def test_number_parsing_edge_cases() -> None:
    assert api._int("") is None
    assert api._number("x") is None
    assert api._number("3") == 3
    assert api.pick_phrase([], "") is None
    assert api.lookup("-x") is None
