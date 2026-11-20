import {
  $, $$, esc, fmt, pct, dt, state, role, isAdmin, get, post, patch, put, del, toast, fail, modal, badge, sevBadge, originRu,
  srcLink, srcUrl, options, renderAny, loadSchema, dropSchemaCache, loadYaml, qs, api,
} from "./api.js";

export const ADMIN_TABS = [
  ["corpus", "Корпус", ["admin"]], ["pipeline", "Конвейер", ["admin"]], ["editor", "Записи", ["admin", "annotator"]],
  ["issues", "Замечания", ["admin", "annotator"]], ["config", "Схемы и правила", ["admin"]], ["quality", "Качество", ["admin", "annotator"]],
  ["users", "Пользователи и аудит", ["admin"]], ["settings", "Модель и ключ", ["admin"]],
];
const STAGES = ["register", "parse", "segment", "extract", "verify", "normalize", "validate", "publish"];
const STAGE_RU = { register: "регистрация", parse: "разбор текста", segment: "сегментация", extract: "извлечение", verify: "сверка цитат", normalize: "нормализация", validate: "проверка правилами", publish: "публикация" };

let timers = [];
const every = (ms, fn) => { const t = setInterval(fn, ms); timers.push(t); return t; };
const clearTimers = () => { timers.forEach(clearInterval); timers = []; };

export async function adminRoute(parts, params, app, current) {
  clearTimers();
  const tabs = ADMIN_TABS.filter(t => t[2].includes(role()));
  let tab = parts[0] || tabs[0][0];
  if (!tabs.some(t => t[0] === tab)) { app.innerHTML = `<div class="empty">Нет доступа к этому разделу для вашей роли.</div>`; return; }
  app.innerHTML = `<div class="sub">${tabs.map(t => `<a href="#/admin/${t[0]}" class="${t[0] === tab ? "on" : ""}">${t[1]}</a>`).join("")}</div><div id="adm"><div class="empty"><span class="spin"></span>Загрузка…</div></div>`;
  const box = $("#adm");
  const F = { corpus, pipeline, editor, issues: adminIssues, config, quality, users, settings }[tab];
  try { await F(box, params, current); } catch (e) { if (current()) box.innerHTML = `<div class="card"><h2>Ошибка</h2><p>${esc(e.message)}</p></div>`; }
}

const dom = () => state.domain;
const noteBox = (t, c = "") => `<div class="note ${c}">${t}</div>`;
async function run(btn, fn) {
  if (btn) btn.disabled = true;
  try { return await fn(); } catch (e) { fail(e); } finally { if (btn) btn.disabled = false; }
}

