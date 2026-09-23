# Backlog

## 1. Ускорить `search`: инвертированный индекс лемм вместо ILIKE-скана

**Статус:** ✅ выполнено 2026-09-23, ветка `feature/lemma-postings`. Итог,
цифры и отступления от плана (колонка `freq`, сборка кусками, стоп-слова) —
`docs/build.md`, проблема 6; изменения — `docs/changelog.md`. Текст ниже
сохранён как постановка. **Приоритет:** высокий.
**Исполнитель:** агент (Opus) по инструкции ниже. Инструкция самодостаточна:
прочитать `AGENTS.md`, `docs/build.md` (особенно проблему 5) и этот раздел
целиком перед началом.

### Проблема

`search_smart()` в `src/bukvarix/core/db.py` фильтрует 113.7М строк через
`lemma_signature ILIKE '%слово%'` на каждое слово запроса, причём дважды
(count/sum и top-N). Это полный скан без индекса: 16-30 с на запрос.
Побочный дефект: `%авто%` матчит и `автосервис`, и `автомат`, то есть
текущий AND работает по подстрокам, а не по словам.

FTS/BM25 из DuckDB уже пробовали и откатили (OR-семантика, взрыв на
предлогах, сломанные планы с `-слово`). Не повторять.

### Решение

Таблица постингов `lemma_postings(lemma, kw_id)`: одна строка на каждую
пару «лемма → фраза, в которой она есть», отсортированная по `lemma`.
Запрос «все слова обязательны» становится пересечением списков `kw_id`
по каждому слову, `-слово` — вычитанием. Пересечение только сужает
результат, поэтому частые слова не взрывают выдачу по построению.

Ожидания (оценка, не замер): ~400М строк постингов, +3-6 ГБ к файлу базы
после компрессии, типичный запрос под секунду, худший случай (два очень
частых слова) 1-3 с.

Старый скан остаётся как fallback (если таблицы нет) и как `--mode scan`
для сверки.

### Ключевые решения (не пересматривать без причины)

1. **Идентификатор фразы — `rowid` таблицы `keywords`.** Явную колонку
   `kw_id` не добавлять: `UPDATE` 113.7М строк на таблице с тремя
   ART-индексами рискован (DuckDB может делать delete+insert, раздувая
   индексы), а `CREATE OR REPLACE` сбросит индексы (26 ГБ, 16 мин
   пересборки). `rowid` стабилен, пока таблицу не пересоздают, поэтому
   постинги надо **инвалидировать** при `import-keywords` и `normalize`
   (см. шаг 4).
2. **Индексировать все леммы, включая стоп-слова.** Так `+в` продолжает
   работать как сейчас. Исключение стоп-слов из постингов — отдельная
   оптимизация на потом, если худший случай окажется медленным.
3. **Токены постингов = `str_split(lemma_signature, ' ')`.** Ровно то, что
   выдаёт `_lemmatize_words_list()` для слов запроса (обе стороны идут
   через `word_lemmas` с `coalesce(lemma, word)`), поэтому лемма запроса
   совпадёт с постингом побайтово для любого слова, виденного при импорте.
4. **Слово, которого нет в постингах, даёт пустой результат.** Это
   корректно: ни одна фраза его не содержит. Подстрочный fallback не
   делать. В JSON-ответ добавить поле `engine: "postings" | "scan"`, чтобы
   было видно, какой путь сработал.
5. **Один тяжёлый запрос, а не два.** Совпавшие `kw_id` материализовать в
   `TEMP TABLE _hits`, затем count/sum и top-N читать из неё. Проверить,
   что DuckDB 1.5.x позволяет TEMP-таблицы на read-only соединении; если
   нет, использовать один запрос с CTE и оконной функцией
   `count(*) OVER ()` / `sum(...) OVER ()` вместе с `LIMIT`.

### Шаги

#### Шаг 0. Бэкапы (обязательно, см. AGENTS.md)

