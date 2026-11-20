import copy
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


def n(cfg, metric, v, u="", **kw):
    return R.normalize_record(cfg, "results", {"metric_name": metric, "raw_value": v, "raw_unit": u, **kw})


def test_mv_to_v():
    r = n(SOLAR, "Voc", "1197", "mV")
    assert (r["normalized_value"], r["normalized_unit"]) == ("1.197", "V")
    assert r["raw_value"] == "1197" and r["raw_unit"] == "mV"
    assert (r["metric_value"], r["metric_unit"]) == ("1.197", "V")


def test_voc_in_volts_and_unicode_minus_comma():
    assert n(SOLAR, "Voc", "0,95", "V")["normalized_value"] == "0.95"
    assert n(SOLAR, "Rs", "−2,5", "Ohm cm2")["normalized_value"] == "-2.5"


def test_a_cm2_to_ma_cm2():
    assert n(SOLAR, "Jsc", "0.0205", "A/cm2")["normalized_value"] == "20.5"
    assert n(SOLAR, "Jsc", "20,5", "mA·cm−2")["normalized_value"] == "20.5"
    assert n(SOLAR, "Jsc", "250", "uA/cm2")["normalized_value"] == "0.25"
    assert n(SOLAR, "Jsc", "20.5", "mA/cm2")["normalized_unit"] == "mA/cm2"


def test_fraction_to_percent():
    assert n(SOLAR, "PCE", "0.185", "fraction")["normalized_value"] == "18.5"
    assert n(SOLAR, "PCE", "0.185", "доля")["normalized_unit"] == "%"
    assert n(SOLAR, "PCE", "18.5", "%")["normalized_value"] == "18.5"


def test_ff_071_to_71_without_unit():
    r = n(SOLAR, "FF", "0.71")
    assert (r["normalized_value"], r["normalized_unit"]) == ("71", "%")
    assert n(SOLAR, "FF", "71")["normalized_value"] == "71"
    assert n(SOLAR, "FF", "0.71 %")["normalized_value"] == "0.71"       # явный % не пересчитывается


def test_unknown_unit_gives_empty():
    r = n(SOLAR, "Voc", "5", "kg")
    assert r["normalized_value"] == "" and r["raw_value"] == "5"


def test_garbage_values():
    for bad in ("nan", "inf", "", None, "10–20", "10-20", "n/a", "<5", "abc"):
        r = n(SOLAR, "PCE", bad, "%")
        assert r["normalized_value"] == "", bad
        assert r["raw_value"] == bad


def test_plusminus_stripped():
    assert n(SOLAR, "PCE", "15.2 ± 0.3", "%")["normalized_value"] == "15.2"


def test_percent_inside_value_text():
    assert n(SOLAR, "PCE", "18.5%")["normalized_value"] == "18.5"


def test_time_conversion():
    assert n(SOLAR, "T80", "2", "day")["normalized_value"] == "48"


def test_cited_records_normalized_too():
    r = n(SOLAR, "Voc", "1100", "mV", result_origin="cited")
    assert r["normalized_value"] == "1.1"


def test_non_result_entity_untouched():
    d = {"material_id": "M1", "bandgap_value": "1,6"}
    assert R.normalize_record(SOLAR, "materials", d) == d


def test_input_not_mutated():
    d = {"metric_name": "Voc", "raw_value": "900", "raw_unit": "mV"}
    c = copy.deepcopy(d)
    R.normalize_record(SOLAR, "results", d)
    assert d == c


def test_rag_percent_and_fraction_kept_as_is():
    r = n(RAG, "F1", "62.4", "%")
    assert (r["normalized_value"], r["normalized_unit"]) == ("62.4", "%")
    r = n(RAG, "EM", ".204", "fraction")
    assert (r["normalized_value"], r["normalized_unit"]) == ("0.204", "fraction")


def test_rag_unknown_scale_not_recomputed():
    r = n(RAG, "BLEU", "0.35")
    assert r["normalized_value"] == "" and r["raw_value"] == "0.35"
    r = n(RAG, "F1", "0.35", "")
    assert r["normalized_value"] == ""
    r = n(RAG, "Latency", "120", "ms")
    assert r["normalized_value"] == ""


def test_rag_scale_from_metric_unit():
    r = n(RAG, "EM", ".204", "", metric_unit="fraction")
    assert r["normalized_value"] == "0.204"


def test_normalize_all_both_domains_same_function():
    t = {"results": [{"metric_name": "Voc", "raw_value": "900", "raw_unit": "mV"}], "devices": [{"device_id": "DV1"}]}
    out = R.normalize_all(SOLAR, t)
    assert out["results"][0]["normalized_value"] == "0.9" and out["devices"] == [{"device_id": "DV1"}]
    t = {"results": [{"metric_name": "EM", "raw_value": "55", "raw_unit": "%"}]}
    assert R.normalize_all(RAG, t)["results"][0]["normalized_value"] == "55"


def test_conversion_comes_from_config():
    c = copy.deepcopy(SOLAR)
    c["schema"]["metrics"]["Voc"]["conversions"]["mV"] = 0.01
    assert n(c, "Voc", "1000", "mV")["normalized_value"] == "10"
    assert n(SOLAR, "Voc", "1000", "mV")["normalized_value"] == "1"


def test_rag_percent_text_to_fraction_target():
    r = n(RAG, "EM", "100%", "", metric_unit="fraction")
    assert (r["normalized_value"], r["normalized_unit"]) == ("1", "fraction")
    r = n(RAG, "EM", "0.5", "fraction", metric_unit="%")
    assert (r["normalized_value"], r["normalized_unit"]) == ("50", "%")
