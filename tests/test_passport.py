import json
from app import config, passport, db
from .helpers import env  # noqa: F401


def test_passport_matches_schema_and_rules():
    for d in config.list_domains():
        cfg = config.load_domain(d)
        p = passport.passport(d)
        md = passport.markdown(d)
        by_name = {e["name"]: e for e in p["entities"]}
        assert set(by_name) == set(cfg["schema"]["entities"])
        for ent, e in cfg["schema"]["entities"].items():
            assert [c["name"] for c in by_name[ent]["columns"]] == e["columns"]
            for c in e["columns"]:
                assert c in md
            for f, spec in (e.get("fields") or {}).items():
                col = [c for c in by_name[ent]["columns"] if c["name"] == f][0]
                assert col["type"] == spec["type"]
                assert col.get("vocab", []) == spec.get("vocab", [])
                for v in spec.get("vocab", []):
                    assert str(v) in md
        assert [r["id"] for r in p["rules"]] == [r["id"] for r in cfg["rules"]["rules"]]
        for r, src in zip(p["rules"], cfg["rules"]["rules"]):
            assert r["params"] == src.get("params", {}) and r["severity"] == src.get("severity")
            assert json.dumps(src.get("params", {}), ensure_ascii=False).replace("|", "\\|") in md
        assert [m["name"] for m in p["metrics"]] == list((cfg["schema"].get("metrics") or {}).keys())
        assert cfg["version"] in md and p["version"] == cfg["version"]


def test_passport_follows_active_config():
    import yaml
    body = yaml.safe_load(config._file_body("solar", "rules"))
    body["rules"][2]["params"]["max"] = 77
    ver = config.save_version("solar", "rules", yaml.safe_dump(body, allow_unicode=True), "t")
    p = passport.passport("solar")
    assert p["rules"][2]["params"]["max"] == 77 and "rules v%d" % ver in p["version"]
    assert '"max": 77' in passport.markdown("solar")


def test_write_all_creates_files(env):
    out = passport.write_all()
    assert len(out) == len(config.list_domains())
    assert (passport.DOCS_DIR / "passport_solar.md").read_text(encoding="utf-8").startswith("# Паспорт")
