# Архитектура и контракт модулей

Система: PDF → 8 стадий конвейера → записи в SQLite (с источником у каждого значения) → правила проверки → веб (пользователь и админ).
Корень проекта: `/Users/artemshelkunov/Documents/hackaton`. Python: `.venv/bin/python` (3.14), зависимости уже стоят:
fastapi, uvicorn, python-multipart, httpx, pypdf, pdfplumber, pyyaml, pandas. Новые пакеты ставить в `.venv` и дописывать в `requirements.txt`.
Главное требование проекта: **не выдумывать данные**; запись никогда не удаляется физически (отклонённая получает `status='rejected'`).

## Уже готово (не переписывать, можно импортировать)
- `app/db.py` — схема SQLite и хелперы `q, q1, ex, tx, jl, jd, now, audit, conn, init_db`. Схему читать в самом файле.
- `app/config.py` — `load_domain(domain)`, `list_domains()`, `seed_versions()`, `save_version()`, `activate()`, `columns(cfg, entity)`.
- `config/domains/{solar,rag}/{schema,rules}.yaml` — предметные области. Всё предметное только здесь (поля, словари, метрики и единицы, правила, критерии сопоставления `compare`, поля оценки `eval`). **Код не содержит имён предметных полей** (кроме общих: publication_id, result_id, source_page, source_fragment, raw_value, raw_unit, normalized_value, normalized_unit, result_origin, extraction_status, duplicate_of, note). Область 2 подключается только конфигом.
- Старый код: `app/pipeline.py`, `app/store.py`, `app/server.py` (прототип, файловое хранилище). Новый код его заменяет: pipeline-агент переименовывает `app/pipeline.py` → `app/legacy_pipeline.py` (оставляет для справки: промпты, `parse_json`, `normalize`), `store.py` не используется.

## Модель данных
- `records.data` — JSON строки сущности: ключи = `columns` сущности из schema.yaml (значения строками, пустые можно опускать). `records.entity` — имя сущности из схемы (`results`, `devices`, `configurations`…), `records.rid` — её id (`R001`, `DV1`…), уникально в `(pub_id, entity, rid)`.
- `records.status`: `extracted` (из модели) | `verified` (подтвердил человек/прошло всё) | `flagged` (есть открытое замечание) | `rejected` (цитата не найдена или отклонено человеком; в экспорт и UI по умолчанию не попадает) | `manual` (создана/правлена вручную).
- `source_page`, `source_fragment`, `quote_verified` (1/0/NULL) денормализованы в колонки records для результатов.
- `publications.status`: `registered` → `processing` → `needs_ocr` | `verified` | `flagged` | `error` | `excluded` (вне области).
- `issues`: `origin` = `rule` (движок правил) | `model` (противоречие, найденное моделью) | `verify` (цитата не найдена); `status` — модерация (`open|accepted|fixed|kept`); `record_ids` — JSON-список `rid` результатов той же публикации.
- `pages(pub_id,page,text,ocr)` — постраничный текст (с 1). `llm_calls` — журнал всех вызовов модели.
- Пользователи: `users(role: admin|annotator|researcher)`; пароль — pbkdf2-hmac-sha256 (salt в строке). Токен случайный, хранится в `users.token`.
- Эталон (gold): таблица `gold_records(pub_id, domain, entity, rid, data)` создаётся API-агентом (`CREATE TABLE IF NOT EXISTS` в `app/evaluation.py`).

