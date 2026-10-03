# Примеры вывода bukvarix

Реальные команды и реальный вывод на текущей базе (113 696 532 фраз,
регион — Москва). Сквозной пример — фраза "растаможка авто". С 2026-09-17
весь `--json`-вывод обёрнут в конверт `{"ok": true/false, ...}` (см.
AGENTS.md, "Контракт вывода"). `elapsed_seconds` в реальном выводе —
полное дробное число (`0.07281408295966685`); здесь округлено для
читаемости. Время `search` с постингами — 0.1-0.4 с, без них (`engine:
scan`) — 16-35 с и плавает от прогона к прогону.

## search — основная команда (умный дефолт, строгий AND по леммам)

Одним вызовом — частотность самой фразы и список похожих в глубину
(порядок слов, словоформы не важны, но **все** слова запроса обязательны
как целые леммы; стоп-слова без `+` игнорируются):

```
$ bukvarix search "растаможка авто" --limit 5
растаможка авто: 466
калькулятор растаможки авто     1631
растаможка авто калькулятор     1021
растаможка авто 466
калькулятор растаможки авто в россии 2025       418
авто из кореи под заказ в россию с растаможкой цены     166
... показано 5 из 1550, задайте --limit 1550 чтобы увидеть все
сумма по всем 1550 похожим: 11387
(0.085s, engine: postings)
```

Тот же запрос с `--json`:

```
$ bukvarix search "растаможка авто" --limit 5 --json
{"ok": true, "query": "растаможка авто", "mode": "default",
 "results": [{"phrase": "калькулятор растаможки авто", "freq_msk_exact": 1631}, {"phrase": "растаможка авто калькулятор", "freq_msk_exact": 1021}, {"phrase": "растаможка авто", "freq_msk_exact": 466}, {"phrase": "калькулятор растаможки авто в россии 2025", "freq_msk_exact": 418}, {"phrase": "авто из кореи под заказ в россию с растаможкой цены", "freq_msk_exact": 166}],
 "elapsed_seconds": 0.093,
 "self": {"phrase": "растаможка авто", "freq_msk_exact": 466, "matched_via": "exact"},
 "total_count": 1550, "total_freq_sum": 11387, "engine": "postings"}
```