// ======================= Корпус =======================
async function corpus(box, p, current) {
  const draw = async () => {
    const [c, inv, ocr, ocrSt] = await Promise.all([
      get("/corpus", { domain: dom() }), get("/corpus/inventory", { domain: dom() }).catch(() => null),
      get("/ocr/queue", { domain: dom() }).catch(() => []), get("/ocr/status").catch(() => null),
    ]);
    if (!current()) return;
    const sources = Array.isArray(c) ? c : (c.sources || []);
    const summary = Array.isArray(c) ? null : (c.inventory || c.summary || null);
    const queue = Array.isArray(ocr) ? ocr : (ocr.items || ocr.queue || []);
    const ocrOk = ocrSt && (ocrSt.available ?? ocrSt.installed ?? ocrSt.ok);
    const invd = inv || summary || {};
    const pickList = k => { const v = invd[k]; return Array.isArray(v) ? v : null; };
    box.innerHTML = `<h1>Корпус</h1><p class="lead">Откуда берутся PDF для области «${esc(dom())}», что нашла инвентаризация и какие статьи ждут распознавания.</p>
    <div class="card"><h3>Источники</h3>
      <div class="tw"><table><thead><tr><th>Область</th><th>Путь</th><th>Набор</th><th></th></tr></thead><tbody>${sources.map(s => `<tr><td>${esc(s.domain || dom())}</td><td class="mono">${esc(s.path)}</td><td>${esc(s.split || "")}</td><td><button class="btn sm" data-scan="${esc(s.path)}">Сканировать</button></td></tr>`).join("") || `<tr><td colspan="4" class="muted">Источников нет</td></tr>`}</tbody></table></div>
      <form id="srcf" class="form" style="margin-top:12px"><label>Путь к папке с PDF<input type="text" name="path" required placeholder="/путь/к/папке"></label><label>Набор<select name="split"><option value="">—</option><option>dev</option><option>test</option><option>train</option></select></label>
      <div style="align-self:end"><button class="btn primary" type="submit">Добавить источник</button></div></form>
      <div class="actions"><button class="btn" id="scanAll">Сканировать всё</button><span id="scanMsg" class="muted"></span></div></div>
    <div class="card"><h3>Загрузка PDF в область «${esc(dom())}»</h3>
      <label class="drop" id="drop"><b>Перетащите PDF сюда</b><span>или нажмите, чтобы выбрать файлы</span><input type="file" id="fi" accept="application/pdf,.pdf" multiple hidden></label><div id="upMsg"></div></div>
    <div class="card"><h3>Инвентаризация</h3>${Object.keys(invd).length ? `
      <div class="stats">${Object.entries(invd).filter(([, v]) => typeof v === "number").map(([k, v]) => `<div class="stat"><b>${v}</b><span>${esc(INV_RU[k] || k)}</span></div>`).join("")}</div>
      ${["duplicates", "unreadable", "excluded", "out_of_scope", "scope_boundary", "boundary"].map(k => pickList(k) && pickList(k).length ? `<h3>${esc(INV_RU[k] || k)}</h3>${renderAny(pickList(k))}` : "").join("")}
      ${Object.entries(invd).filter(([k, v]) => typeof v === "object" && v && !["duplicates", "unreadable", "excluded", "out_of_scope", "scope_boundary", "boundary"].includes(k)).map(([k, v]) => `<h3>${esc(INV_RU[k] || k)}</h3>${renderAny(v)}`).join("")}`
      : `<span class="muted">Отчёта пока нет: добавьте источник и запустите сканирование.</span>`}</div>
    <div class="card"><h3>Очередь OCR</h3>
      ${ocrSt && !ocrOk ? noteBox("OCR недоступен: " + esc(ocrSt.message || "tesseract не установлен. Установите tesseract-ocr и ocrmypdf на сервере, затем повторите."), "warnbox") : ""}
      <div class="tw"><table><thead><tr><th class="chk-col"></th><th>ID</th><th>Название</th><th>Статус OCR</th></tr></thead><tbody>${queue.map(q => `<tr><td><input type="checkbox" data-ocr="${esc(q.id || q.pub_id)}" checked></td><td class="mono">${esc(q.id || q.pub_id)}</td><td>${esc(q.title || "")}</td><td>${esc(q.ocr_status || q.status || "")}</td></tr>`).join("") || `<tr><td colspan="4" class="muted">Очередь пуста</td></tr>`}</tbody></table></div>
      <div class="actions"><button class="btn primary" id="ocrRun" ${queue.length ? "" : "disabled"}>Запустить OCR</button><span id="ocrMsg"></span></div></div>`;
    $("#srcf").onsubmit = async e => { e.preventDefault(); const f = new FormData(e.target); await run($("button", e.target), async () => { await post("/corpus/sources", { domain: dom(), path: f.get("path"), split: f.get("split") }); toast("Источник добавлен", "ok"); draw(); }); };
    const scan = async (btn, path) => run(btn, async () => {
      $("#scanMsg").innerHTML = `<span class="spin"></span>Сканирую…`;
      const r = await post("/corpus/scan", { domain: dom(), ...(path ? { path } : {}) });
      $("#scanMsg").textContent = `Добавлено: ${(r.added || []).length}, пропущено: ${(r.skipped || []).length}, дубликатов: ${(r.duplicates || []).length}, нечитаемых: ${(r.unreadable || []).length}`;
      setTimeout(draw, 1200);
    });
    $$("[data-scan]").forEach(b => b.onclick = () => scan(b, b.dataset.scan)); $("#scanAll").onclick = e => scan(e.target);
    const drop = $("#drop"), fi = $("#fi");
    const upload = async files => {
      const fl = [...files].filter(f => /\.pdf$/i.test(f.name));
      if (!fl.length) return toast("Нужны файлы PDF", "bad");
      const fd = new FormData(); fd.append("domain", dom()); fl.forEach(f => fd.append("files", f));
      $("#upMsg").innerHTML = `<span class="spin"></span>Загружаю ${fl.length} файл(ов)…`;
      try { const r = await api("/upload", { method: "POST", body: fd }); $("#upMsg").innerHTML = noteBox("Загружено: " + esc(JSON.stringify(r.added || r.ids || r)), "ok"); toast("Файлы загружены", "ok"); draw(); } catch (e) { $("#upMsg").innerHTML = noteBox(esc(e.message), "badbox"); }
    };
    fi.onchange = () => upload(fi.files);
    ["dragover", "dragenter"].forEach(ev => drop.addEventListener(ev, e => { e.preventDefault(); drop.classList.add("over"); }));
    ["dragleave", "drop"].forEach(ev => drop.addEventListener(ev, e => { e.preventDefault(); drop.classList.remove("over"); }));
    drop.addEventListener("drop", e => upload(e.dataTransfer.files));
    $("#ocrRun").onclick = e => run(e.target, async () => {
      const ids = $$("[data-ocr]:checked").map(x => x.dataset.ocr);
      try { const r = await post("/ocr/run", { pub_ids: ids }); $("#ocrMsg").innerHTML = noteBox(esc(r.message || "Запущено: " + ids.length), "ok"); }
      catch (er) { $("#ocrMsg").innerHTML = noteBox(esc(er.message), "warnbox"); }
    });
  };
  await draw();
}
const INV_RU = { total: "всего файлов", pdfs: "PDF", added: "добавлено", registered: "зарегистрировано", skipped: "пропущено", duplicates: "дубликаты", unreadable: "нечитаемые", needs_ocr: "нужен OCR", excluded: "вне области", out_of_scope: "вне области", in_scope: "в области", scope_boundary: "граница области", boundary: "граница области", by_split: "по наборам", by_status: "по статусам" };