## Конвейер (8 стадий, порядок фиксирован)
`register → parse → segment → extract → verify → normalize → validate → publish`. Каждая стадия пишет строку в `stage_runs` (`pending|running|done|error`, `attempt`, `message`) и **идемпотентна**: повторный запуск с места сбоя (`from_stage=None` = первая не `done`) не дублирует записи (перед записью стадия удаляет свои прежние результаты: extract — записи `origin≠manual`, validate — открытые замечания `origin='rule'`).
- register: sha256, DOI из первых страниц, дедупликация (по хешу и DOI → второй файл помечается `status='error', message='дубликат X'`), число страниц.
- parse: текст по страницам в `pages` (pypdf, при провале pdfplumber); страницы <200 символов = без слоя; если без слоя >40 % → `needs_ocr=1, status='needs_ocr', ocr_status='queued'` и конвейер для статьи останавливается; таблицы/подписи рисунков остаются в тексте страницы (pdfplumber-таблицы дописываются markdown-блоком `[TABLE]…`).
- segment: разбиение на разделы (по заголовкам) и куски ≤ `extraction.chunk_chars`; отрезание списка литературы; результат кэшируется в `data/cache/<pub>/segments.json`.
- extract: LLM-извлечение строго по схеме из конфига: промпт собирается из `schema.yaml` (поля, словари, `scope`, `extraction.hints`) — шаг «структура» (сущности с `structure: true`) и шаг «результаты» (сущность `result_entity`) по кускам. Ответ — JSON; `in_scope=false` → публикация `excluded`, остальных записей нет. Все вызовы пишутся в `llm_calls`. Дубликаты значений помечает код (`duplicate_of`), не модель.
- verify: дословная цитата (`source_fragment`) ищется в тексте указанной страницы (нормализация: регистр, пробелы, переносы слов через дефис, лигатуры, юникод-минусы); не найдена на странице — ищется на других; не найдена вообще → запись `status='rejected'`, `quote_verified=0` + issue `origin='verify'`, `rule_id='citation_not_found'`.
- normalize / validate — `app/rules.py` (см. ниже). publish — обновляет FTS (`records_fts`), статус публикации: `excluded` / `flagged` (есть открытые замечания severity error|warning или отклонённые записи) / `verified`.
Публичный интерфейс `app/pipeline.py`:
```
STAGES = ["register","parse","segment","extract","verify","normalize","validate","publish"]
register_pdf(domain, path=None, data=None, filename=None, split="", pub_id=None) -> pub_id   # копирует/запоминает PDF, создаёт publications
scan_source(domain, path, split="") -> {"added":[ids],"skipped":[...],"duplicates":[...],"unreadable":[...]}   # инвентаризация папки
enqueue(pub_id, from_stage=None); enqueue_many(pub_ids, from_stage=None)     # пул потоков, число воркеров из settings 'workers'
run_publication(pub_id, from_stage=None, stop_after=None) -> final status     # синхронно
revalidate(pub_id)       # синхронно normalize+validate+publish (после правки записей)
inventory_report(domain=None) -> dict                                          # JSON-отчёт инвентаризации
ocr_available() -> bool;  run_ocr(pub_id) -> dict                              # tesseract/ocrmypdf, если установлены; иначе понятная ошибка
```
LLM-клиент `app/llm.py`: `call_json(system, user, pub_id, stage) -> dict` (OpenAI-совместимый `/chat/completions`, настройки из таблицы `settings`: ключи `provider, base_url, model, api_key, workers`; ключ также из env `DEEPSEEK_API_KEY`; ретраи, лог в `llm_calls`, парсинг усечённого JSON как в `legacy_pipeline.parse_json`), `get_settings(public=False)`, `save_settings(patch)`, `test_connection()`. Ошибки — класс `LLMError` с понятным текстом (нет ключа, 401, 402, 429…). Провайдеры: deepseek (`response_format=json_object`), lmstudio, custom.

## Правила и нормализация: `app/rules.py` (rules-агент)
```
normalize_record(cfg, entity, data: dict) -> dict      # для сущности result_entity: из raw_value/raw_unit считает normalized_value/normalized_unit и metric_value/metric_unit по cfg['schema']['metrics'];
                                                       # метрика без правил/без известной шкалы -> normalized пусто (если schema.scale_required) — НЕ пересчитывать
normalize_all(cfg, tables: dict[str, list[dict]]) -> dict  # все записи публикации
run_rules(cfg, tables: dict[str, list[dict]]) -> list[dict]
   # tables = {entity: [data-dict, ...]} ОДНОЙ публикации (без rejected, без duplicate_of там, где правило этого требует)
   # issue = {"rule_id","rule_version","severity","record_ids":[rid,...],"description","source_page","source_fragment"}
RULE_TYPES = {...}   # реестр типов: formula_consistency, bound, inequality, pair_diff, champion_vs_mean, separate_values, metric_scale, k_required, link_integrity, no_merge
```
Типы и параметры — в `config/domains/*/rules.yaml`; пороги только оттуда. Выражения (`expr`) считаются безопасным вычислителем на `ast` (только числа, имена метрик, + - * / **, скобки). Неизвестный тип правила → предупреждение в лог, не падение. Тесты `tests/test_rules.py`, `tests/test_normalize.py`: каждое правило (нарушение/норма/граница), единицы (mV→V, доля→%, неизвестная шкала, пустое значение, cited-записи не проверяются на физику устройства).

