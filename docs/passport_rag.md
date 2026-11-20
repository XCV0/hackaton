# Паспорт набора данных: RAG-системы и поиск по документам

Область: `rag`. Версия конфигурации: **schema v1 / rules v1**. Документ сгенерирован автоматически из `schema.yaml` и `rules.yaml`.

## Границы области

Публикации с описанием конфигурации RAG (система находит сведения во внешней коллекции документов и использует найденный контекст при ответе) и результатов её оценки. Решение по содержанию, а не по слову RAG в заголовке.

## Сущности и поля

### publications

| Поле | Тип | Допустимые значения | По умолчанию | Связь | Описание |
|---|---|---|---|---|---|
| publication_id (id) | string |  |  |  |  |
| title | string |  |  |  |  |
| year | string |  |  |  |  |
| DOI | string |  |  |  |  |
| authors | string |  |  |  |  |
| in_scope | bool |  |  |  |  |
| exclusion_reason | string |  |  |  |  |

### configurations (родитель: publications)

| Поле | Тип | Допустимые значения | По умолчанию | Связь | Описание |
|---|---|---|---|---|---|
| publication_id | string |  |  |  |  |
| config_id (id) | string |  |  |  |  |
| rag_variant | string |  |  |  |  |
| retrieval_method | string |  |  |  |  |
| retriever_model | string |  |  |  |  |
| embedding_model | string |  |  |  |  |
| embedding_version | string |  |  |  |  |
| chunking_method | string |  |  |  |  |
| chunk_size | number |  |  |  |  |
| chunk_overlap | number |  |  |  |  |
| chunk_unit | string |  |  |  |  |
| index_type | string |  |  |  |  |
| vector_store | string |  |  |  |  |
| top_k | int |  |  |  | не путать с k метрики; диапазон не усреднять |
| reranker_model | string |  |  |  |  |
| generator_model | string |  |  |  |  |
| generator_version | string |  |  |  |  |
| prompt_strategy | string |  |  |  |  |
| context_strategy | string |  |  |  |  |
| note | string |  |  |  |  |

### datasets (родитель: publications)

| Поле | Тип | Допустимые значения | По умолчанию | Связь | Описание |
|---|---|---|---|---|---|
| publication_id | string |  |  |  |  |
| dataset_id (id) | string |  |  |  |  |
| dataset_name | string |  |  |  |  |
| dataset_version | string |  |  |  |  |
| language | string |  |  |  |  |
| data_split | string |  |  |  |  |
| scientific_domain | string |  |  |  |  |
| note | string |  |  |  |  |

### tasks (родитель: publications)

| Поле | Тип | Допустимые значения | По умолчанию | Связь | Описание |
|---|---|---|---|---|---|
| publication_id | string |  |  |  |  |
| task_id (id) | string |  |  |  |  |
| task_type | string |  |  |  |  |
| note | string |  |  |  |  |

### experiments (родитель: publications)

| Поле | Тип | Допустимые значения | По умолчанию | Связь | Описание |
|---|---|---|---|---|---|
| publication_id | string |  |  |  |  |
| experiment_id (id) | string |  |  |  |  |
| evaluation_protocol | string |  |  |  |  |
| baseline_name | string |  |  |  |  |
| hardware | string |  |  |  |  |
| latency_ms | number |  |  |  |  |
| tokens_per_query | number |  |  |  |  |
| sample_size | int |  |  |  |  |
| note | string |  |  |  |  |

### results (ключевая сущность, родитель: publications)

