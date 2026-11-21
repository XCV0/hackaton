from app import compare, config, db
from app.api import common as C
from .helpers import seed_solar, put_rec, put_pub, env  # noqa: F401


def _recs(ids, keys):
    return [C.rec_out(db.q1("SELECT * FROM records WHERE id=?", (ids[k],))) for k in keys]


def test_comparable_pair():
    ids = seed_solar()
    cfg = config.load_domain("solar")
    r = compare.compare(cfg, _recs(ids, ["R001", "R002"]))
    assert r["comparable"] and r["blocked"] == []
    assert r["rows"][0]["resolved"]["material_class"] == "perovskite"   # result -> material
    assert r["rows"][0]["resolved"]["area"] == "0.1" and r["rows"][1]["resolved"]["area"] == "0.12"   # result -> device
    assert r["rows"][0]["resolved"]["spectrum"] == "AM1.5G"   # result -> measurement
    assert r["groups"] == [[ids["R001"], ids["R002"]]]


def test_blocked_reasons():
    ids = seed_solar()
    cfg = config.load_domain("solar")
    r = compare.compare(cfg, _recs(ids, ["R001", "R003", "R004", "R005"]))
    assert not r["comparable"]
    pairs = {(b["a"], b["b"]): {x["criterion"]: x for x in b["reasons"]} for b in r["blocked"]}
    ab = pairs[(ids["R001"], ids["R003"])]          # площадь 0.1 vs 1.0: больше чем в 3 раза
    assert list(ab) == ["area"] and ab["area"]["a_value"] == "0.1" and ab["area"]["b_value"] == "1.0" and "несопоставимая площадь" in ab["area"]["reason"]
    assert "metric" in pairs[(ids["R001"], ids["R004"])] and "разные показатели" in pairs[(ids["R001"], ids["R004"])]["metric"]["reason"]
    miss = pairs[(ids["R001"], ids["R005"])]["spectrum"]   # пустой спектр
    assert "значение не указано в статье" in miss["reason"] and miss["b_value"] == "" and miss["a_value"] == "AM1.5G"
    assert [ids["R001"]] in r["groups"] or any(ids["R001"] in g for g in r["groups"])
    assert all(len(g) == 1 for g in r["groups"])


def test_ratio_within_boundary_and_empty_value():
    cfg = config.load_domain("solar")
    crit = [c for c in cfg["schema"]["compare"]["criteria"] if c["name"] == "area"]
    mk = lambda v: {"resolved": {"area": v}}
    assert compare.check_pair(crit, mk("1"), mk("3")) == []
    assert compare.check_pair(crit, mk("1"), mk("3.01"))
    assert "значения не являются" in compare.check_pair(crit, mk("abc"), mk("1"))[0]["reason"]
    assert "не указано" in compare.check_pair(crit, mk(""), mk("1"))[0]["reason"]
    assert compare.check_pair([{**crit[0], "allow_empty": True}], mk(""), mk("")) == []


def test_candidates_and_api():
    from fastapi.testclient import TestClient
    from app.server import app
    ids = seed_solar()
    put_pub("P2")
    cfg = config.load_domain("solar")
    base = C.rec_out(db.q1("SELECT * FROM records WHERE id=?", (ids["R001"],)))
    pool = [C.rec_out(r) for r in db.q("SELECT * FROM records WHERE entity='results' AND id!=?", (ids["R001"],))]
    assert compare.candidates(cfg, base, pool) == [ids["R002"]]
    with TestClient(app) as c:
        assert c.get("/api/compare/candidates?record_id=%d" % ids["R001"]).json() == [ids["R002"]]
        r = c.post("/api/compare", json={"domain": "solar", "record_ids": [ids["R001"], ids["R002"]]}).json()
        assert r["comparable"] is True
        r = c.post("/api/compare", json={"domain": "solar", "record_ids": [ids["R001"], ids["R004"]]}).json()
        assert r["comparable"] is False and r["blocked"][0]["reasons"][0]["criterion"] == "metric"
        assert c.post("/api/compare", json={"domain": "solar", "record_ids": [ids["R001"]]}).status_code == 400
        assert c.post("/api/compare", json={"domain": "solar", "record_ids": [ids["R001"], 99999]}).status_code == 404
        mat = db.q1("SELECT id FROM records WHERE entity='materials'")["id"]
        assert c.post("/api/compare", json={"domain": "solar", "record_ids": [ids["R001"], mat]}).status_code == 400


def test_rag_domain_paths():
    """Другая область: пути dataset.* / task.* / experiment.* резолвятся через refs результата."""
    put_pub("G1", domain="rag")
    kw = {"domain": "rag"}
    put_rec("G1", "tasks", "T1", {"task_id": "T1", "task_type": "QA"}, **kw)
    put_rec("G1", "datasets", "DS1", {"dataset_id": "DS1", "dataset_name": "NQ", "dataset_version": "1", "data_split": "test"}, **kw)
    put_rec("G1", "experiments", "E1", {"experiment_id": "E1", "evaluation_protocol": "p"}, **kw)
    ra = put_rec("G1", "results", "R001", {"result_id": "R001", "task_id": "T1", "dataset_id": "DS1", "experiment_id": "E1", "metric_name": "EM", "metric_value": "0.5"}, **kw)
    rb = put_rec("G1", "results", "R002", {"result_id": "R002", "task_id": "T1", "dataset_id": "DS1", "experiment_id": "E1", "metric_name": "EM", "metric_value": "0.6"}, **kw)
    cfg = config.load_domain("rag")
    recs = [C.rec_out(db.q1("SELECT * FROM records WHERE id=?", (i,))) for i in (ra, rb)]
    r = compare.compare(cfg, recs)
    res = r["rows"][0]["resolved"]
    assert res["task"] == "QA" and res["dataset"] == "NQ" and res["split"] == "test"


def test_solar_comparison_requires_standard_illumination():
    criteria = config.load_domain("solar")["schema"]["compare"]["criteria"]
    pair = [c for c in criteria if c["name"] in ("spectrum", "light_intensity")]
    wrong = {"resolved": {"spectrum": "AM0", "light_intensity": "60"}}
    assert {r["criterion"] for r in compare.check_pair(pair, wrong, wrong)} == {"spectrum", "light_intensity"}


def test_solar_irradiance_uses_measurement_units():
    ids = seed_solar()
    cfg = config.load_domain("solar")
    recs = _recs(ids, ["R001", "R002"])
    measurement = db.q1("SELECT id,data FROM records WHERE entity='measurements' AND rid='MS1'")
    data = db.jl(measurement["data"])
    for intensity, unit, comparable in (("1000", "W/m²", True), ("1", "sun", True), ("100", "W/m²", False), ("100", "", False)):
        db.ex("UPDATE records SET data=? WHERE id=?", (db.jd({**data, "light_intensity": intensity, "intensity_unit": unit}), measurement["id"]))
        assert compare.compare(cfg, recs)["comparable"] is comparable
