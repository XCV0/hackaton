import json, pathlib, shutil, sys, threading, types
import httpx, pytest

from app import db, config, llm, pipeline, ocr
from tests.conftest import make_pdf, page_text

P1 = page_text("Perovskite solar cells with champion PCE of 20.1 % and Voc of 1.10 V were fabricated.", "DOI: 10.1234/abc.2024.001")
P2 = page_text("The control device showed PCE of 18.0 % and the champion PCE of 20.1 % was confirmed.")
P3 = page_text("References", "[1] A. Author, J. Mater. 2020, PCE of 99.9 %")


@pytest.fixture
def pdf(tmp_path):
    return make_pdf(tmp_path / "paper.pdf", [P1, P2, P3])


def solar_stub(calls=None, fail_results=False, in_scope=True):
    def call(system, user, pub_id=None, stage=None):
        if calls is not None:
            calls.append((stage, user))
        if stage == "extract:structure":
            if not in_scope:
                return {"in_scope": False, "exclusion_reason": "tandem cells only", "title": "T"}
            return {"in_scope": True, "title": "Perovskite paper", "year": "2024", "DOI": "10.1234/abc.2024.001", "authors": ["Ivanov I."],
                    "materials": [{"id": "M1", "material_name_raw": "MAPbI3", "material_class": "perovskite"}],
                    "devices": [{"id": "DV1", "absorber_material_id": "M1", "device_label": "control", "device_role": "control", "device_type": "cell"}],
                    "layers": [{"id": "L1", "device_id": "DV1", "layer_order": 1, "layer_function": "absorber", "layer_material": "MAPbI3"}],
                    "measurements": [{"id": "MS1", "device_id": "DV1", "measurement_type": "J-V", "scan_direction": "reverse"}]}
        if fail_results:
            raise llm.LLMError("Недостаточно средств на аккаунте провайдера (HTTP 402). Пополните баланс.")
        return {"results": [
            {"device_id": "DV1", "measurement_id": "MS1", "metric_name": "PCE", "raw_value": "20.1", "raw_unit": "%", "aggregation": "champion",
             "origin": "primary", "page": 2, "table": "", "fragment": "the champion PCE of 20.1 % was confirmed"},
            {"device_id": "DV1", "measurement_id": "MS1", "metric_name": "PCE", "raw_value": "20.1", "raw_unit": "%", "aggregation": "champion",
             "origin": "primary", "page": 1, "table": "", "fragment": "champion PCE of 20.1 %"},
            {"device_id": "DV1", "measurement_id": "MS1", "metric_name": "Voc", "raw_value": "1.10", "raw_unit": "V", "origin": "primary", "page": 1,
             "fragment": "Voc of 1.10 V"},
            {"device_id": "DV1", "metric_name": "FF", "raw_value": "85", "raw_unit": "%", "origin": "primary", "page": 2,
             "fragment": "a fill factor of 85 % was obtained"},                       # выдумана
            {"device_id": "DV9", "metric_name": "PCE", "raw_value": "18.0", "raw_unit": "%", "origin": "primary", "page": 3,
             "fragment": "control device showed PCE of 18.0 %"},                      # страница указана неверно, ссылка на несуществующий прибор
        ], "issues": [{"check": "contradiction", "description": "test", "page": 1, "fragment": "x"}]}
    return call


def recs(pub, entity=None, status=None):
    sql, a = "SELECT * FROM records WHERE pub_id=?", [pub]
    if entity:
        sql += " AND entity=?"; a.append(entity)
    if status:
        sql += " AND status=?"; a.append(status)
    out = db.q(sql, a)
    for r in out:
        r["d"] = json.loads(r["data"])
    return out


def stages(pub):
    return {r["stage"]: r for r in db.q("SELECT * FROM stage_runs WHERE pub_id=?", (pub,))}


