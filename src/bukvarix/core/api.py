"""Client for the Bukvarix web API (http://api.bukvarix.com, docs: bukvarix.com/api.html).

The local database holds Moscow only; the API answers for the whole world
(`freq_world_exact` = "!точная" frequency, `freq_world_broad` = broad) and
for domains (keywords a site ranks for, with its SERP position). Standard
library only: one GET/POST, a TSV body, the HTTP status mapped to a CliError.

Pacing follows the SEMTools add-in: at least 2 s between `keywords` calls,
1 s between `site` calls; 429/503 are retried after 5, 10 and 20 s.
"""

from __future__ import annotations

import logging
import time
import urllib.error
import urllib.parse
import urllib.request

from rb_credentials import Store

from .errors import ApiCredentialsInvalid, ApiError, ApiNetworkError, ApiQuotaExceeded, ApiRateLimited

BASE_URL = "http://api.bukvarix.com/v1"
KEY_ENV = "BUKVARIX_API_KEY"
FREE_KEY = "free"
SOURCE = "bukvarix-api"
REGION_WORLD = "world"
MAX_ROWS = 1_000_000  # free key: max rows per report
MAX_DOMAINS = 10  # free key: max domains in one competitors' report
TIMEOUT = 120.0

# region codes of /v1/site/ — the search engine and city whose SERP positions are reported
REGIONS = {
    "msk": "Москва (Яндекс)",
    "gmsk": "Москва (Google)",
    "spb": "Санкт-Петербург (Яндекс)",
    "rus": "Россия (Яндекс)",
    "nsk": "Новосибирск (Яндекс)",
    "ekb": "Екатеринбург (Яндекс)",
    "kzn": "Казань (Яндекс)",
    "nnv": "Нижний Новгород (Яндекс)",
    "kry": "Красноярск (Яндекс)",
    "che": "Челябинск (Яндекс)",
    "sam": "Самара (Яндекс)",
    "ufa": "Уфа (Яндекс)",
    "rnd": "Ростов-на-Дону (Яндекс)",
    "krr": "Краснодар (Яндекс)",
    "oms": "Омск (Яндекс)",
    "vrn": "Воронеж (Яндекс)",
    "prm": "Пермь (Яндекс)",
    "vlg": "Волгоград (Яндекс)",
    "sar": "Саратов (Яндекс)",
    "tmn": "Тюмень (Яндекс)",
    "tom": "Томск (Яндекс)",
    "gkiev": "Киев (Google)",
    "minsk": "Минск (Яндекс)",
    "gminsk": "Минск (Google)",
    "nursul": "Астана (Яндекс)",
}
DEFAULT_REGION = "msk"

_MIN_INTERVAL = {"keywords": 2.0, "site": 1.0}
_RETRY_DELAYS = (5.0, 10.0, 20.0)
_last_call: dict[str, float] = {}
_log = logging.getLogger(__name__)


_STORE = Store("bukvarix")
_PROVIDER = "api"
_LEGACY = {"api_key": KEY_ENV}


def api_key() -> str:
    """The Bukvarix key: the store's `bukvarix:api` credential, else the built-in free key."""
    credential = _STORE.get(_PROVIDER, env_fields=["api_key"], legacy_env=_LEGACY)
    return (credential or {}).get("api_key") or FREE_KEY


def key_source() -> str:
    """Where the key comes from, for doctor — never the value itself."""
    located = _STORE.locate(_PROVIDER, ["api_key"], legacy_env=_LEGACY)
    entry = located.get("api_key")
    if entry is None:
        return "free (built-in)"
    _, source, where = entry
    return where if source == "env" else f"keychain {where}"


def _pace(family: str) -> None:
    wait = _MIN_INTERVAL[family] - (time.monotonic() - _last_call.get(family, float("-inf")))
    if wait > 0:
        time.sleep(wait)
    _last_call[family] = time.monotonic()