`-слово` — исключить, `self` при этом `null` (нет единой "канонической
фразы" при исключении):

```
$ bukvarix search "растаможка авто -калькулятор" --limit 5 --json
{"ok": true, "query": "растаможка авто -калькулятор", "mode": "default",
 "results": [{"phrase": "растаможка авто", "freq_msk_exact": 466}, {"phrase": "авто из кореи под заказ в россию с растаможкой цены", "freq_msk_exact": 166}, ...],
 "elapsed_seconds": 0.092, "self": null, "total_count": 1280, "total_freq_sum": 6699, "engine": "postings"}
```

Тот же запрос старым движком (`--mode scan`, полный скан по подстроке):
1 824 фразы вместо 1 550 и ~16 с вместо 0.09 с. Лишние 274 — подстрочные
совпадения вроде «растаможки автомобиля» («авто» внутри «автомобиль»).
Сравнение движков на 12 запросах — `docs/build.md`, проблема 6.

`(a|b)` группы **не поддерживаются** — скобки и `|` воспринимаются как
буквальный текст запроса, не как оператор (сознательно убрано, работа
только с одной фразой).

`--csv path.csv` — сохраняет `results` в CSV вместо печати; с `--json`
на stdout приходит подтверждение, а не пусто:

```
$ bukvarix search "растаможка авто" --csv out.csv --json
{"ok": true, "query": "растаможка авто", "saved_to": "out.csv", "rows": 20}
```

## freq — быстрая частотность без списка похожих

То же, что `self` у `search`, но через индекс, без скана —
единицы-десятки мс на всю пачку. Одна фраза или много — ответ один и тот
же: `results` по элементу на каждую входную фразу, в порядке ввода, плюс
происхождение цифры (`source`, `region`, `data_date`):

```
$ bukvarix freq "доставка грузов из китая" "доставка из китая" --json
{"ok": true, "count": 2, "region": "213", "source": "bukvarix", "data_date": null,
 "results": [{"query": "доставка грузов из китая", "phrase": "доставка грузов из китая", "freq_msk_exact": 284, "matched_via": "exact"},
             {"query": "доставка из китая", "phrase": "доставка из китая", "freq_msk_exact": 585, "matched_via": "exact"}],
 "elapsed_seconds": 0.131}

$ printf 'растаможка космического корабля\n' | bukvarix freq --input - --json
{"ok": true, "count": 1, "region": "213", "source": "bukvarix", "data_date": null,
 "results": [{"query": "растаможка космического корабля", "phrase": null, "freq_msk_exact": 0, "matched_via": null}],
 "elapsed_seconds": 0.092}
```

`data_date: null` — база импортирована до 0.4.0, дату выгрузки тогда не
сохраняли; после `db import-keywords` (с `--data-date` или по дате файла)
она появится.

Три уровня `matched_via`:

- `exact` — порядок слов не важен, но набор слов и формы — точные.
- `stopwords` — совпало после удаления предлогов/союзов из запроса.
- `lemma` — совпало через приведение к словарной форме.

Если БД ещё не собрана (`bukvarix db import-keywords` ни разу не
запускался) — `search`/`freq` вернут структурированную ошибку вместо
пустого результата:

```
$ bukvarix freq "что угодно" --json
{"ok": false, "error": {"code": "DATABASE_NOT_READY", "message": "No bukvarix database found at '...'.",
 "action": "Run 'bukvarix db import-keywords <path.tsv>' to build one, or 'bukvarix db set-path <path>' to point at an existing .duckdb file."}}
```

## --mode: инженерные примитивы (не дефолт, буквальные, без лемм)

```
$ bukvarix search "растаможка авто -калькулятор" --mode query --limit 3
растаможка авто 466
растаможка авто из кореи        84
растаможка авто в россии        81

$ bukvarix search "авто растаможка" --mode exact --json
{"ok": true, "query": "авто растаможка", "mode": "exact", "results": [{"phrase": "растаможка авто", "freq_msk_exact": 466, "matched_via": "exact"}], "elapsed_seconds": 0.055}

$ bukvarix search "растаможка авто" --mode contains --limit 3
растаможка авто калькулятор     1021
растаможка авто 466
растаможка авто калькулятор 2025        86
```

`--mode words` — как `contains`, но без учёта порядка слов, строгий AND,
БЕЗ лемм (буквальные слова, не лемматизированные, в отличие от дефолта):

```
$ bukvarix search "авто растаможка" --mode words --limit 3 --json
{"ok": true, "query": "авто растаможка", "mode": "words",
 "results": [{"phrase": "растаможка авто калькулятор", "freq_msk_exact": 1021}, {"phrase": "растаможка авто", "freq_msk_exact": 466}, {"phrase": "растаможка авто калькулятор 2025", "freq_msk_exact": 86}],
 "elapsed_seconds": 8.42}
```

## stats

Работает даже без собранной БД (`keywords_rows: 0` в этом случае):

```
$ bukvarix stats --json
{"ok": true, "db_path": "/Users/me/Library/Application Support/bukvarix/bukvarix.duckdb", "db_size_bytes": 43963396096, "tables": ["keywords", "word_lemmas"], "keywords_rows": 113696532}
```

## doctor

```
$ bukvarix doctor --json
{"ok": true, "healthy": true, "checks": [
  {"name": "python", "ok": true},
  {"name": "cli_version", "ok": true, "detail": "0.1.0"},
  {"name": "db_path", "ok": true, "detail": "/Users/me/Library/Application Support/bukvarix/bukvarix.duckdb"},
  {"name": "db_ready", "ok": true, "detail": null}
]}
```

Проверок проекта и credentials нет — bukvarix ими не пользуется;
`healthy` = все четыре проверки `ok` (на практике — `db_ready`).

## db — обслуживание (деструктивно, требует --confirm)

```
$ bukvarix db import-keywords keywords213.tsv --dry-run --json
{"ok": true, "would_import": "keywords213.tsv", "db_path": "/Users/me/Library/Application Support/bukvarix/bukvarix.duckdb"}

$ bukvarix --non-interactive db import-keywords keywords213.tsv --json
{"ok": false, "error": {"code": "INVALID_ARGUMENTS", "message": "This replaces the entire keywords table in ... — make sure you have a backup requires --confirm when --non-interactive is set."}}

$ bukvarix db import-keywords keywords213.tsv --confirm --json
{"ok": true, "raw_rows": 126784040, "deduped_rows": 113696532, "removed_permutations": 13087508}
```

`db set-path` — сохраняет путь к БД в пользовательском конфиге, без
привязки к переменной окружения:

```
$ bukvarix db set-path /Volumes/data/bukvarix.duckdb --json
{"ok": true, "db_path": "/Volumes/data/bukvarix.duckdb"}
```