@pytest.fixture(params=["groq", "ollama"])
def mock_provider(tmpdb, monkeypatch, request):
    provider = request.param
    monkeypatch.setenv("GROQ_API_KEY", "test-groq-key")
    llm.save_settings({"provider": provider})

    def install(stub):
        def post(url, *, headers, json, **options):
            assert url == llm.PRESETS[provider]["base_url"] + "/chat/completions"
            assert json["model"] == llm.PRESETS[provider]["model"]
            assert json["response_format"] == {"type": "json_object"}
            if provider == "groq":
                assert headers["Authorization"] == "Bearer test-groq-key"
                assert json["max_completion_tokens"] == llm.MAX_TOKENS
            else:
                assert "Authorization" not in headers and options["trust_env"] is False
            system, user = (m["content"] for m in json["messages"])
            stage = "extract:results" if user.startswith("Структура статьи уже извлечена") else "extract:structure"
            return ok_resp(db.jd(stub(system, user, stage=stage)))

        monkeypatch.setattr(llm.httpx, "post", post)

    return install


# ------------------------------------------------------------------ полный прогон
def test_full_run_solar(pdf, mock_provider):
    calls = []
    mock_provider(solar_stub(calls))
    pid = pipeline.register_pdf("solar", path=pdf, split="dev")
    st = pipeline.run_publication(pid)
    assert st in ("flagged", "verified")
    s = stages(pid)
    assert [s[x]["status"] for x in pipeline.STAGES] == ["done"] * 8
    pub = db.q1("SELECT * FROM publications WHERE id=?", (pid,))
    assert pub["pages"] == 3 and pub["doi"] == "10.1234/abc.2024.001" and pub["title"] == "Perovskite paper" and pub["in_scope"] == 1
    res = recs(pid, "results")
    assert len(res) == 5
    by = {r["d"].get("raw_value") + "|" + r["d"].get("metric_name") + "|" + str(r["d"].get("source_page")): r for r in res}
    # дубликат: основной с меньшей страницей (стр. 1), повтор со страницы 2 помечен
    dups = [r for r in res if r["d"].get("duplicate_of")]
    assert len(dups) == 1 and dups[0]["d"]["duplicate_of"] == by["20.1|PCE|1"]["rid"]
    # выдуманная цитата отклонена
    ff = [r for r in res if r["d"]["metric_name"] == "FF"][0]
    assert ff["status"] == "rejected" and ff["quote_verified"] == 0
    iss = db.q("SELECT * FROM issues WHERE pub_id=? AND origin='verify'", (pid,))
    assert any(i["rule_id"] == "citation_not_found" and i["severity"] == "warning" and json.loads(i["record_ids"]) == [ff["rid"]] for i in iss)
    # верная цитата на другой странице: страница исправлена, значение подтверждено
    pce18 = [r for r in res if r["d"]["raw_value"] == "18.0"][0]
    assert pce18["quote_verified"] == 1 and pce18["source_page"] == 2 and pce18["d"]["source_page"] == "2"
    assert pce18["d"]["device_id"] == ""            # несуществующая ссылка DV9 -> пусто, запись осталась
    ok = [r for r in res if r["d"]["metric_name"] == "Voc"][0]
    assert ok["quote_verified"] == 1 and ok["source_page"] == 1 and ok["source_fragment"] == "Voc of 1.10 V"
    # ссылки структуры приведены к rid
    dev = recs(pid, "devices")[0]
    assert dev["d"]["absorber_material_id"] == recs(pid, "materials")[0]["rid"]
    assert recs(pid, "publications")[0]["d"]["in_scope"] == "true"
    # нормализация (rules) отработала
    assert ok["d"].get("normalized_value") in ("1.1", "1.10", "1.1000", "1.1", 1.1) or ok["d"].get("normalized_value")
    # список литературы вырезан из промптов
    assert all("99.9" not in u for _, u in calls)
    # журнал вызовов
    n = db.q1("SELECT COUNT(*) n FROM llm_calls WHERE pub_id=?", (pid,))["n"]
    assert n == len(calls) > 0
    assert db.q("SELECT 1 FROM issues WHERE pub_id=? AND origin='model'", (pid,))


