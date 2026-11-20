"""Извлечение results через локальный LM Studio. qwen_extract.py <text_dir> <ID> <out_dir>
Пишет out_dir/<ID>/results.csv (минимальный набор колонок для score.py) и raw.json."""
import sys, json, csv, re, pathlib, urllib.request, time
tdir, pid, out = pathlib.Path(sys.argv[1]), sys.argv[2], pathlib.Path(sys.argv[3])
MODEL = "huihui-qwen3.5-9b-claude-4.6-opus-abliterated"
pages = sorted((tdir / pid).glob("p*.txt"))
text = "\n".join(f"=== PAGE {i} ===\n{p.read_text(encoding='utf-8')}" for i, p in enumerate(pages, 1))
text = text[:90000]
prompt = f"""Ты извлекаешь данные из статьи про тонкоплёночные солнечные элементы. Верни ТОЛЬКО JSON-массив без пояснений.
Каждый элемент — одно значение показателя одного прибора:
{{"device": "подпись прибора/образца", "metric": "PCE|Voc|Jsc|FF|Rs|Rsh|HI|Jsc_EQE|stabilized_PCE|T80|retained_PCE",
 "value": число как в статье, "unit": "единица как в статье", "aggregation": "champion|mean|median|", "origin": "primary|cited", "page": номер PAGE, "fragment": "дословная цитата до 120 символов"}}
Правила: origin=cited — значения из чужих работ (введение, ссылки); ничего не выдумывай; если статья расчётная без прибора — верни [].
Включай значения из таблиц и текста, повторы из аннотации тоже.

ТЕКСТ СТАТЬИ:
{text}"""
req = urllib.request.Request("http://127.0.0.1:1234/v1/chat/completions", json.dumps({
    "model": MODEL, "temperature": 0, "max_tokens": 12000,
    "messages": [{"role": "user", "content": prompt}]}).encode(), {"Content-Type": "application/json"})
t0 = time.time()
resp = json.load(urllib.request.urlopen(req, timeout=3000))
msg = resp["choices"][0]["message"]["content"]
d = out / pid; d.mkdir(parents=True, exist_ok=True)
(d / "raw.json").write_text(msg, encoding="utf-8")
m = re.search(r"\[.*\]", re.sub(r"<think>.*?</think>", "", msg, flags=re.S), re.S)
items = json.loads(m.group(0)) if m else []
unit_norm = {"mv": 0.001, "fraction": 1}
rows = []
for k, it in enumerate(items, 1):
    try: v = float(str(it["value"]).replace(",", "."))
    except Exception: continue
    CANON = {m.lower(): m for m in ("PCE","Voc","Jsc","FF","Rs","Rsh","HI","Jsc_EQE","stabilized_PCE","T80","retained_PCE")}
    met, u = CANON.get(str(it.get("metric", "")).lower(), it.get("metric", "")), str(it.get("unit", "")).lower()
    if met == "Voc" and ("mv" in u or v > 10): v = v / 1000
    if met in ("FF", "PCE") and v <= 1.0 and "%" not in u: v = v * 100
    rows.append(dict(publication_id=pid, result_id=f"R{k:03d}", metric_name=met, normalized_value=round(v, 4),
                     aggregation=it.get("aggregation", ""), result_origin=it.get("origin", "primary"),
                     source_page=it.get("page", ""), source_fragment=it.get("fragment", ""), duplicate_of="", extraction_status="extracted"))
with open(d / "results.csv", "w", newline="", encoding="utf-8") as f:
    w = csv.DictWriter(f, fieldnames=list(rows[0].keys()) if rows else ["publication_id"]); w.writeheader(); w.writerows(rows)
print(pid, len(rows), "строк,", round(time.time() - t0), "сек, токенов:", resp.get("usage", {}).get("total_tokens"))
