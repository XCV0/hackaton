import io, csv, json, zipfile, types
import pytest
from fastapi.testclient import TestClient
from app import db, config
from app.api import common as C
from .helpers import seed_solar, put_pub, put_rec, env  # noqa: F401

STUB = types.SimpleNamespace(STAGES=["register", "parse", "segment", "extract", "verify", "normalize", "validate", "publish"], calls=[], fail=False,
                             queued=[])


def _revalidate(pid):
    STUB.calls.append(pid)
    if STUB.fail:
        raise RuntimeError("стадия упала")


STUB.revalidate = _revalidate
STUB.enqueue = lambda pid, from_stage=None: STUB.queued.append((pid, from_stage))
STUB.enqueue_many = lambda ids, from_stage=None: STUB.queued.extend((i, from_stage) for i in ids)
STUB.ocr_available = lambda: False
STUB.inventory_report = lambda domain=None: {"total": 0}


@pytest.fixture
def client(monkeypatch):
    from app import passport
    from app.server import app
    STUB.calls.clear(); STUB.queued.clear(); STUB.fail = False
    monkeypatch.setattr(C, "pipeline_mod", lambda: STUB)
    with TestClient(app) as c:
        yield c


def hdr(t):
    return {"Authorization": "Bearer " + t}


def login(client, name, pw):
    r = client.post("/api/auth/login", json={"name": name, "password": pw})
    assert r.status_code == 200, r.text
    return r.json()["token"]


@pytest.fixture
def tokens(client):
    from app import passport  # noqa
    pw = (db.DB_PATH.parent / "admin_password.txt").read_text().split()[1]
    adm = login(client, "admin", pw)
    for n, role in (("ann", "annotator"), ("res", "researcher")):
        assert client.post("/api/users", json={"name": n, "password": "secret1", "role": role}, headers=hdr(adm)).status_code == 200
    return {"admin": adm, "ann": login(client, "ann", "secret1"), "res": login(client, "res", "secret1")}


def test_admin_created_and_login(client):
    p = db.DB_PATH.parent / "admin_password.txt"
    assert p.exists() and (p.stat().st_mode & 0o777) == 0o600
    pw = p.read_text().split()[1]
    assert client.post("/api/auth/login", json={"name": "admin", "password": "wrong"}).status_code == 401
    t = login(client, "admin", pw)
    me = client.get("/api/auth/me", headers=hdr(t)).json()
    assert me["role"] == "admin" and me["name"] == "admin"
    u = db.q1("SELECT pw_hash, salt FROM users WHERE name='admin'")
    assert u["pw_hash"] != pw and len(u["salt"]) >= 16


def test_roles(client, tokens):
    ids = seed_solar()
    rid = ids["R001"]
    body = {"data": {"metric_value": "21"}}
    # аноним: чтение можно, запись нельзя
    assert client.get("/api/domains").status_code == 200
    assert client.get("/api/auth/me").json()["role"] == "researcher"
    assert client.patch("/api/records/%d" % rid, json=body).status_code == 401
    assert client.patch("/api/records/%d" % rid, json=body, headers=hdr(tokens["res"])).status_code == 403
    assert client.patch("/api/records/%d" % rid, json=body, headers=hdr(tokens["ann"])).status_code == 200
    # annotator не админ
    assert client.get("/api/users", headers=hdr(tokens["ann"])).status_code == 403
    assert client.get("/api/users", headers=hdr(tokens["admin"])).status_code == 200
    assert client.get("/api/audit", headers=hdr(tokens["ann"])).status_code == 403
    assert client.get("/api/gold?domain=solar", headers=hdr(tokens["res"])).status_code == 403
    assert client.get("/api/auth/me", headers={"Authorization": "Bearer nope"}).status_code == 401


def test_require_login(client, tokens):
    assert client.post("/api/settings", json={"require_login": "true"}, headers=hdr(tokens["admin"])).status_code == 200
    assert client.get("/api/domains").status_code == 401
    assert client.get("/api/domains", headers=hdr(tokens["res"])).status_code == 200
    client.post("/api/settings", json={"require_login": "false"}, headers=hdr(tokens["admin"]))
    assert client.get("/api/domains").status_code == 200


