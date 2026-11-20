import copy
import logging
import pathlib
import sys

import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import app.rules as R  # noqa: E402


def cfg_of(domain):
    d = ROOT / "config" / "domains" / domain
    return {"domain": domain, "schema": yaml.safe_load((d / "schema.yaml").read_text(encoding="utf-8")),
            "rules": yaml.safe_load((d / "rules.yaml").read_text(encoding="utf-8"))}


SOLAR, RAG = cfg_of("solar"), cfg_of("rag")
_n = [0]


def res(metric, value, dev="DV1", ms="MS1", agg="", **kw):
    _n[0] += 1
    d = {"result_id": f"R{_n[0]:03d}", "device_id": dev, "measurement_id": ms, "metric_name": metric,
         "normalized_value": str(value), "aggregation": agg, "result_origin": "primary",
         "source_page": "3", "source_fragment": f"frag {metric}"}
    d.update(kw)
    return d


def ids(issues, rule):
    return [i for i in issues if i["rule_id"] == rule]


def run(cfg, **tables):
    return R.run_rules(cfg, tables)


# ---- issue-формат
def test_issue_shape():
    out = run(SOLAR, results=[res("FF", 95)])
    i = ids(out, "range_ff")[0]
    assert i["rule_version"] == 1 and i["severity"] == "warning" and i["record_ids"] == [f"R{_n[0]:03d}"]
    assert i["source_page"] == "3" and i["source_fragment"] == "frag FF" and "95" in i["description"]


# ---- formula_consistency
def jv(pce, voc=1.0, jsc=20, ff=70, **kw):
    return [res("PCE", pce, **kw), res("Voc", voc, **kw), res("Jsc", jsc, **kw), res("FF", ff, **kw)]


def test_jv_violation_and_norm():
    assert ids(run(SOLAR, results=jv(18)), "jv_consistency")     # расчёт 14
    assert not ids(run(SOLAR, results=jv(14)), "jv_consistency")


def test_jv_boundary():
    # расчёт = 14; допуск 3 % от PCE
    assert not ids(run(SOLAR, results=jv(13.6)), "jv_consistency")     # 2.9 %
    assert ids(run(SOLAR, results=jv(13.5)), "jv_consistency")         # 3.7 %
    assert not ids(run(SOLAR, results=jv(14.2)), "jv_consistency")     # 1.4 %
    assert ids(run(SOLAR, results=jv(14.5)), "jv_consistency")         # 3.4 %


def test_jv_missing_values_and_default_pin():
    assert not ids(run(SOLAR, results=jv(18)[:3]), "jv_consistency")             # нет FF
    c = copy.deepcopy(SOLAR)
    del c["rules"]["rules"][0]["params"]["defaults"]["Pin"]
    assert not ids(run(c, results=jv(18)), "jv_consistency")                       # Pin нет и нет defaults
    c["rules"]["rules"][0]["params"]["defaults"]["Pin"] = 50                       # Pin из конфига
    assert ids(run(c, results=jv(14)), "jv_consistency")


def test_jv_groups_cited_duplicates_time():
    a = jv(18, dev="DV1", agg="champion")
    b = jv(14, dev="DV1", agg="mean")
    out = ids(run(SOLAR, results=a + b), "jv_consistency")
    assert len(out) == 1 and set(out[0]["record_ids"]) == {x["result_id"] for x in a}
    assert not ids(run(SOLAR, results=jv(18, result_origin="cited")), "jv_consistency")
    assert not ids(run(SOLAR, results=jv(18, duplicate_of="R001")), "jv_consistency")
    assert not ids(run(SOLAR, results=jv(18, time_point="100")), "jv_consistency")
    assert not ids(run(SOLAR, results=jv(18, status="rejected")), "jv_consistency")


# ---- bound
def test_bound_ff_inclusive_edges():
    for v, bad in ((9.99, True), (10, False), (90, False), (90.01, True), (50, False)):
        assert bool(ids(run(SOLAR, results=[res("FF", v)]), "range_ff")) is bad, v


