"""PDF -> 7 таблиц: текст, два шага LLM (структура, результаты по кускам), нормализация, проверка цитат, физические проверки."""
import json, re, pathlib, time, collections
import httpx
from pypdf import PdfReader

ROOT = pathlib.Path(__file__).resolve().parent.parent
PASSPORT = json.loads((ROOT / "docs" / "passport_area1.json").read_text(encoding="utf-8"))
COLS = PASSPORT["tables"]
VOCAB = PASSPORT["vocabularies"]
METRICS = list(PASSPORT["normalization_units"])
TARGET_UNIT = PASSPORT["normalization_units"]

SYSTEM = ("Ты извлекаешь данные из научных статей про тонкоплёночные солнечные элементы (перовскиты, CIGS, CdTe, кестериты, Sb2Se3, тонкоплёночный Si; "
          "только однопереходные приборы, без тандемов; только экспериментальные работы с изготовленным прибором). "
          "Ничего не выдумывай: если значения нет в тексте — пустая строка. Отвечай только одним JSON-объектом.")

STEP_A = """Статья ниже разбита по страницам (маркеры === PAGE n ===).
Верни JSON:
{{"in_scope": true|false, "exclusion_reason": "почему вне области, если in_scope=false",
 "title": "", "year": "", "doi": "", "authors": ["Фамилия И."],
 "materials": [{{"id":"M1","name_raw":"","formula_normalized":"","material_class":"{mc}","dimensionality":"3D|2D|2D-3D|","additives":"","bandgap_value":"число эВ","bandgap_method":"Tauc|EQE|PL|cited|","note":""}}],
 "devices": [{{"id":"DV1","label":"подпись образца как в статье","absorber_material_id":"M1","role":"control|target|record","type":"cell|minimodule|module","architecture":"n-i-p|p-i-n|substrate|superstrate|","substrate_type":"rigid|flexible|","active_area":"число","area_definition":"designated illumination|aperture|total|","encapsulation":"","note":""}}],
 "layers": [{{"device_id":"DV1","order":1,"function":"front_contact|ETL|absorber|passivation|HTL|interlayer|recombination|back_contact","material":"","thickness":"число","thickness_unit":"nm","deposition_method":"","annealing_temperature":"°C","annealing_time":"10 min","atmosphere":"","note":""}}],
 "measurements": [{{"id":"MS1","device_ids":["DV1"],"type":"J-V|MPP tracking|EQE|stability","spectrum":"AM1.5G","light_intensity":"100","intensity_unit":"mW/cm2","scan_direction":"reverse|forward|average|","scan_rate":"","mask_used":"true|false|","certified":"true|false|","certification_lab":"","temperature":"","stability_protocol":"ISOS-D|ISOS-L|ISOS-T|other|","stress_conditions":"","note":""}}]}}
Правила: слои перечисляй от подложки, подложку (стекло, фольга) не записывай, толщины в нм; прямой и обратный ход развёртки — разные measurements; control — контрольный образец, target — целевой, record — рекордный; если статья вне области, остальные массивы верни пустыми. Для каждого прибора, у которого в статье есть измеренные показатели, заведи device.

ТЕКСТ СТАТЬИ:
{text}"""

