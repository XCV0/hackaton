# Паспорт набора данных: Тонкоплёночные солнечные элементы

Область: `solar`. Версия конфигурации: **schema v5 / rules v1**. Документ сгенерирован автоматически из `schema.yaml` и `rules.yaml`.

## Границы области

Экспериментальные публикации с описанием изготовленного однопереходного тонкоплёночного солнечного элемента (перовскиты, CIGS, CdTe, кестериты, Sb2Se3, тонкоплёночный кремний) и его фотоэлектрических характеристик. Тандемы и расчётные работы без образцов не включаются.

## Сущности и поля

### publications

| Поле | Тип | Допустимые значения | По умолчанию | Связь | Описание |
|---|---|---|---|---|---|
| publication_id (id) | string |  |  |  |  |
| title | string |  |  |  |  |
| year | string |  |  |  |  |
| DOI | string |  |  |  |  |
| authors | string |  |  |  |  |
| in_scope | bool |  |  |  | относится ли статья к области |
| exclusion_reason | string |  |  |  |  |

### materials (родитель: publications)

| Поле | Тип | Допустимые значения | По умолчанию | Связь | Описание |
|---|---|---|---|---|---|
| publication_id | string |  |  |  |  |
| material_id (id) | string |  |  |  |  |
| material_name_raw | string |  |  |  |  |
| formula_normalized | string |  |  |  | для перовскита вид A-B-X с раскрытыми MA, FA, Cs |
| material_class | enum | perovskite, CIGS, CdTe, kesterite, Sb2Se3, thin-film Si, other |  |  |  |
| dimensionality | enum | 3D, 2D, 2D-3D |  |  |  |
| additives | string |  |  |  |  |
| bandgap_value | number |  |  |  |  |
| bandgap_unit | string |  | eV |  |  |
| bandgap_method | enum | Tauc, EQE, PL, cited |  |  |  |
| note | string |  |  |  |  |

### devices (родитель: publications)

| Поле | Тип | Допустимые значения | По умолчанию | Связь | Описание |
|---|---|---|---|---|---|
| publication_id | string |  |  |  |  |
| device_id (id) | string |  |  |  |  |
| absorber_material_id | string |  |  | materials |  |
| device_label | string |  |  |  |  |
| device_role | enum | control, target, record |  |  |  |
| device_type | enum | cell, minimodule, module |  |  |  |
| architecture | enum | n-i-p, p-i-n, substrate, superstrate |  |  |  |
| substrate_type | enum | rigid, flexible |  |  |  |
| active_area | number |  |  |  |  |
| area_unit | string |  | cm2 |  |  |
| area_definition | enum | designated illumination, aperture, total |  |  |  |
| encapsulation | string |  |  |  |  |
| note | string |  |  |  |  |

### layers (родитель: devices)

| Поле | Тип | Допустимые значения | По умолчанию | Связь | Описание |
|---|---|---|---|---|---|
| publication_id | string |  |  |  |  |
| layer_id (id) | string |  |  |  |  |
| device_id | string |  |  | devices |  |
| layer_order | int |  |  |  |  |
| layer_function | enum | front_contact, ETL, absorber, passivation, HTL, interlayer, recombination, back_contact |  |  |  |
| layer_material | string |  |  |  |  |
| layer_thickness | number |  |  |  |  |
| thickness_unit | string |  | nm |  |  |
| deposition_method | string |  |  |  |  |
| annealing_temperature | string |  |  |  |  |
| annealing_time | string |  |  |  |  |
| atmosphere | string |  |  |  |  |
| note | string |  |  |  |  |

### measurements (родитель: devices)

| Поле | Тип | Допустимые значения | По умолчанию | Связь | Описание |
|---|---|---|---|---|---|
| publication_id | string |  |  |  |  |
| measurement_id (id) | string |  |  |  |  |
| device_id | string |  |  | devices |  |
| measurement_type | enum | J-V, MPP tracking, EQE, stability |  |  |  |
| spectrum | string |  |  |  |  |
| light_intensity | string |  |  |  |  |
| intensity_unit | string |  |  |  |  |
| scan_direction | enum | reverse, forward, average |  |  |  |
| scan_rate | string |  |  |  |  |
| mask_used | bool |  |  |  |  |
| certified | bool |  |  |  |  |
| certification_lab | string |  |  |  |  |
| temperature | string |  |  |  |  |
| stability_protocol | enum | ISOS-D, ISOS-L, ISOS-T, other |  |  |  |
| stress_conditions | string |  |  |  |  |
| note | string |  |  |  |  |

### results (ключевая сущность, родитель: publications)

| Поле | Тип | Допустимые значения | По умолчанию | Связь | Описание |
|---|---|---|---|---|---|
| publication_id | string |  |  |  |  |
| result_id (id) | string |  |  |  |  |
| device_id | string |  |  | devices |  |
| measurement_id | string |  |  | measurements |  |
| material_id | string |  |  | materials |  |
| metric_name | enum | PCE, FF, HI, retained_PCE, Voc, Jsc, Jsc_EQE, Rs, Rsh, T80 |  |  |  |
| metric_value | number |  |  |  |  |
| metric_unit | string |  |  |  |  |
| aggregation | enum | champion, mean, median |  |  | пусто, если в статье не сказано |
| spread_value | string |  |  |  |  |
| n_devices | string |  |  |  |  |
| time_point | string |  |  |  |  |
| time_unit | string |  |  |  |  |
| raw_value | string |  |  |  |  |
| raw_unit | string |  |  |  |  |
| normalized_value | number |  |  |  |  |
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