def test_bound_exclusive():
    for v, bad in ((0, True), (0.01, False), (35, True), (34.9, False), (-1, True)):
        assert bool(ids(run(SOLAR, results=[res("PCE", v)]), "range_pce")) is bad, v
    assert ids(run(SOLAR, results=[res("Jsc", 50)]), "range_jsc")


def test_bound_missing_value_and_cited_still_checked():
    assert not ids(run(SOLAR, results=[res("FF", "")]), "range_ff")
    assert ids(run(SOLAR, results=[res("FF", 95, result_origin="cited")]), "range_ff")


# ---- inequality
def mats(eg="1.2"):
    return {"materials": [{"material_id": "M1", "bandgap_value": eg}], "devices": [{"device_id": "DV1", "absorber_material_id": "M1"}]}


def test_inequality_voc_vs_bandgap():
    t = mats("1.2")
    assert ids(run(SOLAR, results=[res("Voc", 1.3)], **t), "voc_limit")
    assert ids(run(SOLAR, results=[res("Voc", 1.2)], **t), "voc_limit")           # граница: должно быть строго меньше
    assert not ids(run(SOLAR, results=[res("Voc", 1.19)], **t), "voc_limit")
    i = ids(run(SOLAR, results=[res("Voc", 1.3)], **t), "voc_limit")[0]
    assert i["severity"] == "error" and "1.2" in i["description"]


def test_inequality_no_bandgap_or_link():
    assert not ids(run(SOLAR, results=[res("Voc", 5)], **mats("")), "voc_limit")
    assert not ids(run(SOLAR, results=[res("Voc", 5)]), "voc_limit")
    assert not ids(run(SOLAR, results=[res("Voc", 5, device_id="DV9")], **mats()), "voc_limit")
    assert not ids(run(SOLAR, results=[res("Voc", 5, result_origin="cited")], **mats()), "voc_limit")


# ---- pair_diff
def test_pair_diff():
    f = lambda j, e: run(SOLAR, results=[res("Jsc", j), res("Jsc_EQE", e)])
    assert ids(f(20, 21.5), "eqe_consistency")        # 7.5 %
    assert not ids(f(20, 20.5), "eqe_consistency")    # 2.5 %
    assert not ids(f(20, 21), "eqe_consistency")      # ровно 5 % — не нарушение
    assert not ids(run(SOLAR, results=[res("Jsc", 20)]), "eqe_consistency")
    assert not ids(run(SOLAR, results=[res("Jsc", 20), res("Jsc_EQE", 30, device_id="DV2")]), "eqe_consistency")


# ---- champion_vs_mean
def cm(c, m, metric="PCE"):
    return run(SOLAR, results=[res(metric, c, agg="champion"), res(metric, m, agg="mean")])


def test_champion_vs_mean():
    assert ids(cm(13.66, 14.66), "champion_vs_mean")      # champion < mean
    assert not ids(cm(15, 14), "champion_vs_mean")
    assert not ids(cm(14, 14), "champion_vs_mean")        # равны
    assert ids(cm(21.1, 14), "champion_vs_mean")          # >50 %
    assert not ids(cm(21, 14), "champion_vs_mean")        # ровно 50 %
    assert not ids(run(SOLAR, results=[res("PCE", 20, agg="champion")]), "champion_vs_mean")
    assert not ids(run(SOLAR, results=[res("PCE", 10, agg="champion", result_origin="cited"), res("PCE", 14, agg="mean")]), "champion_vs_mean")


# ---- separate_values
def test_separate_values():
    ms = [{"measurement_id": "MS1", "scan_direction": "reverse"}, {"measurement_id": "MS2", "scan_direction": "forward"}]
    i = ids(run(SOLAR, measurements=ms), "scan_direction_mix")
    assert len(i) == 1 and i[0]["severity"] == "info" and i[0]["record_ids"] == []
    assert not ids(run(SOLAR, measurements=ms[:1]), "scan_direction_mix")


# ---- RAG
def rr(metric, value, unit="", **kw):
    _n[0] += 1
    d = {"result_id": f"R{_n[0]:03d}", "experiment_id": "E1", "config_id": "C1", "dataset_id": "DS1", "task_id": "T1",
         "metric_name": metric, "normalized_value": str(value), "normalized_unit": unit, "result_origin": "primary"}
    d.update(kw)
    return d