## API (всё под `/api`, JSON; владелец — API-агент; файлы `app/server.py` + `app/api/*.py`)
Авторизация: заголовок `Authorization: Bearer <token>`. Без токена — доступ researcher только на чтение (`GET`), настройка `require_login` (settings, по умолчанию false). annotator: правки записей, модерация замечаний, gold. admin: всё. Каждое изменение → `db.audit`.
- `POST /auth/login {name,password}` → `{token,user:{name,role}}`; `GET /auth/me`; при первом старте создаётся `admin` со случайным паролем в `data/admin_password.txt`.
- `GET /domains` → `[{domain,title,version}]`
- `GET /stats?domain=` → `{pubs,by_status:{},coverage,verified_share,records,rejected,issues_by_rule:{rule:{open,total}},quote_valid_share,ocr_queue,last_eval}` (`coverage` = доля статей, прошедших все стадии)
- `GET /publications?domain=&status=&q=&split=` → `[{id,domain,title,year,doi,status,in_scope,pages,needs_ocr,n_records,n_issues_open,message,split}]`
- `GET /publications/{id}` → `{publication,records:{entity:[rec]},issues:[],stages:[]}`; rec = `{id,pub_id,entity,rid,data,status,source_page,source_fragment,quote_verified,issue_ids:[]}`; отклонённые записи — только при `?include_rejected=1`
- `GET /records?domain=&entity=&pub=&q=&status=&metric=&origin=&limit=&offset=` → `{total,items:[rec]}` (по умолчанию без rejected и без duplicate_of; `?dups=1` включает повторы)
- `GET /records/{id}` → `{record, context:{entity:{rid:data}}  # связанные по refs из схемы, history:[], issues:[]}`
- `GET /search?q=&domain=` → FTS5 по записям и публикациям: `{items:[{kind:'record'|'publication',pub_id,entity,rid,id,snippet}]}`
- `POST /compare {domain,record_ids:[id,...]}` → `{comparable:bool, rows:[{record, resolved:{criterion:value}}], blocked:[{a,b,reasons:[{criterion,reason,a_value,b_value}]}], groups:[[id,...]]}` по `schema.compare.criteria` (mode `equal` / `ratio_within`); пустое значение критерия = несопоставимо с объяснением «значение не указано в статье»; `GET /compare/candidates?record_id=` → id записей, сопоставимых со всеми критериями
- `GET /issues?domain=&pub=&status=&rule=&severity=` → `[{id,pub_id,rule_id,severity,origin,status,comment,description,record_ids:[rid],source_page,source_fragment,created}]`
- `GET /export?domain=&format=csv|json&status=verified|all&pubs=a,b` → zip (CSV по сущностям; `status=all` включает extracted/flagged/manual, rejected — никогда) или JSON с источниками и статусами; для csv — колонки ровно по `columns` схемы, UTF-8 с BOM
- `GET /passport/{domain}` (JSON) и `GET /passport/{domain}.md` — **генерируется из schema.yaml + rules.yaml** (поля, типы, словари, единицы, правила, пороги, версия); `docs/passport_<domain>.md` пересоздаётся при каждой активации конфига
- `GET /pdf/{pub_id}` (inline), `GET /pdf/{pub_id}/page/{n}/text`
- Правки (annotator+): `PATCH /records/{id} {data:{..},comment}` → пишет `record_history`, `status='manual'`, вызывает `pipeline.revalidate(pub)`; `POST /records {pub_id,entity,data}` (rid присваивается: `R###`/`<prefix>##` следующий свободный); `POST /records/{id}/action {action:'confirm'|'reject'|'restore',comment}`; `POST /issues/{id}/resolve {status:'accepted'|'fixed'|'kept',comment}`; `GET /records/{id}/history`
- Gold и качество (annotator+/admin): `POST /gold/import {domain, dir}` (читает CSV-таблицы папки в `gold_records`, publication_id → pub_id), `POST /gold/from_publication {pub_id}` (копирует текущие записи в gold для ручной разметки), `GET /gold?domain=`, `PATCH /gold/{pub_id}/{entity}/{rid}`; `POST /quality/run {domain,pub_ids,note}` → считает precision/recall/F1 по полям (после нормализации: числа с допуском 1e-6, строки: lower+схлопнутые пробелы; сущности сопоставляются по `schema.eval.match_fields`; результаты — по ключу из `match_fields.results`), сводная F1 по области, доля значений с валидной цитатой, покрытие корпуса; сохраняет в `eval_runs`; **регрессия**: если F1 по тому же набору pub_ids упала >0.01 против предыдущего прогона — `regression:true` в ответе; `GET /quality/runs?domain=`, `GET /quality/runs/{id}`
- Админ: `GET/POST/PATCH/DELETE /users`; `GET /corpus` (источники + сводка инвентаризации), `POST /corpus/sources {domain,path,split}`, `POST /corpus/scan {domain,path?}` (→ `pipeline.scan_source`), `GET /corpus/inventory?domain=`, `POST /upload` (multipart `files`, поле `domain`) ; `GET /jobs?domain=` (публикации + стадии), `POST /jobs/run {pub_ids|domain+all:true, from_stage?}`, `POST /jobs/{pub}/retry`; `GET /llm_calls?pub=&limit=`, `GET /llm_calls/{id}`; `GET /ocr/queue`, `POST /ocr/run {pub_ids}`, `GET /ocr/status`; `GET /config/{domain}` → `{schema_yaml,rules_yaml,versions:[{kind,version,author,ts,active,comment}]}`, `PUT /config/{domain}/{kind} {yaml,comment}` → новая версия (валидация), `POST /config/{domain}/{kind}/activate {version}`, `POST /config/domains {domain,schema_yaml,rules_yaml}` (новая область без правки кода); `GET/POST /settings` (ключ только на запись; в ответе `key_set`), `POST /settings/test`; `GET /audit?limit=`
- Совместимость с прежним сайтом: `GET /data?domain=` → `{tables:{entity:[rows]}, passport}` (все не-rejected записи как плоские строки, как раньше).
- Статика: `/` отдаёт `site/`.