def test_prompt_contains_service_columns_only_if_in_schema(tmpdb, pdf, monkeypatch):
    calls = []
    monkeypatch.setattr(llm, "call_json", solar_stub(calls))
    pid = pipeline.register_pdf("solar", path=pdf)
    pipeline.run_publication(pid, stop_after="extract")
    res_prompt = [u for st, u in calls if st == "extract:results"][0]
    for key in ('"page"', '"fragment"', '"origin"', '"cited_ref"', '"aggregation"', '"metric_name"'):
        assert key in res_prompt
    assert "normalized_value" not in res_prompt and "duplicate_of" not in res_prompt     # считает код
    assert "perovskite" in [u for st, u in calls if st == "extract:structure"][0]
    assert "M1:" in res_prompt or "M1" in res_prompt


def test_resume_after_error_continues_and_no_duplicates(tmpdb, pdf, monkeypatch):
    monkeypatch.setattr(llm, "call_json", solar_stub(fail_results=True))
    pid = pipeline.register_pdf("solar", path=pdf)
    assert pipeline.run_publication(pid) == "error"
    s = stages(pid)
    assert s["extract"]["status"] == "error" and "402" in s["extract"]["message"]
    assert s["parse"]["status"] == "done" and "verify" not in s
    assert db.q1("SELECT status, message FROM publications WHERE id=?", (pid,))["status"] == "error"
    assert recs(pid, "results") == []              # частичных записей нет
    parse_attempt = s["parse"]["attempt"]
    monkeypatch.setattr(llm, "call_json", solar_stub())
    assert pipeline.run_publication(pid) in ("flagged", "verified")
    s = stages(pid)
    assert s["parse"]["attempt"] == parse_attempt and s["extract"]["attempt"] == 2
    n = len(recs(pid, "results"))
    pipeline.run_publication(pid, from_stage="extract")
    assert len(recs(pid, "results")) == n == 5     # идемпотентно
    pipeline.run_publication(pid, from_stage="verify")
    assert len(recs(pid, "results")) == 5
    assert len(db.q("SELECT 1 FROM issues WHERE pub_id=? AND origin='verify'", (pid,))) == 1


def test_manual_records_survive_reextract(tmpdb, pdf, monkeypatch):
    monkeypatch.setattr(llm, "call_json", solar_stub())
    pid = pipeline.register_pdf("solar", path=pdf)
    pipeline.run_publication(pid)
    d = {"publication_id": pid, "result_id": "R001", "metric_name": "PCE", "raw_value": "19", "raw_unit": "%"}
    db.ex("DELETE FROM records WHERE pub_id=? AND entity='results' AND rid='R001'", (pid,))
    db.ex("INSERT INTO records(pub_id,domain,entity,rid,data,status) VALUES(?,?,?,?,?,?)", (pid, "solar", "results", "R001", db.jd(d), "manual"))
    pipeline.run_publication(pid, from_stage="extract")
    r1 = [r for r in recs(pid, "results") if r["rid"] == "R001"]
    assert len(r1) == 1 and r1[0]["status"] == "manual" and r1[0]["d"]["raw_value"] == "19"
    assert len(recs(pid, "results")) == 6


def test_out_of_scope_excluded(tmpdb, pdf, monkeypatch):
    monkeypatch.setattr(llm, "call_json", solar_stub(in_scope=False))
    pid = pipeline.register_pdf("solar", path=pdf)
    assert pipeline.run_publication(pid) == "excluded"
    pub = db.q1("SELECT * FROM publications WHERE id=?", (pid,))
    assert pub["status"] == "excluded" and pub["in_scope"] == 0 and "tandem" in pub["exclusion_reason"]
    rows = recs(pid)
    assert [r["entity"] for r in rows] == ["publications"]
    assert all(r["status"] == "done" for r in stages(pid).values())