// ======================= Конвейер =======================
async function pipeline(box, p, current) {
  const sel = new Set();
  let jobs = [];
  const jid = j => j.id || j.pub_id || (j.publication && j.publication.id);
  const stagesOf = j => {
    const s = j.stages || [];
    const m = {}; (Array.isArray(s) ? s : Object.entries(s).map(([k, v]) => ({ stage: k, ...(typeof v === "object" ? v : { status: v }) }))).forEach(x => { m[x.stage] = x; });
    return m;
  };
  const draw = () => {
    const total = jobs.length * STAGES.length;
    const done = jobs.reduce((n, j) => n + Object.values(stagesOf(j)).filter(s => s.status === "done").length, 0);
    const running = jobs.filter(j => j.status === "processing" || Object.values(stagesOf(j)).some(s => s.status === "running")).length;
    box.innerHTML = `<h1>Конвейер</h1><p class="lead">Восемь стадий обработки по каждой публикации. Повтор запускается с места сбоя, записи не дублируются.</p>
    <div class="stages-key">${STAGES.map((s, i) => `<span><b>${i + 1}</b> ${esc(STAGE_RU[s])}</span>`).join("")}</div>
    <div class="actions"><button class="btn primary" id="runSel" ${sel.size ? "" : "disabled"}>Запустить выбранные (${sel.size})</button><button class="btn" id="runAll">Запустить все</button>
      <span class="muted">${running ? `<span class="spin"></span>в работе: ${running}` : ""} Выполнено стадий: ${done} из ${total}</span></div>
    <div class="prog"><i style="width:${total ? 100 * done / total : 0}%"></i></div>
    <div class="tw" style="margin-top:12px"><table><thead><tr><th class="chk-col"><input type="checkbox" id="all"></th><th>ID</th><th>Название</th><th>Статус</th>${STAGES.map((s, i) => `<th title="${esc(STAGE_RU[s])}">${i + 1}</th>`).join("")}<th></th></tr></thead><tbody>
    ${jobs.map(j => { const id = jid(j), st = stagesOf(j); const failed = STAGES.find(s => st[s] && st[s].status === "error"); return `<tr><td><input type="checkbox" data-s="${esc(id)}" ${sel.has(id) ? "checked" : ""}></td><td class="mono">${esc(id)}</td><td class="cut">${esc(j.title || (j.publication && j.publication.title) || "")}${failed ? `<div class="warn">${esc(st[failed].message || "")}</div>` : ""}</td><td>${badge(j.status)}</td>
      ${STAGES.map(s => { const x = st[s]; const c = x ? x.status : "pending"; return `<td><span class="stage ${c}" title="${esc(STAGE_RU[s])}: ${esc(c)}${x && x.attempt ? ", попытка " + x.attempt : ""}${x && x.message ? " — " + esc(x.message) : ""}">${c === "done" ? "ok" : c === "error" ? "!" : c === "running" ? "…" : "·"}</span></td>`; }).join("")}
      <td style="white-space:nowrap"><button class="btn sm" data-retry="${esc(id)}">${failed ? "Повторить с сбоя" : "Повторить"}</button> <button class="btn sm" data-log="${esc(id)}">Логи</button></td></tr>`; }).join("") || `<tr><td colspan="13" class="empty">Публикаций нет</td></tr>`}</tbody></table></div>`;
    $$("[data-s]").forEach(c => c.onchange = () => { c.checked ? sel.add(c.dataset.s) : sel.delete(c.dataset.s); $("#runSel").disabled = !sel.size; $("#runSel").textContent = `Запустить выбранные (${sel.size})`; });
    $("#all").onchange = e => { jobs.forEach(j => e.target.checked ? sel.add(jid(j)) : sel.delete(jid(j))); draw(); };
    $("#runSel").onclick = e => run(e.target, async () => { await post("/jobs/run", { pub_ids: [...sel] }); toast("Запущено", "ok"); load(); });
    $("#runAll").onclick = e => { if (!confirm("Запустить обработку всех публикаций области? Модель будет вызвана для каждой.")) return; run(e.target, async () => { await post("/jobs/run", { domain: dom(), all: true }); toast("Запущено", "ok"); load(); }); };
    $$("[data-retry]").forEach(b => b.onclick = () => run(b, async () => { await post(`/jobs/${encodeURIComponent(b.dataset.retry)}/retry`); toast("Повтор запущен", "ok"); load(); }));
    $$("[data-log]").forEach(b => b.onclick = () => llmLog(b.dataset.log));
  };
  const load = async () => { try { const r = await get("/jobs", { domain: dom() }); jobs = Array.isArray(r) ? r : (r.items || r.jobs || []); if (current()) draw(); } catch (e) { if (!jobs.length) throw e; } };
  await load();
  every(3000, () => { if (!current()) return clearTimers(); if (!$(".modal-bg")) load(); });
}
async function llmLog(pub) {
  const m = modal(`<h2>Вызовы модели: ${esc(pub)}</h2><div id="llm"><span class="spin"></span>Загрузка…</div>`, { wide: true });
  try {
    const r = await get("/llm_calls", { pub, limit: 100 });
    const list = Array.isArray(r) ? r : (r.items || []);
    $("#llm", m.el).innerHTML = `<div class="tw"><table><thead><tr><th>Время</th><th>Стадия</th><th>Модель</th><th>Статус</th><th class="num">Токены</th><th></th></tr></thead><tbody>${list.map(c => `<tr><td>${esc(dt(c.ts))}</td><td>${esc(c.stage || "")}</td><td>${esc(c.model || "")}</td><td>${c.error ? `<span class="tag bad">${esc(c.error).slice(0, 60)}</span>` : badge(c.status === "ok" || c.ok ? "done" : c.status || "done")}</td><td class="num">${esc(c.tokens ?? ((c.tokens_in || 0) + (c.tokens_out || 0) || ""))}</td><td><button class="btn sm" data-c="${c.id}">Промпт и ответ</button></td></tr>`).join("") || `<tr><td colspan="6" class="muted">Вызовов нет</td></tr>`}</tbody></table></div><div id="callBody"></div>`;
    $$("[data-c]", m.el).forEach(b => b.onclick = async () => {
      const c = await get("/llm_calls/" + b.dataset.c).catch(fail); if (!c) return;
      $("#callBody", m.el).innerHTML = `<h3 style="margin-top:14px">Системный промпт</h3><pre class="box">${esc(c.system ?? c.system_prompt ?? "")}</pre><h3>Запрос</h3><pre class="box">${esc(c.user ?? c.prompt ?? c.user_prompt ?? "")}</pre><h3>Ответ</h3><pre class="box">${esc(c.response ?? c.answer ?? c.output ?? "")}</pre>${c.error ? `<h3>Ошибка</h3><pre class="box">${esc(c.error)}</pre>` : ""}`;
      $("#callBody", m.el).scrollIntoView({ behavior: "smooth" });
    });
  } catch (e) { $("#llm", m.el).innerHTML = noteBox(esc(e.message), "badbox"); }
}