## Фронтенд (`site/`, фронт-агент): один SPA на hash-роутинге, без сборщика, vanilla JS, стиль как в существующем `site/style.css`
Пользователь: обзор-дашборд (покрытие, доля verified, замечания по правилам, последний прогон качества), каталог публикаций, записи с полнотекстовым поиском и фильтрами, карточка записи (значение, единица, условия, статус, цитата, «показать в источнике»), сравнение (выбор записей → блокировка несопоставимых с причинами), замечания, паспорт (из `/api/passport`), экспорт CSV/JSON. Переключатель области. Вход (роль) в шапке.
«Показать в источнике»: `site/viewer.html?pub=ID&page=N&q=ЦИТАТА` — pdf.js (cdnjs.cloudflare.com, фиксированная версия), открывает страницу и подсвечивает текстовый слой по цитате (токены, нормализация как в verify).
Админка (`#/admin/...`, видна admin/annotator): корпус (источники, инвентаризация, дубликаты, границы, очередь OCR), конвейер (таблица стадий по статьям, повтор с места сбоя, логи LLM-вызовов с промптом/ответом), редактор записей (правка полей по схеме, подтвердить/отклонить, история), очередь замечаний (принять/исправить/оставить + комментарий), конструктор схем и правил (YAML-редактор с валидацией, версии, активировать, новая область), качество (gold, запуск оценки, таблица по полям, история, красный флаг регрессии), пользователи и аудит, настройки модели/ключа.

## Тесты и запуск
`.venv/bin/python -m pytest -q tests` (pytest ставит тот, кто пишет первые тесты). Сервер: `.venv/bin/uvicorn app.server:app --port 8765`. Тесты не ходят во внешнюю сеть: LLM подменяется заглушкой (`app.llm.call_json` monkeypatch). БД в тестах — временный файл (`db.DB_PATH` переопределяется до `init_db`).

## Разделение файлов (чтобы агенты не пересекались)
- rules-агент: `app/rules.py`, `tests/test_rules.py`, `tests/test_normalize.py`.
- pipeline-агент: `app/llm.py`, `app/pipeline.py`, `app/ocr.py`, `app/verify.py`, `app/legacy_pipeline.py` (rename), `tests/test_verify.py`, `tests/test_pipeline.py`.
- API-агент: `app/server.py`, `app/api/*.py`, `app/auth.py`, `app/passport.py`, `app/evaluation.py`, `app/compare.py`, `app/migrate.py`, `tests/test_api.py`, `tests/test_passport.py`, `tests/test_compare.py`, `tests/test_evaluation.py`.
- фронт-агент: всё внутри `site/`.
Чужие файлы не править; если нужна правка контракта — написать в отчёте.