def test_duplicate_file_marked(tmpdb, pdf, tmp_path, monkeypatch):
    monkeypatch.setattr(llm, "call_json", solar_stub())
    shutil.copy(pdf, tmp_path / "copy.pdf")
    a = pipeline.register_pdf("solar", path=pdf)
    b = pipeline.register_pdf("solar", path=str(tmp_path / "copy.pdf"))
    assert pipeline.run_publication(a) in ("flagged", "verified")
    assert pipeline.run_publication(b) == "error"
    p = db.q1("SELECT * FROM publications WHERE id=?", (b,))
    assert p["message"].startswith("register") and "дубликат %s" % a in p["message"]
    assert stages(b)["register"]["message"] == "дубликат %s" % a


def test_stage_unknown_and_missing_pub(tmpdb):
    with pytest.raises(KeyError):
        pipeline.run_publication("nope")
    with pytest.raises(ValueError):
        pipeline.run_publication("nope", from_stage="bad")


# ------------------------------------------------------------------ та же стадия, другая область (только конфиг)
def test_rag_domain_same_stages(tmp_path, mock_provider):
    pdf = make_pdf(tmp_path / "rag.pdf", [page_text("Our RAG system with top_k = 5 reaches nDCG@10 of 0.61 on NQ."), page_text("More details on the setup."), page_text("Appendix text.")])

    def stub(system, user, pub_id=None, stage=None):
        if stage == "extract:structure":
            assert "rag_variant" in user and "retrieval_method" in user and "material_class" not in user
            return {"in_scope": True, "title": "RAG paper", "year": "2025",
                    "configurations": [{"id": "C1", "rag_variant": "naive", "top_k": 5, "chunk_size": "512"}],
                    "datasets": [{"id": "DS1", "dataset_name": "NQ"}], "tasks": [{"id": "T1", "task_type": "QA"}],
                    "experiments": [{"id": "E1", "evaluation_protocol": "EM", "sample_size": "100"}]}
        assert "metric_k" in user and "metric_name" in user and "Voc" not in user
        return {"results": [{"config_id": "C1", "dataset_id": "DS1", "task_id": "T1", "experiment_id": "E1", "metric_name": "nDCG", "metric_k": 10,
                             "raw_value": "0.61", "raw_unit": "fraction", "origin": "primary", "page": 1, "fragment": "nDCG@10 of 0.61 on NQ"}]}
    mock_provider(stub)
    pid = pipeline.register_pdf("rag", path=pdf)
    assert pipeline.run_publication(pid) in ("flagged", "verified")
    r = recs(pid, "results")[0]
    assert r["quote_verified"] == 1 and r["d"]["config_id"] == "C1" and r["d"]["dataset_id"] == "DS1" and r["d"]["metric_k"] == "10"
    assert recs(pid, "configurations")[0]["d"]["top_k"] == "5"
    assert [x["stage"] for x in db.q("SELECT stage FROM stage_runs WHERE pub_id=? AND status='done'", (pid,))].__len__() == 8


def test_prompts_domain_independent():
    s, r = config.load_domain("solar"), config.load_domain("rag")
    ps, pr = pipeline.build_structure_prompt(s, "T"), pipeline.build_structure_prompt(r, "T")
    assert "materials" in ps and "devices" in ps and "perovskite" in ps and "configurations" not in ps
    assert "configurations" in pr and "higher_is_better" not in pr and "perovskite" not in pr and "devices" not in pr
    assert "A-B-X" in ps                                 # description поля из схемы
    rp = pipeline.build_results_prompt(r, "", "T", 1, 2)
    assert "higher_is_better" in rp and '"results"' in rp and "ABLATION" not in rp
    sysm = pipeline.build_system(r)
    assert "RAG" in sysm and "ablation" in sysm.lower()


