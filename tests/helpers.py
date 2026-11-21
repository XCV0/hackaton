"""Общие данные тестов API: маленькая публикация области solar."""
import json, shutil, pathlib
import pytest
from app import db, config

ROOT = pathlib.Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def env(tmpdb, monkeypatch):
    """Временная БД (tmpdb из conftest) + копия конфигов/паспортов во временной папке."""
    from app import migrate, passport, evaluation
    d = tmpdb / "domains"
    shutil.copytree(ROOT / "config" / "domains", d)
    monkeypatch.setattr(config, "DOMAINS_DIR", d)
    monkeypatch.setattr(migrate, "PUBS_DIR", tmpdb / "no_pubs")
    monkeypatch.setattr(passport, "DOCS_DIR", tmpdb / "docs")
    evaluation.ensure_tables()
    yield tmpdb


def put_pub(pid="P1", domain="solar", status="verified", title="Test perovskite paper"):
    db.ex("INSERT OR REPLACE INTO publications(id,domain,filename,pdf_path,title,year,doi,authors,in_scope,status,created,updated) VALUES(?,?,?,?,?,?,?,?,1,?,?,?)",
          (pid, domain, pid + ".pdf", "", title, "2021", "10.1/" + pid, "A; B", status, db.now(), db.now()))


def put_rec(pid, entity, rid, data, status="extracted", **kw):
    data = {"publication_id": pid, **data}
    return db.ex("INSERT INTO records(pub_id,domain,entity,rid,data,status,source_page,source_fragment,quote_verified,version,created,updated) VALUES(?,?,?,?,?,?,?,?,?,1,?,?)",
                 (pid, kw.get("domain", "solar"), entity, rid, json.dumps(data, ensure_ascii=False), status, kw.get("page"), kw.get("fragment"), kw.get("qv"), db.now(), db.now()))


def seed_solar(pid="P1"):
    put_pub(pid)
    put_rec(pid, "materials", "M1", {"material_id": "M1", "material_class": "perovskite", "bandgap_value": "1.6"})
    put_rec(pid, "devices", "D1", {"device_id": "D1", "absorber_material_id": "M1", "device_type": "cell", "active_area": "0.1"})
    put_rec(pid, "devices", "D2", {"device_id": "D2", "absorber_material_id": "M1", "device_type": "cell", "active_area": "0.12"})
    put_rec(pid, "devices", "D3", {"device_id": "D3", "absorber_material_id": "M1", "device_type": "cell", "active_area": "1.0"})
    put_rec(pid, "measurements", "MS1", {"measurement_id": "MS1", "device_id": "D1", "measurement_type": "J-V", "spectrum": "AM1.5G", "light_intensity": "100", "intensity_unit": "mW/cm2", "scan_direction": "reverse", "certified": "false"})
    put_rec(pid, "measurements", "MS2", {"measurement_id": "MS2", "device_id": "D1", "measurement_type": "J-V", "spectrum": "", "light_intensity": "100", "intensity_unit": "mW/cm2", "scan_direction": "reverse", "certified": "false"})
    base = {"metric_unit": "%", "aggregation": "champion", "time_point": "0", "result_origin": "primary", "extraction_status": "extracted"}
    ids = {}
    ids["R001"] = put_rec(pid, "results", "R001", {**base, "result_id": "R001", "device_id": "D1", "measurement_id": "MS1", "material_id": "M1", "metric_name": "PCE", "metric_value": "20", "normalized_value": "20"}, page=3, fragment="PCE 20")
    ids["R002"] = put_rec(pid, "results", "R002", {**base, "result_id": "R002", "device_id": "D2", "measurement_id": "MS1", "metric_name": "PCE", "metric_value": "19", "normalized_value": "19"}, page=3, fragment="PCE 19")
    ids["R003"] = put_rec(pid, "results", "R003", {**base, "result_id": "R003", "device_id": "D3", "measurement_id": "MS1", "metric_name": "PCE", "metric_value": "18", "normalized_value": "18"})
    ids["R004"] = put_rec(pid, "results", "R004", {**base, "result_id": "R004", "device_id": "D1", "measurement_id": "MS1", "metric_name": "Voc", "metric_value": "1.1", "normalized_value": "1.1", "metric_unit": "V"})
    ids["R005"] = put_rec(pid, "results", "R005", {**base, "result_id": "R005", "device_id": "D1", "measurement_id": "MS2", "metric_name": "PCE", "metric_value": "17", "normalized_value": "17"})
    return ids