// ======================= Редактор записей =======================
const LONG = new Set(["source_fragment", "note", "description", "exclusion_reason", "title", "authors", "additives", "stress_conditions"]);
async function editor(box, p, current) {
  const schema = await loadSchema(dom(), true);
  const pubs = await get("/publications", { domain: dom() });
  let pub = p.pub || "", recId = p.rec ? +p.rec : null;
  if (recId && !pub) { const r = await get("/records/" + recId); pub = r.record.pub_id; }
  if (!pub && pubs.length) pub = pubs[0].id;
  let data = null, cur = null;
  const load = async () => { data = pub ? await get("/publications/" + encodeURIComponent(pub), { include_rejected: 1 }) : { records: {} }; if (current()) draw(); };
  const entities = () => Object.keys(schema.entities || {}).filter(e => e !== "publications");
  const flat = () => Object.entries(data.records || {}).flatMap(([e, rows]) => rows);
  const draw = () => {
    if (recId && !cur) cur = flat().find(r => r.id === recId) || null;
    if (cur) cur = flat().find(r => r.id === cur.id) || cur;
    box.innerHTML = `<h1>Редактор записей</h1><p class="lead">Правка значений по схеме области. Каждое изменение сохраняется в истории; запись после правки получает статус «вручную» и перепроверяется правилами.</p>
    <div class="filters"><label>Публикация <select id="pubsel">${options(pubs.map(x => [x.id, `${x.id} — ${(x.title || "").slice(0, 70)}`]), pub)}</select></label>
      <button class="btn" id="newbtn">Создать запись</button>${pub ? srcLink(pub, 1, "", "Открыть PDF").replace('class="btn"', 'class="btn"') : ""}</div>
    <div class="cols ed">
    <div class="card" style="max-height:70vh;overflow:auto">${Object.entries(data.records || {}).map(([e, rows]) => `<h3>${esc(e)} <span class="muted">(${rows.length})</span></h3>${rows.map(r => `<div class="layer click ${cur && cur.id === r.id ? "" : ""}" style="cursor:pointer;grid-template-columns:70px 1fr auto;${cur && cur.id === r.id ? "outline:2px solid var(--accent)" : ""}" data-r="${r.id}"><span class="mono">${esc(r.rid)}</span><span class="cut">${esc(r.data.metric_name || r.data.device_label || r.data.material_name_raw || r.data.layer_material || r.data.measurement_type || "")} ${esc(r.data.raw_value || "")}</span>${badge(r.status)}</div>`).join("")}`).join("") || `<div class="muted">Записей нет</div>`}</div>
    <div class="card" id="form">${cur ? formHtml(cur) : `<div class="muted">Выберите запись слева или создайте новую.</div>`}</div></div>`;
    $("#pubsel").onchange = e => { pub = e.target.value; cur = null; recId = null; load(); };
    $$("[data-r]").forEach(d => d.onclick = () => { cur = flat().find(r => r.id === +d.dataset.r); recId = null; draw(); });
    $("#newbtn").onclick = () => newRecord();
    if (cur) bindForm();
  };
  const fieldsOf = entity => {
    const e = (schema.entities || {})[entity] || { columns: Object.keys((cur && cur.data) || {}), fields: {} };
    return (e.columns || []).filter(c => c !== "publication_id" && c !== e.id);
  };
  const inputFor = (entity, name, val) => {
    const f = (((schema.entities || {})[entity] || {}).fields || {})[name] || {};
    const v = val ?? "";
    const id = "f_" + name;
    if (f.type === "enum") { const vocab = f.vocab || []; return `<select id="${id}" data-f="${name}">${options(vocab.includes(v) || !v ? vocab : [v, ...vocab], v, "—")}</select>`; }
    if (f.type === "bool") return `<select id="${id}" data-f="${name}">${options([["true", "да"], ["false", "нет"]], String(v).toLowerCase(), "—")}</select>`;
    if (LONG.has(name)) return `<textarea id="${id}" data-f="${name}" rows="3">${esc(v)}</textarea>`;
    if (f.type === "number" || f.type === "int") return `<input type="text" inputmode="decimal" id="${id}" data-f="${name}" value="${esc(v)}">`;
    return `<input type="text" id="${id}" data-f="${name}" value="${esc(v)}">`;
  };
  const formHtml = r => `<div class="row sp"><h3 style="margin:0">${esc(r.entity)} · ${esc(r.rid)}</h3>${badge(r.status)}</div>
    ${r.status === "rejected" ? noteBox("Запись отклонена и не попадает в экспорт. Её можно восстановить.", "warnbox") : ""}
    <div class="form" style="margin-top:12px">${fieldsOf(r.entity).map(n => `<label class="${LONG.has(n) ? "wide" : ""}">${esc(n)}${inputFor(r.entity, n, r.data[n])}</label>`).join("")}</div>
    <label class="form" style="display:grid;margin-top:12px"><span style="font-size:13px;color:var(--ink-2)">Комментарий к изменению</span><input type="text" id="cmt" placeholder="Почему меняем"></label>
    <div class="actions"><button class="btn primary" id="save">Сохранить</button>
      ${r.status !== "verified" && r.status !== "rejected" ? `<button class="btn" data-act="confirm">Подтвердить</button>` : ""}
      ${r.status !== "rejected" ? `<button class="btn danger" data-act="reject">Отклонить</button>` : `<button class="btn" data-act="restore">Восстановить</button>`}
      ${r.source_page ? srcLink(r.pub_id, r.source_page, r.source_fragment) : ""}<button class="btn" id="hist">История</button><a class="btn" href="#/records/${r.id}">Карточка</a></div><div id="histBox"></div>`;
  const bindForm = () => {
    $("#save").onclick = e => run(e.target, async () => {
      const out = {};
      $$("[data-f]", $("#form")).forEach(el => { const k = el.dataset.f; if ((cur.data[k] ?? "") !== el.value) out[k] = el.value; });
      if (!Object.keys(out).length) return toast("Изменений нет");
      await patch("/records/" + cur.id, { data: out, comment: $("#cmt").value }); toast("Сохранено", "ok"); await load();
    });
    $$("[data-act]", $("#form")).forEach(b => b.onclick = () => run(b, async () => {
      let comment = $("#cmt").value; if (b.dataset.act === "reject" && !comment) { comment = prompt("Причина отклонения"); if (comment === null) return; }
      await post(`/records/${cur.id}/action`, { action: b.dataset.act, comment }); toast("Готово", "ok"); await load();
    }));
    $("#hist").onclick = async () => {
      const h = await get(`/records/${cur.id}/history`).catch(fail); if (!h) return;
      const list = Array.isArray(h) ? h : (h.items || []);
      $("#histBox").innerHTML = `<h3 style="margin-top:14px">История изменений</h3>${list.length ? `<ul class="hist">${list.map(x => `<li><b>${esc(x.author || x.user || "")}</b> · ${esc(dt(x.ts))}${x.comment ? " — " + esc(x.comment) : ""}<br><span class="mono muted">${esc(typeof x.changes === "string" ? x.changes : JSON.stringify(x.changes ?? x.diff ?? x.after ?? ""))}</span></li>`).join("")}</ul>` : `<span class="muted">Изменений не было</span>`}`;
    };
  };
  const newRecord = () => {
    const ents = entities();
    const m = modal(`<h2>Новая запись</h2><div class="form" style="grid-template-columns:1fr"><label>Сущность<select id="ne">${options(ents)}</select></label><div id="nf"></div><div class="note">Идентификатор (rid) будет присвоен автоматически. Новая запись получит статус «вручную».</div><button class="btn primary" id="mk">Создать</button></div>`, { wide: true });
    const fill = () => { const e = $("#ne", m.el).value; $("#nf", m.el).innerHTML = `<div class="form">${fieldsOf(e).filter(n => n !== (schema.entities[e].id)).map(n => `<label class="${LONG.has(n) ? "wide" : ""}">${esc(n)}${inputFor(e, n, "")}</label>`).join("")}</div>`; };
    $("#ne", m.el).onchange = fill; fill();
    $("#mk", m.el).onclick = e => run(e.target, async () => {
      const d = {}; $$("[data-f]", m.el).forEach(el => { if (el.value !== "") d[el.dataset.f] = el.value; });
      const r = await post("/records", { pub_id: pub, entity: $("#ne", m.el).value, data: d });
      m.close(); toast("Запись создана", "ok"); recId = r.id || (r.record && r.record.id) || null; cur = null; await load();
    });
  };
  await load();
}