| Поле | Тип | Допустимые значения | По умолчанию | Связь | Описание |
|---|---|---|---|---|---|
| publication_id | string |  |  |  |  |
| result_id (id) | string |  |  |  |  |
| experiment_id | string |  |  | experiments |  |
| config_id | string |  |  | configurations |  |
| dataset_id | string |  |  | datasets |  |
| task_id | string |  |  | tasks |  |
| metric_group | string |  |  |  |  |
| metric_name | string |  |  |  |  |
| metric_variant | string |  |  |  |  |
| metric_direction | enum | higher_is_better, lower_is_better, target |  |  |  |
| metric_k | int |  |  |  |  |
| metric_value | number |  |  |  |  |
| metric_unit | string |  |  |  |  |
| baseline_value | number |  |  |  |  |
| aggregation | string |  |  |  |  |
| sample_size | string |  |  |  |  |
| raw_value | string |  |  |  |  |
| raw_unit | string |  |  |  |  |
| normalized_value | string |  |  |  |  |
| normalized_unit | string |  |  |  |  |
| result_origin | enum | primary, cited |  |  |  |
| cited_publication_id | string |  |  |  |  |
| source_page | int |  |  |  |  |
| source_section | string |  |  |  |  |
| source_table | string |  |  |  |  |
| source_figure | string |  |  |  |  |
| source_fragment | string |  |  |  |  |
| extraction_status | enum | extracted, present_not_extractable, absent |  |  |  |
| duplicate_of | string |  |  |  |  |
| note | string |  |  |  |  |

### issues (родитель: publications)

| Поле | Тип | Допустимые значения | По умолчанию | Связь | Описание |
|---|---|---|---|---|---|
| publication_id | string |  |  |  |  |
| issue_id (id) | string |  |  |  |  |
| record_ids | string |  |  |  |  |
| check_name | string |  |  |  |  |
| description | string |  |  |  |  |
| source_page | string |  |  |  |  |
| source_fragment | string |  |  |  |  |

## Правила проверки

| Правило | Тип | Уровень | Описание | Параметры и пороги |
|---|---|---|---|---|
| metric_scale | metric_scale | warning | Значение должно лежать в допустимой шкале метрики | `{"scales": {"fraction": [0, 1], "percent": [0, 100]}, "by_unit": {"%": [0, 100], "fraction": [0, 1], "ratio": [0, 1]}, "by_metric_regex": {"(?i)^(f1\|em\|exact.?match\|recall\|precision\|accuracy\|mrr\|ndcg\|map\|hit\|hits\|rouge\|bleu\|bertscore)": "unit_scale"}, "bleu_rouge_note": "BLEU/ROUGE не пересчитываются без известной шкалы"}` |
| metric_k | k_required | warning | Для метрик @k должно быть указано k | `{"name_regex": "@\\s*(\\d+)\|(?i)(recall\|precision\|ndcg\|hit\|hits\|mrr\|map)\\s*@", "extract_k_from_name": true}` |
| result_links | link_integrity | error | Результат связан с экспериментом, конфигурацией, набором данных и задачей | `{"required": ["experiment_id", "config_id", "dataset_id", "task_id"], "optional_ok": ["task_id"]}` |
| protocol_mismatch | no_merge | info | Разные эксперименты и варианты метрик не объединяются | `{"key": ["task_id", "dataset_id", "metric_name", "metric_variant", "experiment_id"]}` |

## Критерии сопоставимости результатов

| Критерий | Путь | Режим | Параметр | Причина блокировки |
|---|---|---|---|---|
| task | task.task_type | equal |  | разные задачи |
| dataset | dataset.dataset_name | equal |  | разные наборы данных |
| dataset_version | dataset.dataset_version | equal |  | разные версии набора |
| split | dataset.data_split | equal |  | разные разбиения данных |
| metric | result.metric_name | equal |  | разные метрики |
| metric_variant | result.metric_variant | equal |  | разные определения метрики |
| metric_k | result.metric_k | equal |  | разное k метрики |
| protocol | experiment.evaluation_protocol | equal |  | разные протоколы оценки |

## Оценка качества

Сопоставление записей с эталоном по полям:

- results: metric_name, metric_variant, metric_k, normalized_value, result_origin
- configurations: rag_variant
- datasets: dataset_name
- tasks: task_type
- experiments: evaluation_protocol
- числовые поля (допуск 1e-6 или 0,5 %): normalized_value, chunk_size, top_k, sample_size

## Подсказки извлечения

- Разные генераторы, ablation-варианты и настройки — отдельные configurations; параметр-диапазон не усреднять, оставить пустым и описать в note.
- Не достраивать версии моделей и параметры по известным настройкам библиотек или другим статьям.
- Одно значение одной метрики в одном эксперименте — одна строка results; повторы в тексте тоже записывай.
- Доли/проценты/единицы времени не пересчитывай без известной шкалы; исходное значение — в raw_value/raw_unit.