RT = {"experiments": [{"experiment_id": "E1"}], "configurations": [{"config_id": "C1"}],
      "datasets": [{"dataset_id": "DS1"}], "tasks": [{"task_id": "T1"}]}


def test_metric_scale():
    f = lambda m, v, u: ids(run(RAG, results=[rr(m, v, u)], **RT), "metric_scale")
    assert f("F1", 1.5, "fraction")
    assert not f("F1", 1, "fraction") and not f("F1", 0, "fraction")
    assert f("F1", 101, "%") and f("F1", -1, "%")
    assert not f("F1", 100, "%")
    assert not f("F1", 150, "")                 # шкала неизвестна
    assert not f("BLEU", 35, "")                # BLEU без шкалы не проверяется
    assert not f("Latency", 500, "ms")
    assert not f("EM decrease", 150, "%")       # приращения не метрика-шкала
    assert not f("F1", "", "%")                 # нет значения


def test_k_required():
    f = lambda m, k="": ids(run(RAG, results=[rr(m, 0.5, "fraction", metric_k=k)], **RT), "metric_k")
    assert f("NDCG@5")                           # k в имени, metric_k пуст
    assert "k=5" in f("NDCG@5")[0]["description"]
    assert not f("NDCG@5", "5")
    assert f("NDCG@5", "10")                     # расхождение
    assert f("Recall@")                          # k нигде нет
    assert not f("F1")


def test_link_integrity():
    f = lambda **kw: ids(run(RAG, results=[rr("F1", 0.5, "fraction", **kw)], **RT), "result_links")
    assert not f()
    assert f(experiment_id="") and f(config_id="C9") and f(dataset_id="")
    assert not f(task_id="")                      # optional_ok
    assert f(task_id="T9")                        # непустое, но несуществующее
    assert not ids(run(RAG, results=[rr("F1", 0.5, "fraction", config_id="", result_origin="cited")], **RT), "result_links")
    i = f(experiment_id="")[0]
    assert i["severity"] == "error"


def test_no_merge_info_only():
    rs = [rr("F1", 0.5, "fraction"), rr("F1", 0.6, "fraction", experiment_id="E2")]
    out = ids(run(RAG, results=rs, **RT), "protocol_mismatch")
    assert len(out) == 1 and out[0]["severity"] == "info"
    assert rs[0]["normalized_value"] == "0.5"
    assert not ids(run(RAG, results=rs[:1], **RT), "protocol_mismatch")


# ---- общее
def test_unknown_type_warns_not_fails(caplog):
    c = copy.deepcopy(SOLAR)
    c["rules"]["rules"].append({"id": "x", "type": "nonexistent", "params": {}})
    with caplog.at_level(logging.WARNING):
        out = run(c, results=[res("FF", 95)])
    assert ids(out, "range_ff") and any("nonexistent" in m for m in caplog.messages)


def test_rules_come_from_config():
    base = run(SOLAR, results=[res("FF", 85)])
    assert not ids(base, "range_ff")
    c = copy.deepcopy(SOLAR)
    next(r for r in c["rules"]["rules"] if r["id"] == "range_ff")["params"]["max"] = 80
    c["rules"]["version"] = 7
    out = run(c, results=[res("FF", 85)])
    assert ids(out, "range_ff") and ids(out, "range_ff")[0]["rule_version"] == 7
    c2 = copy.deepcopy(SOLAR)
    next(r for r in c2["rules"]["rules"] if r["id"] == "jv_consistency")["params"]["tolerance_rel"] = 0.5
    assert not ids(run(c2, results=jv(18)), "jv_consistency")


def test_safe_eval():
    assert R.safe_eval("a*b+2**2/(1-3)", {"a": 3, "b": 4}) == 10
    assert R.safe_eval("1/a", {"a": 0}) is None
    for bad in ("__import__('os')", "a.real", "[1]", "a if 1 else 2", "9**99"):
        try:
            R.safe_eval(bad, {"a": 1})
            raise AssertionError(bad)
        except (ValueError, KeyError, SyntaxError):
            pass


def test_empty_tables():
    assert run(SOLAR) == [] and run(RAG) == []