// ======================= Очередь замечаний =======================
async function adminIssues(box, p, current) {
  let status = p.status ?? "open", rule = p.rule || "";
  const draw = async () => {
    const list = await get("/issues", { domain: dom(), status, rule });
    if (!current()) return;
    const rules = [...new Set(list.map(i => i.rule_id).concat(rule ? [rule] : []))].sort();
    box.innerHTML = `<h1>Очередь замечаний</h1><p class="lead">Решение по каждому замечанию: принять (значение признано ошибочным), исправить (запись поправлена) или оставить (расхождение допустимо). Комментарий сохраняется.</p>
    <div class="filters"><select id="fs">${options(["open", "accepted", "fixed", "kept"].map(s => [s, badge(s).replace(/<[^>]+>/g, "")]), status, "Любой статус")}</select><select id="fr">${options(rules, rule, "Любое правило")}</select><span class="count">${list.length} шт.</span></div>
    ${list.map(i => `<div class="card"><div class="row sp"><div class="row">${sevBadge(i.severity)}<span class="tag">${esc(i.rule_id)}</span><span class="tag dup">${esc(originRu(i.origin))}</span>${badge(i.status)}<span class="mono muted">${esc(i.pub_id)}</span></div>
      <div class="row">${(i.record_ids || []).map(r => `<a class="tag ok" href="#" data-rid="${esc(r)}" data-pub="${esc(i.pub_id)}" data-go="1">${esc(r)}</a>`).join("")}${i.source_page ? `<a class="btn sm" target="_blank" rel="noopener" href="${esc(srcUrl(i.pub_id, i.source_page, i.source_fragment))}">Источник, стр. ${i.source_page}</a>` : ""}</div></div>
      <p style="margin:10px 0">${esc(i.description)}</p>${i.source_fragment ? `<div class="quote">${esc(i.source_fragment)}</div>` : ""}${i.comment ? `<div class="muted">Комментарий: ${esc(i.comment)}</div>` : ""}
      <div class="row" style="margin-top:8px"><input type="text" placeholder="Комментарий" data-c="${i.id}" value="" style="flex:1;min-width:180px">
      ${["accepted:Принять", "fixed:Исправлено", "kept:Оставить"].map(x => { const [s, l] = x.split(":"); return `<button class="btn sm" data-res="${i.id}" data-st="${s}">${l}</button>`; }).join("")}</div></div>`).join("") || `<div class="empty">Замечаний нет</div>`}`;
    $("#fs").onchange = e => { status = e.target.value; draw(); }; $("#fr").onchange = e => { rule = e.target.value; draw(); };
    $$("[data-res]").forEach(b => b.onclick = () => run(b, async () => {
      await post(`/issues/${b.dataset.res}/resolve`, { status: b.dataset.st, comment: $(`[data-c="${b.dataset.res}"]`).value }); toast("Сохранено", "ok"); draw();
    }));
  };
  await draw();
}

// ======================= Схемы и правила =======================
const HELP_SCHEMA = [["entities", "сущности области: имя → id, parent, columns (колонки таблицы и экспорта), fields, refs"], ["fields.<имя>.type", "string, number, int, bool или enum; для enum задаётся vocab"],
  ["fields.<имя>.vocab", "допустимые значения словаря; значение вне словаря отклоняется"], ["refs", "связи записей: поле → сущность, на которую оно ссылается"], ["result_entity", "сущность, которая хранит итоговые значения"],
  ["metrics", "показатели: unit (эталонная единица), conversions (множители пересчёта), kind"], ["compare.criteria", "критерии сопоставимости: path, mode equal или ratio_within, reason"],
  ["extraction.hints", "подсказки модели при извлечении"], ["eval.match_fields", "по каким полям сопоставляются записи при оценке качества"], ["scope", "границы области: что считается подходящей статьёй"]];
const HELP_RULES = [["id", "идентификатор правила"], ["type", "тип: formula_consistency, bound, inequality, pair_diff, champion_vs_mean, separate_values, metric_scale, k_required, link_integrity, no_merge"],
  ["severity", "error, warning или info"], ["description", "текст замечания для пользователя"], ["params", "параметры типа правила: пороги (min, max, tolerance_rel), метрики, группировка group_by"]];