def test_mark_duplicates_rules():
    cfg = config.load_domain("solar")
    mk = lambda i, **kw: {"result_id": "R%03d" % i, "device_id": "DV1", "metric_name": "PCE", "raw_value": "20.1", "result_origin": "primary", "duplicate_of": "", **kw}
    rows = [mk(1, source_page="5"), mk(2, source_page="2"), mk(3, source_page="9", source_table="Table 1"), mk(4, device_id="DV2", source_page="1"),
            mk(5, raw_value="20,1", source_page="7"), mk(6, result_origin="cited", source_page="1"), mk(7, raw_value="", source_page="1"), mk(8, raw_value="", source_page="1")]
    pipeline.mark_duplicates(cfg, rows)
    d = [r["duplicate_of"] for r in rows]
    assert d == ["R003", "R003", "", "", "R003", "", "", ""]


# ------------------------------------------------------------------ OCR
def test_needs_ocr_stops_pipeline(tmpdb, tmp_path, monkeypatch):
    pdf = make_pdf(tmp_path / "scan.pdf", ["", "", "tiny"])
    monkeypatch.setattr(llm, "call_json", lambda *a, **k: pytest.fail("LLM не должен вызываться"))
    pid = pipeline.register_pdf("solar", path=pdf)
    assert pipeline.run_publication(pid) == "needs_ocr"
    p = db.q1("SELECT * FROM publications WHERE id=?", (pid,))
    assert p["status"] == "needs_ocr" and p["ocr_status"] == "queued" and p["needs_ocr"] == 1
    assert pipeline.run_publication(pid) == "needs_ocr"       # без явного from_stage остаётся в очереди OCR


def test_run_ocr_without_tesseract(tmpdb, tmp_path, monkeypatch):
    pdf = make_pdf(tmp_path / "scan.pdf", ["", "", ""])
    pid = pipeline.register_pdf("solar", path=pdf)
    monkeypatch.setattr(ocr.shutil, "which", lambda n: None)
    monkeypatch.setattr(ocr, "engine", lambda: None)
    assert pipeline.ocr_available() is False
    r = pipeline.run_ocr(pid)
    assert r["ok"] is False and "brew install tesseract tesseract-lang" in r["error"]
    assert pipeline.run_ocr("nope")["ok"] is False


def test_run_ocr_success_requeues_and_parse_keeps_ocr_text(tmpdb, tmp_path, monkeypatch):
    pdf = make_pdf(tmp_path / "scan.pdf", ["", "", ""])
    monkeypatch.setattr(llm, "call_json", solar_stub())
    pid = pipeline.register_pdf("solar", path=pdf)
    assert pipeline.run_publication(pid) == "needs_ocr"
    monkeypatch.setattr(ocr.shutil, "which", lambda n: "/usr/bin/tesseract")
    monkeypatch.setattr(ocr, "tesseract_langs", lambda: ["eng", "rus"])
    monkeypatch.setattr(ocr, "_renderer", lambda: "stub")
    monkeypatch.setattr(ocr, "_render_page", lambda path, idx, dpi=250: idx)
    texts = {0: P1, 1: P2, 2: P3}
    monkeypatch.setattr(ocr, "_ocr_image", lambda img, lang: texts[img])
    assert pipeline.ocr_available() is True
    r = pipeline.run_ocr(pid)
    assert r["ok"] and r["pages_ocr"] == 3 and r["lang"] == "eng+rus"
    assert pipeline.wait_idle(30)
    p = db.q1("SELECT * FROM publications WHERE id=?", (pid,))
    assert p["ocr_status"] == "done" and p["status"] in ("flagged", "verified")
    assert db.q1("SELECT ocr FROM pages WHERE pub_id=? AND page=1", (pid,))["ocr"] == 1
    assert recs(pid, "results")