```bash
cd /Users/bolandau/Projects/tools/bukvarix
git checkout -b feature/lemma-postings      # вся работа в ветке, main остаётся рабочим
test -d /Volumes/Data/bukvarix || { echo "Подключите диск /Volumes/Data и повторите"; exit 1; }
cp src/bukvarix/core/db.py src/bukvarix/core/db.py.bak_postings
cp src/bukvarix/commands/db.py src/bukvarix/commands/db.py.bak_postings
cp src/bukvarix/commands/search.py src/bukvarix/commands/search.py.bak_postings
df -h . /Volumes/Data        # на рабочем диске ≥ 20 ГБ под temp-файлы сортировки, на внешнем ≥ 50 ГБ под копию
cp bukvarix.duckdb /Volumes/Data/bukvarix/bukvarix.duckdb.backup_pre_postings
```

Если `/Volumes/Data` не смонтирован — не продолжать: попросить
пользователя подключить диск. Бэкап базы в другое место не класть.

Копии удалять только после шага 7 (проверка реальными запросами).

#### Шаг 1. Сборка постингов: `build_lemma_postings()` в `core/db.py`

```python
def build_lemma_postings(db_path: Path, *, temp_directory: Path | None = None) -> dict:
    con = connect(db_path)
    try:
        con.execute("PRAGMA preserve_insertion_order = false")
        con.execute("SET memory_limit = '10GB'")
        if temp_directory:
            con.execute(f"SET temp_directory = '{temp_directory}'")
        con.execute("DROP TABLE IF EXISTS lemma_postings")
        con.execute("""
            CREATE TABLE lemma_postings AS
            SELECT tok.lemma AS lemma, k.rowid AS kw_id
            FROM keywords k, UNNEST(str_split(k.lemma_signature, ' ')) AS tok(lemma)
            WHERE tok.lemma != ''
            ORDER BY lemma, kw_id
        """)
        con.execute("""
            CREATE OR REPLACE TABLE lemma_postings_meta AS
            SELECT (SELECT count(*) FROM keywords) AS keywords_rows,
                   (SELECT count(*) FROM lemma_postings) AS postings_rows,
                   now() AS built_at
        """)
        con.execute("CHECKPOINT")
        return {"postings_rows": ..., "keywords_rows": ...}
    finally:
        con.close()
```

Требования:
- Требует `lemma_signature` (после `normalize`); если колонки нет — ошибка
  `DATABASE_NOT_READY` с action «run bukvarix db normalize».
- `ORDER BY` на 400М строк — внешняя сортировка, прольёт 10-20 ГБ во
  временные файлы. Перед полным прогоном **замерить на сэмпле**:
  `WHERE hash(k.phrase) % 40 = 0` (~2.8М фраз) и экстраполировать время и
  размер. Помнить урок docs/build.md: сэмпл предсказывает время сборки,
  но не время запроса.
- Если полный `ORDER BY` падает по памяти: собирать без сортировки в
  24 чанка по `hash(phrase) % 24` во временную таблицу, затем один
  `CREATE TABLE lemma_postings AS SELECT * FROM _tmp ORDER BY lemma, kw_id`.
  Если и это падает — сортировать только по `lemma` (без `kw_id`), zone
  maps всё равно сработают.
- ART-индекс на `lemma_postings.lemma` **не строить**: сортировка даёт
  zone maps, этого достаточно, а ART на 400М VARCHAR — часы и десятки ГБ
  (docs/build.md, проблема 1 и 3).
- `lemma_postings_meta.keywords_rows` — защита от рассинхронизации: в
  `search_smart` сверять с текущим `count(*) FROM keywords` (по
  `duckdb_tables().estimated_size` или дешёвому count) и при расхождении
  падать на scan-путь с предупреждением в stderr.

#### Шаг 2. Команда `bukvarix db build-postings`