def _request(endpoint: str, params: dict, *, post: bool = False) -> str:
    """One API call with pacing and 429/503 retries; returns the body without a BOM."""
    family = "site" if endpoint.startswith("site") else "keywords"
    fields = {**params, "api_key": api_key(), "format": "tsv", "header": "0", "bom": "0"}
    encoded = urllib.parse.urlencode(fields)
    url = f"{BASE_URL}/{endpoint}/"
    for delay in (*_RETRY_DELAYS, None):
        _pace(family)
        if post:
            req = urllib.request.Request(  # noqa: S310 — url is the fixed http BASE_URL
                url,
                data=encoded.encode(),
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
        else:
            req = urllib.request.Request(f"{url}?{encoded}")  # noqa: S310 — fixed http BASE_URL
        _log.debug("%s %s %s", "POST" if post else "GET", endpoint, params)
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:  # noqa: S310 — fixed http BASE_URL
                return resp.read().decode("utf-8-sig")
        except urllib.error.HTTPError as exc:
            status = exc.code
            if status in (429, 503) and delay is not None:
                _log.warning("bukvarix API answered %s, retrying in %.0f s", status, delay)
                time.sleep(delay)
                continue
            raise _error_for(status) from None
        except (urllib.error.URLError, TimeoutError) as exc:
            raise ApiNetworkError(str(getattr(exc, "reason", exc))) from None
    raise AssertionError("unreachable")  # pragma: no cover


def _error_for(status: int) -> Exception:
    if status == 401:
        return ApiCredentialsInvalid()
    if status == 402:
        return ApiQuotaExceeded()
    if status in (429, 503):
        return ApiRateLimited(status)
    return ApiError(status)


def _rows(body: str) -> list[list[str]]:
    return [line.split("\t") for line in body.splitlines() if line.strip()]


def _int(value: str) -> int | None:
    value = value.strip()
    return int(value) if value.lstrip("-").isdigit() else None


def _count(endpoint: str, params: dict) -> int:
    body = _request(endpoint, {**params, "result_count": "1"}).strip()
    return _int(body) or 0


def keywords(query: str, num: int) -> list[dict]:
    """Phrases that contain `query` (the API's own syntax: +word, -word, !word), in API order."""
    body = _request("keywords", {"q": query, "num": str(num)})
    # phrase, words, chars, broad, !exact
    return [{"phrase": r[0], "freq_world_exact": _int(r[4]), "freq_world_broad": _int(r[3])} for r in _rows(body)]


def keywords_count(query: str) -> int:
    return _count("keywords", {"q": query})


def phrase_words(query: str) -> list[str] | None:
    """The words of a phrase without +/!/~ operators, sorted; None when it excludes a word (-word)."""
    tokens = query.lower().replace("ё", "е").split()
    if any(t.startswith("-") for t in tokens):
        return None
    return sorted(w for w in (t.lstrip("+!~") for t in tokens) if w)


def pick_phrase(rows: list[dict], query: str) -> dict | None:
    """The row whose words are exactly the query's (any order), the most frequent one of permutations."""
    words = phrase_words(query)
    if not words:
        return None
    hits = [r for r in rows if sorted(r["phrase"].lower().replace("ё", "е").split()) == words]
    if not hits:
        return None
    return max(hits, key=lambda r: r["freq_world_exact"] or 0)


def lookup(phrase: str) -> dict | None:
    """Frequency of one phrase: an exact-forms query (!w1 !w2), then the row with the same words."""
    words = phrase_words(phrase)
    if not words:
        return None
    rows = keywords(" ".join(f"!{w}" for w in words), 50)
    return pick_phrase(rows, phrase)


def site(domain: str, region: str, num: int) -> list[dict]:
    """Keywords one domain ranks for in `region`, in API order."""
    body = _request("site", {"q": domain, "region": region, "num": str(num)})
    # phrase, serp results, words, chars, broad, !exact, position
    return [
        {
            "phrase": r[0],
            "freq_world_exact": _int(r[5]),
            "freq_world_broad": _int(r[4]),
            "serp_results": _int(r[1]),
            "position": _int(r[6]),
        }
        for r in _rows(body)
    ]


def site_count(domain: str, region: str) -> int:
    return _count("site", {"q": domain, "region": region})


def sites(domains: list[str], region: str, num: int) -> list[dict]:
    """The competitors' semantic core: keywords of 2..10 domains, with each domain's position."""
    body = _request("site_mcmp", {"q": "\r\n".join(domains), "region": region, "num": str(num)}, post=True)
    # phrase, serp results, words, chars, broad, !exact, domains found, avg position, position per domain...
    return [
        {
            "phrase": r[0],
            "freq_world_exact": _int(r[5]),
            "freq_world_broad": _int(r[4]),
            "serp_results": _int(r[1]),
            "domains_found": _int(r[6]),
            "avg_position": _number(r[7]),
            # 0 = the domain does not rank for this phrase
            "positions": {d: _int(p) or None for d, p in zip(domains, r[8:], strict=False)},
        }
        for r in _rows(body)
    ]


def _number(value: str) -> float | int | None:
    value = value.strip().replace(",", ".")
    try:
        number = float(value)
    except ValueError:
        return None
    return int(number) if number.is_integer() else number


def sites_count(domains: list[str], region: str) -> int:
    body = _request("site_mcmp", {"q": "\r\n".join(domains), "region": region, "result_count": "1"}, post=True).strip()
    return _int(body) or 0