# ------------------------------------------------------------------ инвентаризация / очередь
def test_scan_source_and_inventory(tmpdb, tmp_path):
    d = tmp_path / "corpus" / "sub"
    d.mkdir(parents=True)
    a = make_pdf(tmp_path / "corpus" / "a.pdf", [P1, P2])
    shutil.copy(a, d / "a_copy.pdf")
    make_pdf(d / "b.pdf", [page_text("Other paper DOI 10.5555/zzz.1")])
    (d / "broken.pdf").write_bytes(b"not a pdf")
    (d / "note.txt").write_text("x")
    rep = pipeline.scan_source("solar", str(tmp_path / "corpus"), split="dev")
    assert len(rep["added"]) == 2 and len(rep["duplicates"]) == 1 and len(rep["unreadable"]) == 1
    assert rep["duplicates"][0]["duplicate_of"] in rep["added"]
    rep2 = pipeline.scan_source("solar", str(tmp_path / "corpus"))
    assert rep2["added"] == [] and len(rep2["skipped"]) == 2
    inv = pipeline.inventory_report("solar")
    s = inv["domains"]["solar"]
    assert s["files"] == 2 and s["pages"] == 3 and s["with_doi"] == 2 and s["needs_ocr"] == 0 and s["layer_share"] == 1.0
    assert len(s["folders"]) == 2 and "solar" in inv["summary"] and s["splits"]["dev"]["files"] == 2
    assert inventory_dups(inv) == 0
    # дубликат попадает в отчёт после регистрации (одинаковый хеш/DOI)
    pipeline.register_pdf("solar", path=str(d / "a_copy.pdf"))
    pipeline.register_pdf("solar", path=str(d / "a_copy.pdf"), pub_id="X2")
    inv = pipeline.inventory_report()
    assert inv["duplicates"]["hash"] and inv["duplicates"]["hash"][0]["domain"] == "solar"


def inventory_dups(inv):
    return len(inv["duplicates"]["hash"]) + len(inv["duplicates"]["doi"])


def test_enqueue_runs_in_pool(tmpdb, pdf, monkeypatch):
    monkeypatch.setattr(llm, "call_json", solar_stub())
    llm.save_settings({"workers": 2})
    pid = pipeline.register_pdf("solar", path=pdf)
    assert pipeline.enqueue(pid) is True
    assert pipeline.wait_idle(60)
    assert db.q1("SELECT status FROM publications WHERE id=?", (pid,))["status"] in ("flagged", "verified")
    assert pipeline.enqueue_many([pid], from_stage="verify") == [pid]
    assert pipeline.wait_idle(60)


def test_recover_interrupted(tmpdb, pdf):
    pid = pipeline.register_pdf("solar", path=pdf)
    pipeline._set_stage(pid, "parse", "running")
    db.ex("UPDATE publications SET status='processing' WHERE id=?", (pid,))
    pipeline.recover_interrupted()
    assert stages(pid)["parse"]["status"] == "error" and "перезапуском" in stages(pid)["parse"]["message"]
    p = db.q1("SELECT status, message FROM publications WHERE id=?", (pid,))
    assert p["status"] == "error" and "перезапуском" in p["message"]


def test_revalidate_after_manual_edit(tmpdb, pdf, monkeypatch):
    monkeypatch.setattr(llm, "call_json", solar_stub())
    pid = pipeline.register_pdf("solar", path=pdf)
    pipeline.run_publication(pid)
    r = [x for x in recs(pid, "results") if x["d"]["metric_name"] == "Voc"][0]
    d = {**r["d"], "raw_value": "1100", "raw_unit": "mV"}
    db.ex("UPDATE records SET data=?, status='manual' WHERE id=?", (db.jd(d), r["id"]))
    assert pipeline.revalidate(pid) in ("flagged", "verified")
    r2 = db.q1("SELECT data FROM records WHERE id=?", (r["id"],))
    assert float(json.loads(r2["data"])["normalized_value"]) == pytest.approx(1.1)
    assert json.loads(r2["data"])["normalized_unit"] == "V"