async function config(box, p, current) {
  let d = dom(), kind = p.kind || "schema", cfg = null;
  const draw = async () => {
    cfg = await get("/config/" + d);
    if (!current()) return;
    const versions = (cfg.versions || []).filter(v => v.kind === kind).sort((a, b) => b.version - a.version);
    const active = versions.find(v => v.active);
    box.innerHTML = `<h1>Схемы и правила</h1><p class="lead">Предметная область описывается двумя YAML-файлами. Изменение сохраняется как новая версия и вступает в силу после активации; код системы не меняется.</p>
    <div class="filters"><label>Область <select id="cd">${options(state.domains.map(x => [x.domain, x.title || x.domain]), d)}</select></label>
      <div class="sub" style="margin:0;border:0;padding:0"><a href="#" data-k="schema" class="${kind === "schema" ? "on" : ""}">Схема</a><a href="#" data-k="rules" class="${kind === "rules" ? "on" : ""}">Правила</a></div><button class="btn" id="newdom">Новая область</button></div>
    <div class="cols cf"><div>
      <textarea class="code" id="yaml" spellcheck="false">${esc(kind === "schema" ? cfg.schema_yaml : cfg.rules_yaml)}</textarea>
      <div id="yerr"></div>
      <div class="sticky-bar"><input type="text" id="vc" placeholder="Комментарий к версии" style="flex:1;min-width:140px"><button class="btn" id="chk">Проверить YAML</button><button class="btn primary" id="sv">Сохранить как новую версию</button></div></div>
    <div><div class="card"><h3>Версии</h3>${versions.map(v => `<div class="row sp" style="margin-bottom:8px"><div><b>v${esc(v.version)}</b> ${v.active ? `<span class="tag ok">активна</span>` : ""}<div class="muted" style="font-size:12.5px">${esc(v.author || "")} · ${esc(dt(v.ts))}${v.comment ? `<br>${esc(v.comment)}` : ""}</div></div>${v.active ? "" : `<button class="btn sm" data-act="${v.version}">Активировать</button>`}</div>`).join("") || `<span class="muted">Версий нет</span>`}
    ${active ? "" : ""}</div>
    <div class="card"><h3>Что означают поля</h3><dl class="yamlhelp">${(kind === "schema" ? HELP_SCHEMA : HELP_RULES).map(([k, t]) => `<dt>${esc(k)}</dt><dd>${esc(t)}</dd>`).join("")}</dl></div></div></div>`;
    $("#cd").onchange = e => { d = e.target.value; draw(); };
    $$("[data-k]").forEach(a => a.onclick = e => { e.preventDefault(); kind = a.dataset.k; draw(); });
    const ta = $("#yaml");
    ta.addEventListener("keydown", e => { if (e.key === "Tab") { e.preventDefault(); const s = ta.selectionStart; ta.setRangeText("  ", s, ta.selectionEnd, "end"); } });
    const check = async () => { try { const y = await loadYaml(); y.load(ta.value); $("#yerr").innerHTML = noteBox("YAML корректен по синтаксису. Полная проверка схемы выполняется при сохранении.", "ok"); return true; } catch (e) { $("#yerr").innerHTML = noteBox("Ошибка YAML: " + esc(e.message), "badbox"); return false; } };
    $("#chk").onclick = check;
    $("#sv").onclick = e => run(e.target, async () => {
      if (!(await check())) return;
      try { const r = await put(`/config/${d}/${kind}`, { yaml: ta.value, comment: $("#vc").value }); toast("Сохранена версия " + (r.version ?? ""), "ok"); dropSchemaCache(); draw(); }
      catch (er) { $("#yerr").innerHTML = noteBox("Сервер не принял конфигурацию: " + esc(er.message), "badbox"); }
    });
    $$("[data-act]").forEach(b => b.onclick = () => run(b, async () => { await post(`/config/${d}/${kind}/activate`, { version: +b.dataset.act }); toast("Версия активирована", "ok"); dropSchemaCache(); draw(); }));
    $("#newdom").onclick = () => {
      const m = modal(`<h2>Новая область</h2><div class="note">Задайте имя и вставьте два YAML. Область появится в переключателе, код менять не нужно.</div><div class="form" style="grid-template-columns:1fr"><label>Имя области (латиницей)<input type="text" id="nd" placeholder="например, rag"></label><label>schema.yaml<textarea class="code" id="ns" style="min-height:200px"></textarea></label><label>rules.yaml<textarea class="code" id="nr" style="min-height:140px"></textarea></label><div id="nerr"></div><button class="btn primary" id="nm">Создать</button></div>`, { wide: true });
      $("#nm", m.el).onclick = e => run(e.target, async () => {
        try { await post("/config/domains", { domain: $("#nd", m.el).value.trim(), schema_yaml: $("#ns", m.el).value, rules_yaml: $("#nr", m.el).value }); m.close(); toast("Область создана", "ok"); state.domains = await get("/domains"); d = $("#nd") ? d : d; location.reload(); }
        catch (er) { $("#nerr", m.el).innerHTML = noteBox(esc(er.message), "badbox"); }
      });
    };
  };
  await draw();
}