STEP_B = """Структура статьи уже извлечена:
{struct}

Ниже часть статьи (страницы {p0}-{p1}). Извлеки из неё ВСЕ значения показателей приборов и ссылки на источник. Верни JSON:
{{"results":[{{"device_id":"DV1 или пусто для cited","measurement_id":"MS1 или пусто","metric_name":"PCE|Voc|Jsc|FF|Rs|Rsh|HI|Jsc_EQE|stabilized_PCE|T80|retained_PCE","raw_value":"число дословно как в статье","raw_unit":"единица дословно: %, V, mV, mA/cm2, fraction, ohm cm2, h ...","aggregation":"champion|mean|median или пусто, если в статье не сказано","spread_value":"","n_devices":"","time_point":"","time_unit":"","origin":"primary|cited","cited_ref":"[12] для cited","page":число,"section":"","table":"Table 1","figure":"","fragment":"ДОСЛОВНАЯ цитата со страницы, до 150 символов, с этим числом"}}],
 "issues":[{{"check":"contradiction|champion_vs_mean|unit_inconsistency|missing_conditions","description":"что не сходится, с числами","page":число,"fragment":"дословная цитата"}}]}}
Правила: каждое значение — отдельный элемент; значения из таблицы и повторы в тексте/аннотации записывай ВСЕ (повторы я сам помечу); cited — значения из чужих работ (введение, ссылки), у них device_id пустой; не включай показатели вне списка metric_name; page — номер из маркера PAGE; fragment копируй символ в символ.

ТЕКСТ:
{text}"""


# ---------- PDF ----------
def read_pdf(path):
    r = PdfReader(str(path))
    pages = []
    for p in r.pages:
        try:
            t = p.extract_text() or ""
        except Exception:
            t = ""
        pages.append(re.sub("[\ud800-\udfff]", "", t).strip())
    return pages