def test_users_guard(client, tokens):
    h = hdr(tokens["admin"])
    assert client.post("/api/users", json={"name": "x", "password": "123", "role": "admin"}, headers=h).status_code == 400
    assert client.post("/api/users", json={"name": "ann", "password": "secret1", "role": "admin"}, headers=h).status_code == 400
    assert client.delete("/api/users/admin", headers=h).status_code == 400
    assert client.patch("/api/users/admin", json={"role": "researcher"}, headers=h).status_code == 400
    assert client.patch("/api/users/res", json={"role": "annotator"}, headers=h).json()["role"] == "annotator"
    assert client.delete("/api/users/res", headers=h).status_code == 200
    assert "pw_hash" not in client.get("/api/users", headers=h).text


def test_patch_history_and_revalidate(client, tokens):
    ids = seed_solar()
    h = hdr(tokens["ann"])
    r = client.patch("/api/records/%d" % ids["R001"], json={"data": {"metric_value": "21,5", "metric_name": "FF"}, "comment": "опечатка"}, headers=h)
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["record"]["status"] == "manual" and j["record"]["data"]["metric_value"] == "21.5" and j["record"]["data"]["metric_name"] == "FF"
    assert j["warnings"] == [] and STUB.calls == ["P1"]
    hist = client.get("/api/records/%d/history" % ids["R001"]).json()
    assert len(hist) == 1 and hist[0]["user"] == "ann" and hist[0]["before"]["metric_value"] == "20" and hist[0]["comment"] == "опечатка"
    # валидация
    assert client.patch("/api/records/%d" % ids["R001"], json={"data": {"metric_name": "XYZ"}}, headers=h).status_code == 400
    assert client.patch("/api/records/%d" % ids["R001"], json={"data": {"metric_value": "abc"}}, headers=h).status_code == 400
    assert client.patch("/api/records/%d" % ids["R001"], json={"data": {"zzz": "1"}}, headers=h).status_code == 400
    assert client.patch("/api/records/%d" % ids["R001"], json={"data": {"source_page": "1.5"}}, headers=h).status_code == 400
    assert client.patch("/api/records/9999", json={"data": {"metric_value": "1"}}, headers=h).status_code == 404
    # падение стадий не отменяет правку
    STUB.fail = True
    r = client.patch("/api/records/%d" % ids["R001"], json={"data": {"metric_value": "22"}}, headers=h)
    assert r.status_code == 200 and r.json()["warnings"] and r.json()["record"]["data"]["metric_value"] == "22"
    assert any(a["action"] == "record.edit" for a in db.q("SELECT action FROM audit"))


def test_create_record(client, tokens):
    seed_solar()
    h = hdr(tokens["ann"])
    r = client.post("/api/records", json={"pub_id": "P1", "entity": "results", "data": {"metric_name": "FF", "metric_value": "75"}}, headers=h)
    assert r.status_code == 200, r.text
    rec = r.json()["record"]
    assert rec["rid"] == "R006" and rec["status"] == "manual" and rec["data"]["result_id"] == "R006"
    assert client.post("/api/records", json={"pub_id": "P1", "entity": "nope", "data": {}}, headers=h).status_code == 400
    assert client.post("/api/records", json={"pub_id": "ZZ", "entity": "results", "data": {}}, headers=h).status_code == 404


def test_reject_restore_and_export(client, tokens):
    ids = seed_solar()
    h = hdr(tokens["ann"])
    rid = ids["R002"]
    assert client.post("/api/records/%d/action" % ids["R001"], json={"action": "confirm"}, headers=h).json()["record"]["status"] == "verified"
    assert client.post("/api/records/%d/action" % rid, json={"action": "reject", "comment": "ошибка"}, headers=h).json()["record"]["status"] == "rejected"
    # отклонённые не в списках по умолчанию, не в экспорте
    items = client.get("/api/records?domain=solar").json()
    assert rid not in [x["id"] for x in items["items"]]
    assert rid in [x["id"] for x in client.get("/api/records?domain=solar&status=rejected").json()["items"]]
    pub = client.get("/api/publications/P1").json()
    assert all(x["rid"] != "R002" for x in pub["records"]["results"])
    assert any(x["rid"] == "R002" for x in client.get("/api/publications/P1?include_rejected=1").json()["records"]["results"])
    z = zipfile.ZipFile(io.BytesIO(client.get("/api/export?domain=solar&format=csv&status=all").content))
    schema = config.load_domain("solar")["schema"]
    assert sorted(z.namelist()) == sorted(e + ".csv" for e in schema["entities"])
    raw = z.read("results.csv").decode("utf-8")
    assert raw.startswith("﻿")
    rows = list(csv.DictReader(io.StringIO(raw.lstrip("﻿"))))
    assert list(rows[0].keys()) == schema["entities"]["results"]["columns"]
    assert "R002" not in [r["result_id"] for r in rows] and len(rows) == 4
    zv = zipfile.ZipFile(io.BytesIO(client.get("/api/export?domain=solar&format=csv&status=verified").content))
    rv = list(csv.DictReader(io.StringIO(zv.read("results.csv").decode("utf-8").lstrip("﻿"))))
    assert [r["result_id"] for r in rv] == ["R001"]
    js = client.get("/api/export?domain=solar&format=json&status=all").json()
    res = js["tables"]["results"]
    assert "R002" not in [x["rid"] for x in res]
    one = [x for x in res if x["rid"] == "R001"][0]
    assert one["status"] == "verified" and one["source"]["page"] == 3 and "quote_verified" in one and one["data"]["metric_name"] == "PCE"
    # восстановление
    assert client.post("/api/records/%d/action" % rid, json={"action": "restore"}, headers=h).json()["record"]["status"] == "extracted"
    assert client.post("/api/records/%d/action" % rid, json={"action": "restore"}, headers=h).status_code == 400
    assert client.post("/api/records/%d/action" % rid, json={"action": "boom"}, headers=h).status_code == 400
    # старый формат
    d = client.get("/api/data?domain=solar").json()
    assert "results" in d["tables"] and d["passport"]["domain"] == "solar"