// ======================= Качество =======================
function collectF1(node, path, out) {
  if (!node || typeof node !== "object") return;
  if (!Array.isArray(node) && ("f1" in node) && typeof node.f1 === "number") { out.push({ path, n: node }); return; }
  (Array.isArray(node) ? node.map((x, i) => [x && (x.name || x.field || x.entity) || i, x]) : Object.entries(node)).forEach(([k, v]) => collectF1(v, path.concat(k), out));
}
const runF1 = r => r && (r.f1 ?? (r.summary && r.summary.f1) ?? r.f1_micro ?? r.macro_f1);
function chart(runs) {
  const pts = runs.map(r => ({ f: runF1(r), reg: r.regression, t: r.ts || r.created })).filter(x => typeof x.f === "number");
  if (pts.length < 1) return `<div class="muted">Прогонов пока нет.</div>`;
  const W = 640, H = 200, L = 38, B = 24, T = 10, R = 10;
  const x = i => L + (pts.length === 1 ? (W - L - R) / 2 : i * (W - L - R) / (pts.length - 1)), y = v => T + (1 - v) * (H - T - B);
  return `<svg class="chart" viewBox="0 0 ${W} ${H}" role="img" aria-label="История F1 по прогонам">${[0, .25, .5, .75, 1].map(v => `<line class="grid" x1="${L}" x2="${W - R}" y1="${y(v)}" y2="${y(v)}"/><text x="4" y="${y(v) + 4}">${v.toFixed(2)}</text>`).join("")}
    <polyline class="ln" points="${pts.map((p, i) => `${x(i)},${y(p.f)}`).join(" ")}"/>${pts.map((p, i) => `<circle class="pt ${p.reg ? "reg" : ""}" cx="${x(i)}" cy="${y(p.f)}" r="4"><title>${esc(dt(p.t))}: F1 ${p.f.toFixed(3)}${p.reg ? " (регрессия)" : ""}</title></circle><text x="${x(i)}" y="${H - 6}" text-anchor="middle">${i + 1}</text>`).join("")}</svg>`;
}
async function quality(box, p, current) {
  let shown = null;
  const draw = async () => {
    const [runsR, pubs] = await Promise.all([get("/quality/runs", { domain: dom() }).catch(() => []), get("/publications", { domain: dom() })]);
    const runs = (Array.isArray(runsR) ? runsR : (runsR.items || [])).slice().sort((a, b) => (a.id || 0) - (b.id || 0));
    if (!shown && runs.length) shown = runs[runs.length - 1];
    if (!current()) return;
    const rows = []; if (shown) collectF1(shown.result || shown.details || shown.metrics || shown, [], rows);
    const f1 = runF1(shown), last = runs[runs.length - 1];
    box.innerHTML = `<h1>Качество</h1><p class="lead">Сравнение извлечённых записей с эталоном (gold): точность, полнота и F1 по сущностям и полям. Если F1 падает больше чем на 0,01 по тому же набору статей, прогон помечается как регрессия.</p>
    ${last && last.regression ? noteBox("<b class='flag'>Регрессия:</b> в последнем прогоне F1 упала более чем на 0,01 по сравнению с предыдущим.", "badbox") : ""}
    <div class="card"><h3>Эталон</h3><div class="form">
      <label>Папка с CSV-эталоном<input type="text" id="gdir" placeholder="/путь/к/папке с таблицами"></label>
      <div style="align-self:end"><button class="btn" id="gimp">Импортировать папку</button></div>
      <label>Разметить из текущих записей<select id="gpub">${options(pubs.map(x => [x.id, `${x.id} — ${(x.title || "").slice(0, 50)}`]))}</select></label>
      <div style="align-self:end"><button class="btn" id="gfrom">Скопировать записи в эталон</button></div></div><div id="gmsg"></div></div>
    <div class="card"><h3>Запуск оценки</h3><div class="row"><input type="text" id="note" placeholder="Заметка к прогону (что изменили)" style="flex:1;min-width:200px"><button class="btn primary" id="qrun">Запустить оценку</button></div><div id="qmsg"></div></div>
    <div class="cols"><div class="card"><h3>История прогонов</h3>${chart(runs)}<div class="muted" style="font-size:12.5px">Красные точки: регрессия. Нажмите на прогон в списке, чтобы открыть результат.</div>
      <div style="margin-top:8px">${runs.slice().reverse().slice(0, 8).map(r => `<div class="row sp"><a href="#" data-run="${r.id}">№${r.id}, ${esc(dt(r.ts || r.created))}</a><span>${typeof runF1(r) === "number" ? runF1(r).toFixed(3) : "—"} ${r.regression ? `<span class="tag bad">регрессия</span>` : ""}</span></div>`).join("")}</div></div>
    <div class="card"><h3>Сводка${shown ? ` прогона №${shown.id}` : ""}</h3>${shown ? `<div class="stats" style="grid-template-columns:1fr 1fr"><div class="stat"><b>${typeof f1 === "number" ? f1.toFixed(3) : "—"}</b><span>сводная F1</span></div><div class="stat"><b>${pct(shown.quote_valid_share ?? (shown.summary || {}).quote_valid_share)}</b><span>значений с валидной цитатой</span></div><div class="stat"><b>${pct(shown.coverage ?? (shown.summary || {}).coverage)}</b><span>покрытие корпуса</span></div></div>${shown.note ? `<div class="muted">${esc(shown.note)}</div>` : ""}` : `<span class="muted">Запустите первую оценку.</span>`}</div></div>
    ${rows.length ? `<h3>Точность, полнота и F1 по полям</h3><div class="tw"><table><thead><tr><th>Сущность</th><th>Поле</th><th class="num">Precision</th><th class="num">Recall</th><th class="num">F1</th><th class="num">Совпало</th><th class="num">Лишних</th><th class="num">Пропущено</th></tr></thead><tbody>${rows.map(r => { const n = r.n; const f = v => typeof v === "number" ? v.toFixed(3) : "—"; return `<tr><td>${esc(r.path[0] ?? "")}</td><td>${esc(r.path.slice(1).join(" / ") || "всего")}</td><td class="num">${f(n.precision ?? n.p)}</td><td class="num">${f(n.recall ?? n.r)}</td><td class="num"><b>${f(n.f1)}</b></td><td class="num">${n.tp ?? ""}</td><td class="num">${n.fp ?? ""}</td><td class="num">${n.fn ?? ""}</td></tr>`; }).join("")}</tbody></table></div>` : ""}`;
    const msg = (id, t, c) => { $(id).innerHTML = noteBox(esc(t), c); };
    $("#gimp").onclick = e => run(e.target, async () => { const r = await post("/gold/import", { domain: dom(), dir: $("#gdir").value.trim() }); msg("#gmsg", "Импортировано: " + JSON.stringify(r), "ok"); });
    $("#gfrom").onclick = e => run(e.target, async () => { const r = await post("/gold/from_publication", { pub_id: $("#gpub").value }); msg("#gmsg", "Записи скопированы в эталон. Исправьте их при необходимости. " + (r && r.count ? "Записей: " + r.count : ""), "ok"); });
    $("#qrun").onclick = e => run(e.target, async () => {
      const g = await get("/gold", { domain: dom() });
      const arr = Array.isArray(g) ? g : (g.items || g.rows || []);
      const ids = g.pub_ids || g.pubs || [...new Set(arr.map(x => x.pub_id))];
      if (!ids.length) return msg("#qmsg", "Эталон пуст: сначала импортируйте папку или разметьте статью из текущих записей.", "warnbox");
      $("#qmsg").innerHTML = `<span class="spin"></span>Считаю…`;
      shown = await post("/quality/run", { domain: dom(), pub_ids: ids, note: $("#note").value });
      toast(shown.regression ? "Готово: обнаружена регрессия" : "Оценка выполнена", shown.regression ? "bad" : "ok"); draw();
    });
    $$("[data-run]").forEach(a => a.onclick = async ev => { ev.preventDefault(); shown = await get("/quality/runs/" + a.dataset.run).catch(fail) || shown; draw(); });
  };
  await draw();
}