В `commands/db.py` по образцу `reindex_cmd`: `--confirm`, `--dry-run`,
`--json`, `--quiet`, `--verbose`, плюс `--temp-dir PATH`. Деструктивна в
смысле «долго и растит файл», поэтому подтверждение обязательно. В
docstring на русском (как у соседей) явно: нужен бэкап, ≥ 20 ГБ на диске
под temp, ориентировочно 10-30 минут на полной базе.

#### Шаг 3. Новый путь в `search_smart()`

Логика после парсинга запроса и лемматизации `required`/`excluded`:

```python
has_postings = <таблица lemma_postings есть И meta.keywords_rows == count(keywords)>
if has_postings:
    engine = "postings"
    # 1) TEMP TABLE _hits(kw_id): пересечение required, минус excluded
    # 2) count/sum из _hits JOIN keywords по rowid
    # 3) top-N: SELECT phrase, freq_msk_exact FROM keywords k JOIN _hits ON k.rowid = _hits.kw_id
    #           ORDER BY freq_msk_exact DESC LIMIT ?
else:
    engine = "scan"   # текущий ILIKE-код без изменений
```

SQL пересечения — через цепочку `INTERSECT`, DuckDB сам оптимизирует;
excluded — `EXCEPT`:

```sql
CREATE TEMP TABLE _hits AS
  (SELECT kw_id FROM lemma_postings WHERE lemma = ?)
  INTERSECT (SELECT kw_id FROM lemma_postings WHERE lemma = ?)
  EXCEPT    (SELECT kw_id FROM lemma_postings WHERE lemma = ?)
```

Порядок `INTERSECT` формально не важен, но на всякий случай ставить
первым самое редкое слово: частоту лемм взять одним запросом
`SELECT lemma, count(*) FROM lemma_postings WHERE lemma IN (...) GROUP BY 1`
(на сортированной таблице это дёшево) и отсортировать `required` по
возрастанию. Если у какого-то required-слова 0 постингов — сразу вернуть
пустой результат без пересечения.

Дубликаты лемм в одном запросе («авто авто») схлопнуть в `set` до
пересечения. `self` (`_exact_lookup`) не трогать. Поле `engine` добавить
в возвращаемый dict; `commands/search.py` пробрасывает его в JSON и в
человеческий вывод (одной строкой, например «engine: postings, 0.4s»).

#### Шаг 4. Инвалидация постингов

- `import_keywords()`: после `CREATE OR REPLACE TABLE keywords` —
  `DROP TABLE IF EXISTS lemma_postings; DROP TABLE IF EXISTS lemma_postings_meta`.
- `normalize()` в `core/normalize.py`: то же самое в начале
  `add_lemma_signature()`, если он реально пересчитывает (а не пропускает
  по «уже заполнено»).
- Проверка по `keywords_rows` в meta из шага 1 — вторая линия защиты.

#### Шаг 5. `--mode scan` и справка

- В `commands/search.py` добавить `scan` в перечень `--mode`: это старый
  ILIKE-путь `search_smart` принудительно, для сверки. Описание в help на
  русском.
- Обновить `AGENTS.md` (раздел `search`: убрать «медленно 16-30 с»,
  описать `engine`, `db build-postings`, изменение семантики «подстрока →
  целое слово»), `src/bukvarix/data/SKILL.md` (то, что отдаёт `--ai`),
  `docs/build.md` (новый шаг в «лучшем пути», тайминги, размер файла до/после,
  и подраздел к проблеме 5: «что в итоге сработало»).

#### Шаг 6. Тесты (`tests/test_db.py`, фикстуры в `tests/conftest.py`)

Добавить фикстуру `postings_db` = `normalized_db` + `build_lemma_postings`.
Тесты:
- `test_search_smart_postings_matches_scan_on_whole_words`: на корпусе
  фикстуры результаты `engine=postings` и `--mode scan` совпадают для
  запросов без подстрочных ловушек («растаможка авто», «авто продажа»).