def test_search_and_filters(client):
    ids = seed_solar()
    from app import fts
    fts.reindex()
    r = client.get("/api/search?q=perovskite&domain=solar").json()["items"]
    assert any(x["kind"] == "record" and x["entity"] == "materials" for x in r)
    assert any(x["kind"] == "publication" for x in r)
    assert all("snippet" in x for x in r)
    assert client.get("/api/search?q=").json()["items"] == []
    assert client.get("/api/search?q=%22%5E(").status_code == 200
    j = client.get("/api/records?domain=solar&metric=Voc").json()
    assert j["total"] == 1 and j["items"][0]["rid"] == "R004"
    j = client.get("/api/records?domain=solar&q=perovskite&entity=materials").json()
    assert j["total"] == 1
    ls = client.get("/api/publications?domain=solar").json()
    assert ls[0]["n_records"] == 11 and ls[0]["in_scope"] is True
    st = client.get("/api/stats?domain=solar").json()
    assert st["pubs"] == 1 and st["by_status"]["verified"] == 1 and st["records"] == 11


def test_issues_resolve(client, tokens):
    ids = seed_solar()
    iid = db.ex("INSERT INTO issues(pub_id,domain,rule_id,severity,record_ids,description,origin,status,created) VALUES('P1','solar','range_pce','warning',?,?,'rule','open',?)",
                (json.dumps(["R001"]), "PCE вне диапазона", db.now()))
    rec = client.get("/api/records/%d" % ids["R001"]).json()
    assert rec["record"]["issue_ids"] == [iid] and rec["issues"][0]["rule_id"] == "range_pce"
    assert "materials" in rec["context"] and rec["context"]["devices"]["D1"]["device_type"] == "cell"
    assert client.get("/api/issues?domain=solar&status=open").json()[0]["record_ids"] == ["R001"]
    h = hdr(tokens["ann"])
    assert client.post("/api/issues/%d/resolve" % iid, json={"status": "bad"}, headers=h).status_code == 400
    r = client.post("/api/issues/%d/resolve" % iid, json={"status": "accepted", "comment": "ок"}, headers=h)
    assert r.status_code == 200 and r.json()["status"] == "accepted"
    assert client.get("/api/stats?domain=solar").json()["issues_by_rule"]["range_pce"] == {"open": 0, "total": 1}