def clean_for_llm(pages):
    """Режем список литературы: после последнего заголовка References/Литература, если он во второй половине."""
    cut = len(pages)
    for i in range(len(pages) - 1, len(pages) // 2 - 1, -1):
        if re.search(r"^\s*(references|литература|список литературы|bibliography)\s*$", pages[i], re.I | re.M):
            cut = i
            break
    return pages[:cut + 1] if cut < len(pages) else pages


def pages_text(pages, start=0):
    return "\n".join(f"=== PAGE {i} ===\n{t}" for i, t in enumerate(pages[start:], start + 1))


def chunk_pages(pages, limit=24000):
    chunks, cur, size = [], [], 0
    for i, t in enumerate(pages, 1):
        if cur and size + len(t) > limit:
            chunks.append(cur); cur, size = [], 0
        cur.append((i, t)); size += len(t) + 20
    if cur:
        chunks.append(cur)
    return chunks


# ---------- LLM ----------
class LLMError(Exception):
    pass


def llm_json(cfg, user, retries=2):
    base = cfg["base_url"].rstrip("/")
    headers = {"Content-Type": "application/json"}
    if cfg.get("api_key"):
        headers["Authorization"] = "Bearer " + cfg["api_key"]
    body = {"model": cfg["model"], "temperature": 0, "max_tokens": 8000,
            "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}]}
    if cfg.get("provider") == "deepseek":
        body["response_format"] = {"type": "json_object"}
    last = None
    for a in range(retries + 1):
        try:
            r = httpx.post(base + "/chat/completions", headers=headers, json=body, timeout=600)
            if r.status_code in (401, 403):
                raise LLMError("API отклонил ключ (%s). Проверьте ключ в настройках." % r.status_code)
            if r.status_code == 402:
                raise LLMError("Недостаточно средств на аккаунте DeepSeek (402).")
            if r.status_code == 429 or r.status_code >= 500:
                time.sleep(3 * (a + 1)); last = "HTTP %s" % r.status_code; continue
            r.raise_for_status()
            text = r.json()["choices"][0]["message"]["content"]
            return parse_json(text)
        except LLMError:
            raise
        except httpx.HTTPError as e:
            last = str(e); time.sleep(3 * (a + 1))
    raise LLMError("Сбой запроса к модели: %s" % last)


def parse_json(text):
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    try:
        return json.loads(text)
    except Exception:
        pass
    # усечённый ответ: достаём все полные объекты массивов
    out, dec = {}, json.JSONDecoder()
    for key in ("results", "issues", "materials", "devices", "layers", "measurements"):
        m = re.search(r'"%s"\s*:\s*\[' % key, text)
        if not m:
            continue
        i, items = m.end(), []
        while True:
            while i < len(text) and text[i] in " \n\r\t,":
                i += 1
            if i >= len(text) or text[i] != "{":
                break
            try:
                obj, j = dec.raw_decode(text, i)
            except Exception:
                break
            items.append(obj); i = j
        out[key] = items
    for key in ("title", "year", "doi", "exclusion_reason"):
        m = re.search(r'"%s"\s*:\s*"((?:[^"\\]|\\.)*)"' % key, text)
        if m:
            out[key] = m.group(1)
    m = re.search(r'"in_scope"\s*:\s*(true|false)', text)
    if m:
        out["in_scope"] = m.group(1) == "true"
    if not out:
        raise LLMError("Модель вернула не-JSON ответ")
    return out


# ---------- нормализация ----------
def fnum(v):
    if v is None or v == "":
        return None
    s = str(v).replace("−", "-").replace("–", "-").replace(",", ".").replace(" ", "").replace(" ", "")
    m = re.search(r"-?\d+(?:\.\d+)?", s)
    return float(m.group(0)) if m else None


def normalize(metric, raw_value, raw_unit):
    v, u = fnum(raw_value), (raw_unit or "").lower().replace(" ", "").replace("·", "").replace("*", "")
    if v is None:
        return None, ""
    tgt = TARGET_UNIT.get(metric, "")
    if metric == "Voc":
        if "mv" in u or (u == "" and v > 10):
            v /= 1000
        return round(v, 4), "V"
    if metric in ("Jsc", "Jsc_EQE"):
        if "ua" in u or "µa" in u:
            v /= 1000
        elif u.startswith("a/") or "a/cm" in u and "ma" not in u:
            v *= 1000
        elif "a/m2" in u and "ma" not in u:
            v /= 10
        elif "ma/m2" in u:
            v /= 10
        return round(v, 4), "mA/cm2"
    if metric in ("PCE", "FF", "HI", "retained_PCE"):
        if "fraction" in u or ("%" not in u and v <= 1.0):
            v *= 100
        return round(v, 4), "%"
    return round(v, 6), tgt


def norm_text(s):
    s = (s or "").lower()
    for a, b in (("ﬁ", "fi"), ("ﬂ", "fl"), ("ﬀ", "ff"), ("−", "-"), ("–", "-"), ("’", "'")):
        s = s.replace(a, b)
    return re.sub(r"\s+", "", s)


# ---------- сборка ----------
def blank(table):
    return {c: "" for c in COLS[table]}


def build(pid, pages, cfg, progress=lambda *a: None, only_meta=False):
    warnings = []
    empty = [i for i, t in enumerate(pages, 1) if len(t) < 200]
    if empty:
        warnings.append("Страницы без текстового слоя (OCR не подключён): " + ", ".join(map(str, empty[:12])) + ("…" if len(empty) > 12 else ""))
    if len(empty) > len(pages) * 0.6:
        raise LLMError("В PDF почти нет текстового слоя (скан). Нужен OCR — извлечение невозможно.")
    cut = clean_for_llm(pages)

    # шаг A: структура
    progress("structure", 0.1, "Извлекаю структуру: материалы, приборы, слои")
    text = pages_text(cut)
    if len(text) > 160000:
        text = text[:160000]
    A = llm_json(cfg, STEP_A.format(mc="|".join(VOCAB["material_class"]), text=text))
    tables = {t: [] for t in COLS}
    in_scope = A.get("in_scope", True) not in (False, "false", "False")
    pub = blank("publications")
    pub.update(publication_id=pid, title=A.get("title", ""), year=str(A.get("year", "") or ""), DOI=A.get("doi", ""),
               authors="; ".join(A["authors"]) if isinstance(A.get("authors"), list) else (A.get("authors") or ""),
               in_scope="true" if in_scope else "false", exclusion_reason="" if in_scope else A.get("exclusion_reason", ""))
    tables["publications"].append(pub)
    if not in_scope:
        return tables, warnings

    mids, dids, msids = {}, {}, {}
    for i, m in enumerate(A.get("materials") or [], 1):
        r = blank("materials")
        mid = str(m.get("id") or f"M{i}")
        mids[mid] = 1
        r.update(publication_id=pid, material_id=mid, material_name_raw=m.get("name_raw", ""), formula_normalized=m.get("formula_normalized", ""),
                 material_class=m.get("material_class", ""), dimensionality=m.get("dimensionality", ""), additives=m.get("additives", ""),
                 bandgap_value=m.get("bandgap_value", ""), bandgap_unit="eV" if m.get("bandgap_value") else "", bandgap_method=m.get("bandgap_method", ""), note=m.get("note", ""))
        tables["materials"].append(r)
    first_mat = next(iter(mids), "")
    for i, d in enumerate(A.get("devices") or [], 1):
        r = blank("devices")
        did = str(d.get("id") or f"DV{i}")
        dids[did] = d.get("label", did)
        am = d.get("absorber_material_id") or first_mat
        r.update(publication_id=pid, device_id=did, absorber_material_id=am if am in mids else first_mat, device_label=d.get("label", ""), device_role=d.get("role", ""),
                 device_type=d.get("type", "") or "cell", architecture=d.get("architecture", ""), substrate_type=d.get("substrate_type", ""),
                 active_area=d.get("active_area", ""), area_unit="cm2" if d.get("active_area") else "", area_definition=d.get("area_definition", ""),
                 encapsulation=d.get("encapsulation", ""), note=d.get("note", ""))
        tables["devices"].append(r)
    for d in A.get("layers") or []:
        did = str(d.get("device_id", ""))
        if did not in dids:
            continue
        n = sum(1 for x in tables["layers"] if x["device_id"] == did) + 1
        r = blank("layers")
        r.update(publication_id=pid, layer_id=f"{did}-L{n}", device_id=did, layer_order=d.get("order", n), layer_function=d.get("function", ""), layer_material=d.get("material", ""),
                 layer_thickness=d.get("thickness", ""), thickness_unit="nm" if d.get("thickness") else "", deposition_method=d.get("deposition_method", ""),
                 annealing_temperature=d.get("annealing_temperature", ""), annealing_time=d.get("annealing_time", ""), atmosphere=d.get("atmosphere", ""), note=d.get("note", ""))
        tables["layers"].append(r)
    for i, m in enumerate(A.get("measurements") or [], 1):
        r = blank("measurements")
        msid = str(m.get("id") or f"MS{i}")
        msids[msid] = 1
        ids = [x for x in (m.get("device_ids") or []) if x in dids] or list(dids)
        r.update(publication_id=pid, measurement_id=msid, device_id=";".join(ids), measurement_type=m.get("type", ""), spectrum=m.get("spectrum", ""),
                 light_intensity=m.get("light_intensity", ""), intensity_unit=m.get("intensity_unit", ""), scan_direction=m.get("scan_direction", ""),
                 scan_rate=m.get("scan_rate", ""), mask_used=str(m.get("mask_used", "")).lower() if m.get("mask_used") != "" else "",
                 certified=str(m.get("certified", "")).lower() if m.get("certified") != "" else "", certification_lab=m.get("certification_lab", ""),
                 temperature=m.get("temperature", ""), stability_protocol=m.get("stability_protocol", ""), stress_conditions=m.get("stress_conditions", ""), note=m.get("note", ""))
        tables["measurements"].append(r)
    struct = json.dumps({"materials": [{"id": m["material_id"], "name": m["material_name_raw"]} for m in tables["materials"]],
                         "devices": [{"id": k, "label": v} for k, v in dids.items()],
                         "measurements": [{"id": m["measurement_id"], "type": m["measurement_type"], "scan": m["scan_direction"], "devices": m["device_id"]} for m in tables["measurements"]]},
                        ensure_ascii=False)

    # шаг B: результаты по кускам
    chunks = chunk_pages(cut)
    raw_res, raw_iss = [], []
    for ci, ch in enumerate(chunks):
        progress("results", 0.3 + 0.6 * ci / len(chunks), f"Извлекаю значения: страницы {ch[0][0]}–{ch[-1][0]} ({ci + 1}/{len(chunks)})")
        text = "\n".join(f"=== PAGE {i} ===\n{t}" for i, t in ch)
        B = llm_json(cfg, STEP_B.format(struct=struct, p0=ch[0][0], p1=ch[-1][0], text=text))
        raw_res += [x for x in (B.get("results") or []) if isinstance(x, dict)]
        raw_iss += [x for x in (B.get("issues") or []) if isinstance(x, dict)]

    progress("post", 0.92, "Нормализация, проверка цитат, физические правила")
    default_ms = next(iter(msids), "")
    rows = []
    for x in raw_res:
        metric = str(x.get("metric_name", "")).strip()
        metric = next((m for m in METRICS if m.lower() == metric.lower()), None)
        if not metric:
            continue
        nv, nu = normalize(metric, x.get("raw_value"), x.get("raw_unit"))
        if nv is None:
            continue
        origin = "cited" if str(x.get("origin", "")).lower() == "cited" else "primary"
        did = str(x.get("device_id") or "")
        if origin == "primary" and did not in dids:
            if len(dids) == 1:
                did = next(iter(dids))
            else:
                continue
        r = blank("results")
        msid = str(x.get("measurement_id") or "")
        if origin == "primary" and msid not in msids:
            msid = default_ms
        mat_id = next((d["absorber_material_id"] for d in tables["devices"] if d["device_id"] == did), "") if origin == "primary" else ""
        agg = str(x.get("aggregation", "")).lower()
        r.update(publication_id=pid, device_id=did if origin == "primary" else "", measurement_id=msid if origin == "primary" else "", material_id=mat_id,
                 metric_name=metric, metric_value=nv, metric_unit=nu, aggregation=agg if agg in ("champion", "mean", "median") else "",
                 spread_value=x.get("spread_value", ""), n_devices=x.get("n_devices", ""), time_point=x.get("time_point", ""), time_unit=x.get("time_unit", ""),
                 raw_value=x.get("raw_value", ""), raw_unit=x.get("raw_unit", ""), normalized_value=nv, normalized_unit=nu, result_origin=origin,
                 cited_publication_id=x.get("cited_ref", "") if origin == "cited" else "", source_page=x.get("page", ""), source_section=x.get("section", ""),
                 source_table=x.get("table", ""), source_figure=x.get("figure", ""), source_fragment=x.get("fragment", ""), extraction_status="extracted")
        rows.append(r)

    # проверка цитат
    ptxt = [norm_text(t) for t in pages]
    not_found = 0
    for r in rows:
        frag = norm_text(r["source_fragment"])
        pg = int(fnum(r["source_page"]) or 0)
        if frag and 1 <= pg <= len(pages) and frag in ptxt[pg - 1]:
            continue
        hit = next((i + 1 for i, t in enumerate(ptxt) if frag and frag in t), None)
        if hit:
            r["source_page"] = hit
        else:
            not_found += 1
            r["note"] = ((r["note"] + "; ") if r["note"] else "") + "цитата не найдена в тексте PDF"
    if not_found:
        warnings.append(f"Цитата не найдена в тексте PDF для {not_found} значений — проверьте источник вручную.")

    # дубликаты: основной — из таблицы либо с меньшим номером страницы
    groups = collections.defaultdict(list)
    for r in rows:
        groups[(r["device_id"], r["measurement_id"], r["metric_name"], r["normalized_value"], r["aggregation"], r["result_origin"], r["cited_publication_id"], r["time_point"])].append(r)
    for g in groups.values():
        g.sort(key=lambda r: (0 if r["source_table"] else 1, int(fnum(r["source_page"]) or 999)))
        g[0]["_main"] = True
    n = 0
    for r in rows:
        n += 1
        r["result_id"] = f"R{n:03d}"
    for g in groups.values():
        for r in g[1:]:
            r["duplicate_of"] = g[0]["result_id"]
    for r in rows:
        r.pop("_main", None)
        tables["results"].append(r)

    # замечания: от модели + физика
    ni = 0
    def add_issue(check, desc, page, frag, rec_ids=""):
        nonlocal ni
        ni += 1
        i = blank("issues")
        i.update(publication_id=pid, issue_id=f"I{ni}", record_ids=rec_ids, check_name=check, description=desc, source_page=page, source_fragment=frag)
        tables["issues"].append(i)
    for x in raw_iss:
        if x.get("description"):
            add_issue(str(x.get("check") or "contradiction"), str(x["description"]), x.get("page", ""), x.get("fragment", ""))
    for i in physics(tables):
        add_issue(*i)
    return tables, warnings


def physics(tables):
    """jv_consistency, voc_limit, range, eqe_consistency. Возвращает кортежи для add_issue."""
    out, g = [], collections.defaultdict(dict)
    for r in tables["results"]:
        if r["duplicate_of"] or r["result_origin"] == "cited" or r["time_point"]:
            continue
        v = fnum(r["normalized_value"])
        if v is not None:
            g[(r["device_id"], r["measurement_id"], r["aggregation"])][r["metric_name"]] = (v, r)
    eg = {m["material_id"]: fnum(m["bandgap_value"]) for m in tables["materials"]}
    amat = {d["device_id"]: d["absorber_material_id"] for d in tables["devices"]}
    lab = {d["device_id"]: d["device_label"] or d["device_id"] for d in tables["devices"]}
    for (dev, ms, agg), m in g.items():
        P = lambda k: m[k][0] if k in m else None
        R = lambda k: m[k][1]
        pce, voc, jsc, ff = P("PCE"), P("Voc"), P("Jsc"), P("FF")
        if None not in (pce, voc, jsc, ff) and pce > 0:
            calc = voc * jsc * ff / 100
            if abs(calc - pce) / pce > 0.03:
                out.append(("jv_consistency", f"{lab.get(dev, dev)}: PCE={pce}%, расчёт Voc·Jsc·FF={calc:.2f}% (расхождение {abs(calc - pce) / pce * 100:.1f}% > 3%, Pin принят 100 мВт/см²)",
                            R("PCE")["source_page"], R("PCE")["source_fragment"], ";".join(R(k)["result_id"] for k in ("PCE", "Voc", "Jsc", "FF"))))
        if ff is not None and not 10 <= ff <= 90:
            out.append(("range", f"{lab.get(dev, dev)}: FF={ff}% вне 10–90%", R("FF")["source_page"], R("FF")["source_fragment"], R("FF")["result_id"]))
        if jsc is not None and not 0 < jsc < 50:
            out.append(("range", f"{lab.get(dev, dev)}: Jsc={jsc} мА/см² вне 0–50", R("Jsc")["source_page"], R("Jsc")["source_fragment"], R("Jsc")["result_id"]))
        if pce is not None and not 0 < pce < 35:
            out.append(("range", f"{lab.get(dev, dev)}: PCE={pce}% вне 0–35", R("PCE")["source_page"], R("PCE")["source_fragment"], R("PCE")["result_id"]))
        e = eg.get(amat.get(dev))
        if voc is not None and e and voc >= e:
            out.append(("voc_limit", f"{lab.get(dev, dev)}: Voc={voc} В ≥ Eg/q={e} эВ", R("Voc")["source_page"], R("Voc")["source_fragment"], R("Voc")["result_id"]))
        je = P("Jsc_EQE")
        if jsc and je and abs(je - jsc) / jsc > 0.05:
            out.append(("eqe_consistency", f"{lab.get(dev, dev)}: Jsc(ВАХ)={jsc}, Jsc(EQE)={je} расходятся >5%", R("Jsc")["source_page"], R("Jsc")["source_fragment"], f'{R("Jsc")["result_id"]};{R("Jsc_EQE")["result_id"]}'))
    return out