- `test_search_smart_postings_is_whole_word`: запрос «авто» через
  постинги **не** матчит фразу с «автомобиль» (добавить в `_ROWS` строку
  вида `("купить автомобиль", 10)`), а scan — матчит. Фиксирует
  осознанное изменение семантики.
- `test_search_smart_postings_excluded`: `-слово` убирает фразы.
- `test_search_smart_unknown_word_is_empty`: слово вне корпуса даёт
  `results=[]`, `total_count=0`, `engine="postings"`.
- `test_search_smart_falls_back_to_scan_without_postings`: на
  `normalized_db` без постингов `engine="scan"`.
- `test_import_keywords_drops_postings`: после повторного импорта
  `lemma_postings` отсутствует.
- `test_build_lemma_postings_meta`: `keywords_rows` в meta равен count.

`uv run pytest` должен быть зелёным; линтеры/хуки проекта — как обычно.

#### Шаг 7. Прогон на реальной базе и приёмка

1. `bukvarix db build-postings --dry-run`, затем `--confirm` (с бэкапом
   из шага 0). Записать время и `db_size_bytes` до/после в docs/build.md.
2. Проверка целостности (должно быть 0):
   ```sql
   SELECT count(*) FROM lemma_postings p JOIN keywords k ON k.rowid = p.kw_id
   WHERE NOT list_contains(str_split(k.lemma_signature, ' '), p.lemma);
   ```
3. Сверка на 10-15 запросах: «растаможка авто», «авто в растаможка»,
   «купить диван», «купить недорого» (два частых слова), «растаможка
   -калькулятор», «+в москве авто», запрос с опечаткой. Для каждого
   `total_count(scan) >= total_count(postings)` всегда; для запросов без
   подстрочных совпадений — равны. Время `postings` записать в таблицу
   в docs/build.md.
4. `bukvarix freq` и `bukvarix stats` работают как раньше.
5. Только после этого удалять `*.bak_postings` и копию базы на `/Volumes/Data/bukvarix/`,
   и только после этого вливать `feature/lemma-postings` в `main`.
6. Записать итог в `docs/build.md` **независимо от исхода**: если постинги
   не дали ожидаемого ускорения или сборка не прошла — подраздел
   «Проблема 6» с цифрами и причиной, как у FTS/BM25. Удачный итог —
   в «Лучший путь», таблицу таймингов и `docs/changelog.md`.

### Критерии готовности

- Типичный `bukvarix search` — под 1 с, худший случай из списка выше — до
  3-5 с, оба замерены на полной базе и записаны в docs/build.md.
- `--json` содержит `engine`; контракт остальных полей не изменился.
- Без таблицы постингов инструмент работает как до задачи (`engine: scan`).
- Тесты из шага 6 зелёные; AGENTS.md, SKILL.md, docs/build.md обновлены.
- Обе резервные копии удалены только после шага 7.

### Что НЕ делать

- Не возвращать FTS/`match_bm25`.
- Не строить ART-индекс на `lemma_postings`.
- Не добавлять колонку `kw_id` в `keywords` через `UPDATE`.
- Не менять `freq`/`_exact_lookup` и три существующих индекса.
- Не удалять `--mode` примитивы (`contains`/`words`/`prefix`/`exact`/`query`).

### Возможные продолжения (не в этой задаче)

- Исключить стоп-слова из постингов, если худший случай медленный.
- Заменить три ART-индекса (26 ГБ) на отсортированную таблицу
  `signature → rowid` для `freq`; файл ужмётся примерно до 20 ГБ.
- ~~Удалить неиспользуемый FTS-индекс~~ — сделано 2026-09-23: схема
  `fts_main_keywords` удалена, но файл не уменьшился — ~4.0 ГБ свободных
  блоков внутри. Вернуть их на диск — только пересборкой файла (перелить
  таблицы в новый `.duckdb` + заново `db reindex`, ~16 мин по docs/build.md).