## Метрики и единицы

| Метрика | Единица | Вид | Пересчёт в единицу |
|---|---|---|---|
| PCE | % | percent |  |
| FF | % | percent |  |
| HI | % | percent |  |
| retained_PCE | % | percent |  |
| stabilized_PCE | % | percent |  |
| Voc | V |  | 1 mV = 0.001 V, 1 V = 1 V |
| Jsc | mA/cm2 |  | 1 A/cm2 = 1000 mA/cm2, 1 uA/cm2 = 0.001 mA/cm2, 1 mA/cm2 = 1 mA/cm2, 1 A/m2 = 0.1 mA/cm2, 1 mA/m2 = 0.0001 mA/cm2 |
| Jsc_EQE | mA/cm2 |  | 1 A/cm2 = 1000 mA/cm2, 1 mA/cm2 = 1 mA/cm2 |
| Rs | Ohm*cm2 |  |  |
| Rsh | Ohm*cm2 |  |  |
| T80 | h |  | 1 h = 1 h, 1 min = 0.016667 h, 1 day = 24 h, 1 d = 24 h |

## Правила проверки

| Правило | Тип | Уровень | Описание | Параметры и пороги |
|---|---|---|---|---|
| jv_consistency | formula_consistency | warning | PCE должно совпадать с Voc·Jsc·FF/Pin | `{"target": "PCE", "expr": "Voc*Jsc*FF/Pin", "defaults": {"Pin": 100}, "tolerance_rel": 0.03, "group_by": ["device_id", "measurement_id", "aggregation"], "pce_percent": true}` |
| voc_limit | inequality | error | Voc должно быть меньше Eg/q | `{"left": "Voc", "right": "bandgap_value", "right_from": "materials via devices.absorber_material_id"}` |
| range_ff | bound | warning | FF вне 10-90 % | `{"metric": "FF", "min": 10, "max": 90}` |
| range_jsc | bound | warning | Jsc вне 0-50 мА/см2 | `{"metric": "Jsc", "min": 0, "max": 50, "exclusive": true}` |
| range_pce | bound | warning | PCE вне 0-35 % | `{"metric": "PCE", "min": 0, "max": 35, "exclusive": true}` |
| eqe_consistency | pair_diff | warning | Jsc по ВАХ и по EQE расходятся | `{"a": "Jsc", "b": "Jsc_EQE", "tolerance_rel": 0.05, "group_by": ["device_id"]}` |
| champion_vs_mean | champion_vs_mean | warning | Лучший прибор хуже или аномально выше среднего той же партии | `{"metrics": ["PCE", "Voc", "Jsc", "FF"], "max_rel_excess": 0.5, "group_by": ["device_id", "measurement_id"]}` |
| scan_direction_mix | separate_values | info | Прямой и обратный ход хранятся и сравниваются раздельно | `{"field": "scan_direction", "entity": "measurements"}` |

## Критерии сопоставимости результатов

| Критерий | Путь | Режим | Параметр | Причина блокировки |
|---|---|---|---|---|
| metric | result.metric_name | equal |  | разные показатели не сравниваются |
| material_class | material.material_class | equal |  | разные классы материала |
| device_type | device.device_type | equal |  | разные типы прибора (ячейка/модуль) |
| spectrum | measurement.spectrum | equal |  | нужен стандартный спектр освещения AM1.5G |
| light_intensity | measurement.light_intensity | equal |  | нужна мощность света 100 мВт/см² |
| area | device.active_area | ratio_within | 3.0 | несопоставимая площадь прибора |
| aggregation | result.aggregation | equal |  | лучший прибор и среднее несравнимы |
| scan_direction | measurement.scan_direction | equal |  | разное направление развёртки |
| certified | measurement.certified | equal |  | сертифицированные значения сравниваются отдельно |
| time | result.time_point | equal |  | стабильность на разных моментах времени |

## Оценка качества

Сопоставление записей с эталоном по полям:

- results: metric_name, normalized_value, result_origin
- materials: material_class
- devices: device_role
- layers: layer_function, layer_material
- measurements: measurement_type, scan_direction
- числовые поля (допуск 1e-6 или 0,5 %): normalized_value, bandgap_value, layer_thickness, active_area

## Подсказки извлечения

- Слои перечисляй от подложки, подложку (стекло, фольга) не записывай, толщины в нм.
- Прямой и обратный ход развёртки — разные measurements.
- Значения лучшего прибора и средние по партии — разные строки; aggregation пустой, если в статье не сказано.
- Значения из чужих работ — result_origin=cited, device_id пустой.
- Если значение явно упоминается, но его нет в тексте PDF (только в supplementary, на графике без числовой подписи), создай запись с extraction_status=present_not_extractable, пустыми raw_value и числовыми полями, а source_fragment — дословное место упоминания. Число по графику не выдумывай.
- Название метрики — только из словаря (PCE, Voc, Jsc, FF, ...). Если в таблице колонки «mean/average» и «best/champion», запиши одну и ту же метрику двумя строками: aggregation=mean и aggregation=champion; слова mean/best в metric_name не пиши.
- Для значения из таблицы source_fragment — ВСЯ строка таблицы дословно (метка строки и все ячейки подряд, как в блоке [TABLE], например «nDCG@10 | 0.55 | 0.64»), а не отдельная ячейка: цитата должна идти в тексте страницы непрерывно.