// ======================= Пользователи и аудит =======================
async function users(box, p, current) {
  const [us, au] = await Promise.all([get("/users"), get("/audit", { limit: 200 }).catch(() => [])]);
  if (!current()) return;
  const list = Array.isArray(us) ? us : (us.items || []);
  const aud = Array.isArray(au) ? au : (au.items || []);
  const uid = u => u.id ?? u.name;
  box.innerHTML = `<h1>Пользователи и аудит</h1>
  <div class="card"><h3>Пользователи</h3><div class="tw"><table><thead><tr><th>Имя</th><th>Роль</th><th>Создан</th><th></th></tr></thead><tbody>${list.map(u => `<tr><td>${esc(u.name)}</td><td><select data-role="${esc(uid(u))}">${options([["admin", "admin"], ["annotator", "annotator"], ["researcher", "researcher"]], u.role)}</select></td><td>${esc(dt(u.created || u.ts))}</td><td style="white-space:nowrap"><button class="btn sm" data-pw="${esc(uid(u))}">Сменить пароль</button> <button class="btn sm danger" data-rm="${esc(uid(u))}" data-n="${esc(u.name)}">Удалить</button></td></tr>`).join("")}</tbody></table></div>
  <form id="uf" class="form" style="margin-top:14px"><label>Имя<input type="text" name="name" required></label><label>Пароль<input type="password" name="password" required autocomplete="new-password"></label><label>Роль<select name="role"><option value="researcher">researcher</option><option value="annotator">annotator</option><option value="admin">admin</option></select></label><div style="align-self:end"><button class="btn primary">Создать пользователя</button></div></form></div>
  <div class="card"><h3>Журнал изменений</h3><div class="tw"><table><thead><tr><th>Время</th><th>Кто</th><th>Действие</th><th>Подробности</th></tr></thead><tbody>${aud.map(a => `<tr><td>${esc(dt(a.ts))}</td><td>${esc(a.user || a.author || "")}</td><td>${esc(a.action || a.event || "")}</td><td class="cut mono">${esc(typeof (a.details ?? a.detail) === "string" ? (a.details ?? a.detail) : JSON.stringify(a.details ?? a.detail ?? a.target ?? ""))}</td></tr>`).join("") || `<tr><td colspan="4" class="muted">Записей нет</td></tr>`}</tbody></table></div></div>`;
  const redraw = () => users(box, p, current);
  $("#uf").onsubmit = async e => { e.preventDefault(); const f = new FormData(e.target); await run($("button", e.target), async () => { await post("/users", { name: f.get("name"), password: f.get("password"), role: f.get("role") }); toast("Пользователь создан", "ok"); redraw(); }); };
  $$("[data-role]").forEach(s => s.onchange = () => run(s, async () => { await patch("/users/" + encodeURIComponent(s.dataset.role), { role: s.value }); toast("Роль изменена", "ok"); }));
  $$("[data-pw]").forEach(b => b.onclick = () => { const pw = prompt("Новый пароль"); if (pw) run(b, async () => { await patch("/users/" + encodeURIComponent(b.dataset.pw), { password: pw }); toast("Пароль изменён", "ok"); }); });
  $$("[data-rm]").forEach(b => b.onclick = () => { if (confirm(`Удалить пользователя ${b.dataset.n}?`)) run(b, async () => { await del("/users/" + encodeURIComponent(b.dataset.rm)); toast("Удалён", "ok"); redraw(); }); });
}

// ======================= Настройки модели =======================
async function settings(box, p, current) {
  const s = await get("/settings");
  if (!current()) return;
  const presets = s.presets || {};
  box.innerHTML = `<h1>Модель и ключ</h1><p class="lead">Какая модель извлекает данные. Ключ доступа записывается только в одну сторону: после сохранения он не показывается.</p>
  <div class="card"><form id="sf" class="form">
    <label>Провайдер<select name="provider">${options([["deepseek", "DeepSeek"], ["lmstudio", "LM Studio (локально)"], ["ollama", "Ollama (локально)"], ["orcarouter", "OrcaRouter (бесплатные модели)"], ["gemini", "Google Gemini (AI Studio)"], ["groq", "Groq"], ["custom", "Другой, совместимый с OpenAI"]], s.provider)}</select></label>
    <label>Адрес API<input type="url" name="base_url" value="${esc(s.base_url || "")}" placeholder="https://..."></label>
    <label>Модель<input type="text" name="model" value="${esc(s.model || "")}"></label>
    <label>Воркеров (параллельных статей)<input type="number" min="1" max="32" name="workers" value="${esc(s.workers || 2)}"></label>
    <label class="wide">Ключ API <span id="key-status"></span><input type="password" name="api_key" autocomplete="off"></label>
    <div class="wide row"><button class="btn primary" type="submit">Сохранить</button><button class="btn" type="button" id="tst">Проверить подключение</button></div></form><div id="smsg"></div></div>`;
  const sel = $("[name=provider]"), url = $("[name=base_url]"), model = $("[name=model]");
  const workers = $("[name=workers]"), key = $("[name=api_key]"), keyStatus = $("#key-status");
  function syncProviderFields() {
    const local = sel.value === "ollama";
    url.readOnly = local || sel.value === "groq";
    workers.disabled = local;
    if (local) workers.value = 1;
    key.disabled = local;
    const keySet = sel.value === s.provider && s.key_set;
    keyStatus.className = keySet ? "tag ok" : "tag cited";
    keyStatus.textContent = local ? "не требуется" : sel.value === s.provider ? (keySet ? "ключ задан" : "ключ не задан") : "";
    key.placeholder = local ? "" : keySet ? "оставьте пустым, чтобы не менять" : "ключ выбранного провайдера";
  }
  sel.onchange = () => {
    url.value = presets[sel.value]?.base_url || "";
    model.value = presets[sel.value]?.model || "";
    workers.value = s.workers || 2;
    key.value = "";
    syncProviderFields();
  };
  syncProviderFields();
  $("#sf").onsubmit = async e => {
    e.preventDefault(); const f = Object.fromEntries(new FormData(e.target)); if (!f.api_key) delete f.api_key; if (f.workers) f.workers = +f.workers;
    await run($("button[type=submit]", e.target), async () => { await post("/settings", f); toast("Настройки сохранены", "ok"); settings(box, p, current); });
  };
  $("#tst").onclick = e => run(e.target, async () => {
    $("#smsg").innerHTML = noteBox(`<span class="spin"></span>Проверяю подключение…`);
    try { const r = await post("/settings/test"); $("#smsg").innerHTML = noteBox(esc(r.message || (r.ok === false ? "Не удалось подключиться" : "Подключение работает")) + (r.model ? `, модель: ${esc(r.model)}` : ""), r.ok === false ? "badbox" : "ok"); }
    catch (er) { $("#smsg").innerHTML = noteBox(esc(er.message), "badbox"); }
  });
}
