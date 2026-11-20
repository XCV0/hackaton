import csv, pathlib
from fastapi.testclient import TestClient
from app import evaluation, db, config
from app.server import app
from .helpers import seed_solar, put_pub, put_rec, env  # noqa: F401

RES_COLS = config.load_domain("solar")["schema"]["entities"]["results"]["columns"]


def write_gold(d, rows, mats=None):
    d.mkdir(exist_ok=True)
    with open(d / "results.csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=RES_COLS)
        w.writeheader()
        for r in rows:
            w.writerow({"publication_id": "P1", **r})
    (d / "unknown_table.csv").write_text("publication_id,x\nP1,1\n", encoding="utf-8")


GOLD = [
    {"result_id": "G1", "metric_name": "PCE", "normalized_value": "20", "result_origin": "primary", "metric_value": "20", "aggregation": "champion", "source_page": "3"},
    {"result_id": "G2", "metric_name": "PCE", "normalized_value": "19.0", "result_origin": "primary", "metric_value": "19", "aggregation": "mean", "source_page": "3"},
    {"result_id": "G3", "metric_name": "Voc", "normalized_value": "1.1", "result_origin": "primary", "metric_value": "1.1", "aggregation": "champion"},
    {"result_id": "G4", "metric_name": "FF", "normalized_value": "78", "result_origin": "primary", "metric_value": "78"},
]


def test_import_and_run_hand_counted(tmp_path):
    ids = seed_solar()
    res = evaluation.import_gold("solar", write_gold(tmp_path / "gold", GOLD) or tmp_path / "gold")
    assert res["imported"] == {"results": 4} and res["pubs"] == ["P1"] and res["skipped"][0]["file"] == "unknown_table.csv"
    g = evaluation.list_gold("solar")
    assert len(g) == 4 and g[0]["pub_id"] == "P1" and "publication_id" not in g[0]["data"]
    m = evaluation.run("solar", None, "тест", "t")
    r = m["entities"]["results"]
    # соответствие по (metric_name, normalized_value, result_origin): R001<->G1, R002<->G2, R004<->G3; G4 не найден; R003, R005 лишние
    assert r["rows"] == {"gold": 4, "pred": 5, "matched": 3}
    # поля: metric_name 3tp+2fp(R003,R005)+1fn(G4); aggregation: G1, G3 ok; G2 mean vs champion -> fp+fn; R003,R005 champion -> 2 fp
    f = r["fields"]
    assert (f["metric_name"]["tp"], f["metric_name"]["fp"], f["metric_name"]["fn"]) == (3, 2, 1)
    assert (f["aggregation"]["tp"], f["aggregation"]["fp"], f["aggregation"]["fn"]) == (2, 3, 1)
    assert f["normalized_value"]["tp"] == 3     # 19 == 19.0
    assert f["source_page"]["tp"] == 2 and "source_fragment" not in f and "note" not in f
    assert 0 < m["f1"] < 1 and m["key_entity"]["entity"] == "results" and m["key_entity"]["f1"] == r["f1"]
    assert m["coverage"] == 1.0 and m["quote_valid_share"] is None and m["regression"] is False
    runs = evaluation.list_runs("solar")
    assert len(runs) == 1 and runs[0]["f1"] == m["f1"] and runs[0]["note"] == "тест"
    assert evaluation.get_run(runs[0]["id"])["entities"]["results"]["fields"]["metric_name"]["tp"] == 3


def test_numeric_tolerance():
    assert evaluation.values_equal("1.0000004", "1", False)
    assert not evaluation.values_equal("20.1", "20", False)
    assert evaluation.values_equal("20.05", "20", True)         # 0.25 %
    assert not evaluation.values_equal("20.2", "20", True)
    assert evaluation.values_equal("  PCE ", "pce")


def test_regression_flag(tmp_path):
    ids = seed_solar()
    evaluation.import_gold("solar", _gold_dir(tmp_path))
    first = evaluation.run("solar", ["P1"])
    assert first["regression"] is False and first["prev_run_id"] is None
    same = evaluation.run("solar", ["P1"])
    assert same["regression"] is False and same["delta"] == 0 and same["prev_run_id"] == first["id"]
    # ухудшаем предсказания -> F1 падает больше чем на 0.01
    db.ex("UPDATE records SET data=json_set(data,'$.normalized_value','1') WHERE rid IN ('R001','R002','R004')")
    bad = evaluation.run("solar", ["P1"])
    assert bad["f1"] < first["f1"] - 0.01 and bad["regression"] is True and bad["prev_run_id"] == same["id"]
    # другой набор pub_ids — сравнения нет
    put_pub("P2")
    db.ex("INSERT INTO gold_records(pub_id,domain,entity,rid,data) VALUES('P2','solar','results','G1','{\"metric_name\":\"PCE\"}')")
    other = evaluation.run("solar", ["P1", "P2"])
    assert other["prev_run_id"] is None and other["regression"] is False


def _gold_dir(tmp_path):
    d = tmp_path / "gold"
    write_gold(d, GOLD)
    return d


def test_gold_from_publication_and_patch():
    ids = seed_solar()
    n = evaluation.gold_from_publication("P1")
    assert n == 11
    m = evaluation.run("solar", ["P1"])
    assert m["f1"] == 1.0 and m["key_entity"]["f1"] == 1.0   # предсказание == копии
    evaluation.patch_gold("P1", "results", "R001", {"metric_value": "99"}, "solar")
    assert evaluation.run("solar", ["P1"])["f1"] < 1.0


def test_quote_share_and_api(tmp_path):
    ids = seed_solar()
    db.ex("UPDATE records SET quote_verified=1 WHERE rid IN ('R001','R002')")
    db.ex("UPDATE records SET quote_verified=0, status='rejected' WHERE rid='R003'")
    d = _gold_dir(tmp_path)
    with TestClient(app) as c:
        pw = (db.DB_PATH.parent / "admin_password.txt").read_text().split()[1]
        t = c.post("/api/auth/login", json={"name": "admin", "password": pw}).json()["token"]
        h = {"Authorization": "Bearer " + t}
        assert c.post("/api/gold/import", json={"domain": "solar", "dir": str(d)}).status_code == 401
        assert c.post("/api/gold/import", json={"domain": "solar", "dir": str(tmp_path / "nope")}, headers=h).status_code == 400
        r = c.post("/api/gold/import", json={"domain": "solar", "dir": str(d)}, headers=h)
        assert r.status_code == 200 and r.json()["imported"]["results"] == 4
        assert len(c.get("/api/gold?domain=solar", headers=h).json()) == 4
        p = c.patch("/api/gold/P1/results/G4", json={"data": {"metric_name": "bad"}}, headers=h)
        assert p.status_code == 400
        p = c.patch("/api/gold/P1/results/G4", json={"data": {"metric_name": "HI"}}, headers=h)
        assert p.status_code == 200 and p.json()["data"]["metric_name"] == "HI"
        assert c.patch("/api/gold/P1/results/XX", json={"data": {}}, headers=h).status_code == 404
        run = c.post("/api/quality/run", json={"domain": "solar", "pub_ids": ["P1"], "note": "n"}, headers=h)
        assert run.status_code == 200, run.text
        j = run.json()
        assert j["quote_valid_share"] == round(2 / 3, 4) and "regression" in j and j["entities"]["results"]["fields"]
        assert c.get("/api/quality/runs?domain=solar", headers=h).json()[0]["id"] == j["id"]
        assert c.get("/api/quality/runs/%d" % j["id"], headers=h).json()["f1"] == j["f1"]
        assert c.get("/api/quality/runs/999", headers=h).status_code == 404
        assert c.post("/api/quality/run", json={"domain": "solar", "pub_ids": ["NONE"]}, headers=h).status_code == 400
        st = c.get("/api/stats?domain=solar").json()
        assert st["last_eval"]["id"] == j["id"] and st["quote_valid_share"] == round(2 / 3, 4)
        assert c.post("/api/gold/from_publication", json={"pub_id": "P1"}, headers=h).json()["records"] == 10


def test_two_domains_same_stem(tmp_path):
    """D001 есть в solar и в rag (во второй — pub_id 'D001-rag'): эталоны и экспорт не пересекаются."""
    import io, zipfile
    from app.api import common as C
    rag_res_cols = config.load_domain("rag")["schema"]["entities"]["results"]["columns"]
    for pid, dom in (("D001", "solar"), ("D001-rag", "rag")):
        db.ex("INSERT INTO publications(id,domain,filename,title,in_scope,status,created,updated) VALUES(?,?,?,?,1,'verified',?,?)", (pid, dom, "D001.pdf", "T", db.now(), db.now()))
    put_rec("D001", "results", "R001", {"result_id": "R001", "metric_name": "PCE", "normalized_value": "20", "result_origin": "primary"}, status="verified")
    put_rec("D001-rag", "results", "R001", {"result_id": "R001", "metric_name": "EM", "metric_variant": "", "metric_k": "", "normalized_value": "0.5", "result_origin": "primary"}, status="verified", domain="rag")
    gs, gr = tmp_path / "gs", tmp_path / "gr"
    gs.mkdir(); gr.mkdir()
    (gs / "results.csv").write_text("publication_id,result_id,metric_name,normalized_value,result_origin\nD001,G1,PCE,20,primary\nD001,G2,PCE,21,primary\n", encoding="utf-8")
    (gr / "results.csv").write_text("publication_id,result_id,metric_name,normalized_value,result_origin\nD001,G1,EM,0.5,primary\n", encoding="utf-8")
    r1 = evaluation.import_gold("solar", gs)
    r2 = evaluation.import_gold("rag", gr)
    assert r1["pub_ids"] == ["D001"] and r2["pub_ids"] == ["D001-rag"] and r2["pubs"] == ["D001"] and not r2["unresolved"]
    assert len(evaluation.list_gold("solar")) == 2 and len(evaluation.list_gold("rag")) == 1
    ms = evaluation.run("solar", ["D001"])
    mr = evaluation.run("rag", ["D001"])                 # stem
    mr2 = evaluation.run("rag", ["D001-rag"])            # внутренний id
    assert ms["entities"]["results"]["rows"] == {"gold": 2, "pred": 1, "matched": 1} and ms["pub_ids"] == ["D001"]
    assert mr["entities"]["results"]["rows"] == {"gold": 1, "pred": 1, "matched": 1} and mr["f1"] == 1.0 and mr["pub_ids"] == ["D001"]
    assert mr2["prev_run_id"] == mr["id"]
    from fastapi.testclient import TestClient
    with TestClient(app) as c:
        for dom in ("solar", "rag"):
            z = zipfile.ZipFile(io.BytesIO(c.get("/api/export?domain=%s&format=csv&status=all" % dom).content))
            rows = list(csv.DictReader(io.StringIO(z.read("results.csv").decode("utf-8").lstrip("\ufeff"))))
            assert [r["publication_id"] for r in rows] == ["D001"]
            pubs = list(csv.DictReader(io.StringIO(z.read("publications.csv").decode("utf-8").lstrip("\ufeff"))))
            assert [r["publication_id"] for r in pubs] == ["D001"]
            js = c.get("/api/export?domain=%s&format=json&status=all" % dom).json()
            assert js["tables"]["results"][0]["publication_id"] == "D001"


def test_gold_table_migration():
    c = db.conn()
    c.execute("DROP TABLE IF EXISTS gold_records")
    c.execute("CREATE TABLE gold_records(pub_id TEXT NOT NULL, domain TEXT NOT NULL, entity TEXT NOT NULL, rid TEXT NOT NULL, data TEXT NOT NULL, PRIMARY KEY(pub_id, entity, rid))")
    c.execute("INSERT INTO gold_records VALUES('A','solar','results','G1','{}')")
    c.commit()
    evaluation.ensure_tables()
    c.execute("INSERT INTO gold_records VALUES('A','rag','results','G1','{}')")   # теперь допустимо
    assert c.execute("SELECT COUNT(*) FROM gold_records").fetchone()[0] == 2


def test_match_best_overlap_mode():
    g = [{"a": "x", "b": "1", "c": "same"}, {"a": "y", "b": "2", "c": "other"}]
    p = [{"a": "q", "b": "2", "c": "other"}, {"a": "x", "b": "9", "c": "same"}]
    pairs, miss, extra = evaluation._match(g, p, ["*"], set(), ["a", "b", "c"])
    assert len(pairs) == 2 and not miss and not extra
    assert pairs[0][1]["a"] == "x" and pairs[1][1]["b"] == "2"