def test_configs(client, tokens):
    h = hdr(tokens["admin"])
    cur = client.get("/api/config/solar", headers=h).json()
    assert "entities" in cur["schema_yaml"] and cur["versions"]
    assert client.get("/api/config/solar").status_code == 401
    r = client.put("/api/config/solar/rules", json={"yaml": cur["rules_yaml"], "comment": "тест"}, headers=h)
    assert r.status_code == 200 and r.json()["version"] == 2
    assert client.put("/api/config/solar/rules", json={"yaml": "not: a rules file", "comment": ""}, headers=h).status_code == 400
    assert client.put("/api/config/solar/rules", json={"yaml": "a: [", "comment": ""}, headers=h).status_code == 400
    assert client.put("/api/config/solar/schema", json={"yaml": "entities: {}", "comment": ""}, headers=h).status_code == 400
    assert client.post("/api/config/solar/rules/activate", json={"version": 1}, headers=h).status_code == 200
    vs = [v for v in client.get("/api/config/solar", headers=h).json()["versions"] if v["kind"] == "rules"]
    assert {v["version"]: v["active"] for v in vs} == {1: True, 2: False}
    assert client.post("/api/config/solar/rules/activate", json={"version": 9}, headers=h).status_code == 400
    assert client.get("/api/domains").json()[0]["version"].startswith("schema v")
    # новая область без правки кода
    s = client.get("/api/config/rag", headers=h).json()
    n = client.post("/api/config/domains", json={"domain": "rag2", "schema_yaml": s["schema_yaml"], "rules_yaml": s["rules_yaml"]}, headers=h)
    assert n.status_code == 200, n.text
    assert "rag2" in [d["domain"] for d in client.get("/api/domains").json()]
    assert client.get("/api/passport/rag2").json()["domain"] == "rag2"
    assert client.post("/api/config/domains", json={"domain": "rag2", "schema_yaml": s["schema_yaml"], "rules_yaml": s["rules_yaml"]}, headers=h).status_code == 400
    assert client.post("/api/config/domains", json={"domain": "Bad Name", "schema_yaml": "a: 1", "rules_yaml": "b: 2"}, headers=h).status_code == 400


def test_passport_endpoints(client):
    j = client.get("/api/passport/solar").json()
    assert j["domain"] == "solar" and j["entities"]
    md = client.get("/api/passport/solar.md")
    assert md.status_code == 200 and "Паспорт набора данных" in md.text
    assert client.get("/api/passport/nope").status_code == 404


def test_pipeline_admin_endpoints(client, tokens):
    seed_solar()
    h = hdr(tokens["admin"])
    assert client.post("/api/jobs/run", json={"pub_ids": ["P1"], "from_stage": "verify"}, headers=h).status_code == 200
    assert STUB.queued == [("P1", "verify")]
    assert client.post("/api/jobs/run", json={"pub_ids": ["P1"], "from_stage": "zzz"}, headers=h).status_code == 400
    assert client.post("/api/jobs/run", json={"pub_ids": ["NOPE"]}, headers=h).status_code == 404
    assert client.post("/api/jobs/P1/retry", headers=h).status_code == 200
    assert client.get("/api/jobs?domain=solar", headers=h).json()[0]["id"] == "P1"
    assert client.get("/api/ocr/status", headers=h).json()["available"] is False
    assert client.get("/api/settings", headers=h).status_code == 200
    up = client.post("/api/upload", data={"domain": "solar"}, files=[("files", ("a.pdf", b"not pdf", "application/pdf"))], headers=h)
    assert up.status_code == 200 and "error" in up.json()[0]


def test_migrate(tmp_path):
    from app import migrate
    d = tmp_path / "pubs" / "X1"
    d.mkdir(parents=True)
    (d / "meta.json").write_text(json.dumps({"filename": "X1.pdf", "pdf_path": "/tmp/X1.pdf"}))
    (d / "tables.json").write_text(json.dumps({
        "publications": [{"publication_id": "X1", "title": "T", "year": "2020", "DOI": "10.2/x", "authors": "Z", "in_scope": "true", "exclusion_reason": ""}],
        "materials": [{"publication_id": "X1", "material_id": "M1", "material_class": "CIGS"}],
        "results": [{"publication_id": "X1", "result_id": "R001", "metric_name": "PCE", "metric_value": "10", "source_page": "2", "source_fragment": "q"}],
        "issues": [{"publication_id": "X1", "issue_id": "I1", "record_ids": "R001;R002", "check_name": "x", "description": "d", "source_page": "2", "source_fragment": ""}]}))
    res = migrate.migrate(tmp_path / "pubs")
    assert res == {"pubs": 1, "records": 2, "issues": 1}
    r = db.q1("SELECT * FROM records WHERE rid='R001'")
    assert r["status"] == "extracted" and r["source_page"] == 2 and r["domain"] == "solar"
    assert db.q1("SELECT pdf_path FROM publications WHERE id='X1'")["pdf_path"] == "/tmp/X1.pdf"
    assert json.loads(db.q1("SELECT record_ids FROM issues")["record_ids"]) == ["R001", "R002"]
    assert db.q1("SELECT COUNT(*) n FROM records_fts")["n"] == 3
    assert migrate.migrate(tmp_path / "pubs")["pubs"] == 0