# ------------------------------------------------------------------ LLM-клиент
class FakeResp:
    def __init__(self, code, body=None, text=""):
        self.status_code, self._b, self.text = code, body, text or json.dumps(body or {})

    def json(self):
        return self._b


def ok_resp(content):
    return FakeResp(200, {"choices": [{"message": {"content": content}}]})


def test_settings_public_hides_key(tmpdb, monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    assert llm.get_settings(public=True)["key_set"] is False
    pub = llm.save_settings({"api_key": "sk-secret", "workers": 4})
    assert pub["key_set"] is True and "api_key" not in pub and pub["workers"] == 4
    assert pub["provider"] == "deepseek" and pub["base_url"] == "https://api.deepseek.com" and pub["model"] == "deepseek-flash"
    assert llm.get_settings()["api_key"] == "sk-secret"
    assert llm.save_settings({"api_key": ""})["key_set"] is True              # пустой = не менять
    pub = llm.save_settings({"provider": "lmstudio"})
    assert pub["base_url"] == "http://127.0.0.1:1234/v1"
    assert llm.save_settings({"clear_api_key": True})["key_set"] is False
    monkeypatch.setenv("DEEPSEEK_API_KEY", "envkey")
    llm.save_settings({"provider": "deepseek"})
    assert llm.get_settings()["api_key"] == "envkey"


def test_groq_key_is_bound_to_provider_when_switching_to_ollama(tmpdb, monkeypatch):
    # Legacy installation with a key saved before provider binding existed.
    db.ex("INSERT INTO settings(key,value) VALUES('provider','groq'),('api_key','saved-groq-key')")
    monkeypatch.setenv("GROQ_API_KEY", "env-groq-key")
    assert llm.effective_settings()["api_key"] == "saved-groq-key"

    public = llm.save_settings({"provider": "ollama", "workers": 4, "api_key": "ignored"})
    assert public["workers"] == 1 and public["key_set"] is False
    assert llm.effective_settings()["api_key"] == ""

    public = llm.save_settings({"provider": "groq", "base_url": "https://other.example/v1"})
    assert public["base_url"] == "https://api.groq.com/openai/v1"
    assert public["workers"] == 4 and public["key_set"] is True
    assert "api_key" not in public and "api_key_provider" not in public
    assert llm.effective_settings()["api_key"] == "saved-groq-key"

    llm.save_settings({"clear_api_key": True})
    assert llm.effective_settings()["api_key"] == "env-groq-key"


@pytest.mark.parametrize("retry_after", ["5", "3600"])
def test_groq_retry_after(tmpdb, monkeypatch, retry_after):
    llm.save_settings({"provider": "groq", "api_key": "key"})
    rate_limit = httpx.Response(429, headers={"Retry-After": retry_after})
    replies = [rate_limit, ok_resp('{"ok": true}')]
    delays = []
    monkeypatch.setattr(llm.httpx, "post", lambda *args, **kwargs: replies.pop(0))
    monkeypatch.setattr(llm.time, "sleep", delays.append)
    if retry_after == "5":
        assert llm.call_json("JSON", "JSON") == {"ok": True}
        assert delays == [5] and not replies
    else:
        with pytest.raises(llm.LLMError, match="Ollama"):
            llm.call_json("JSON", "JSON")
        assert not delays and len(replies) == 1


def test_call_json_logs_and_errors(tmpdb, monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    with pytest.raises(llm.LLMError, match="API-ключ"):
        llm.call_json("s", "u", "P1", "extract")
    log = db.q("SELECT * FROM llm_calls")
    assert log == [] or log[0]["ok"] == 0
    llm.save_settings({"api_key": "k"})
    seen = []

    def post(url, headers=None, json=None, timeout=None):
        seen.append((url, headers, json))
        return ok_resp('{"a": 1}')
    monkeypatch.setattr(llm.httpx, "post", post)
    assert llm.call_json("sys", "usr", "P1", "extract:x") == {"a": 1}
    assert seen[0][0] == "https://api.deepseek.com/chat/completions"
    assert seen[0][2]["response_format"] == {"type": "json_object"} and seen[0][1]["Authorization"] == "Bearer k"
    row = db.q("SELECT * FROM llm_calls WHERE ok=1")[0]
    assert row["pub_id"] == "P1" and row["stage"] == "extract:x" and "usr" in row["prompt"] and row["response"] == '{"a": 1}' and row["ms"] >= 0


@pytest.mark.parametrize("code,text", [(401, "отклонил ключ"), (403, "отклонил ключ"), (402, "Недостаточно средств")])
def test_call_json_http_errors(tmpdb, monkeypatch, code, text):
    llm.save_settings({"api_key": "k"})
    monkeypatch.setattr(llm.httpx, "post", lambda *a, **k: FakeResp(code, text="x"))
    with pytest.raises(llm.LLMError, match=text):
        llm.call_json("s", "u", "P", "st")
    row = db.q("SELECT * FROM llm_calls")[-1]
    assert row["ok"] == 0 and text in row["error"]


def test_call_json_retries_429_then_ok(tmpdb, monkeypatch):
    llm.save_settings({"api_key": "k"})
    seq = [FakeResp(429), FakeResp(503), ok_resp('{"ok": true}')]
    monkeypatch.setattr(llm.httpx, "post", lambda *a, **k: seq.pop(0))
    assert llm.call_json("s", "u") == {"ok": True} and seq == []
    seq2 = [FakeResp(429)] * 10
    monkeypatch.setattr(llm.httpx, "post", lambda *a, **k: seq2.pop(0))
    with pytest.raises(llm.LLMError, match="не ответила"):
        llm.call_json("s", "u")
    def boom(*a, **k):
        raise httpx.ConnectError("refused")
    monkeypatch.setattr(llm.httpx, "post", boom)
    with pytest.raises(llm.LLMError, match="сеть"):
        llm.call_json("s", "u")


def test_parse_json_truncated():
    txt = '```json\n{"in_scope": true, "title": "T \\"x\\"", "results": [{"a": 1, "n": [1,2]}, {"a": 2}, {"a": 3, "fra'
    out = llm.parse_json(txt)
    assert out["in_scope"] is True and out["title"] == 'T "x"' and out["results"] == [{"a": 1, "n": [1, 2]}, {"a": 2}]
    assert llm.parse_json('<think>hmm</think>{"x": [1]}') == {"x": [1]}
    with pytest.raises(llm.LLMError):
        llm.parse_json("совсем не json")


def test_test_connection(tmpdb, monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    r = llm.test_connection()
    assert r["ok"] is False and "ключ" in r["message"]
    llm.save_settings({"api_key": "k"})
    monkeypatch.setattr(llm.httpx, "post", lambda *a, **k: ok_resp('{"ok": true}'))
    assert llm.test_connection()["ok"] is True


def test_fuzzy_enum_and_synonyms():
    cfg = config.load_domain("solar")
    row = pipeline._clean_row(cfg, "results", {"metric_name": "PCE mean (%)", "raw_value": "5.52"}, ["metric_name", "aggregation", "raw_value", "note"], [])
    assert row["metric_name"] == "PCE" and row["raw_value"] == "5.52"
    assert pipeline._fuzzy_enum("Jsc_EQE (mA/cm2)", ["Jsc", "Jsc_EQE"])[0] == "Jsc_EQE"
    assert pipeline._fuzzy_enum("unknown thing", ["PCE", "Voc"]) is None


def test_parse_json_from_reasoning_prose():
    from app import llm
    text = 'We need to output JSON like {"foo": 1}. Let\'s draft:\n\n{\n  "results": [\n    {"metric_name": "A", "raw_value": "1"},\n    {"metric_name": "B", "raw_value": "2"},\n    {"metric_name": "C", "raw'
    out = llm.parse_json(text)
    assert [x["metric_name"] for x in out["results"]] == ["A", "B"]
