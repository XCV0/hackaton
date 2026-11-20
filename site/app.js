import {
  $, $$, esc, fmt, pct, dt, state, LS, role, isStaff, isAdmin, get, post, setDomain, loadMe, login, logout, onAuthNeeded,
  toast, fail, modal, badge, sevBadge, originRu, srcLink, srcUrl, pager, options, basket, download, renderAny, loadSchema, qs, num,
} from "./api.js";
import { adminRoute, ADMIN_TABS } from "./admin.js";

const app = $("#app");
const ENT_RU = { publications: "Публикации", results: "Результаты", devices: "Приборы", materials: "Материалы", layers: "Слои", measurements: "Измерения", configurations: "Конфигурации" };
const entRu = e => ENT_RU[e] || e;

// ---------- тема ----------
function applyTheme(t) { if (t) document.documentElement.setAttribute("data-theme", t); else document.documentElement.removeAttribute("data-theme"); }
applyTheme(LS.get("theme"));
$("#theme").onclick = () => {
  const cur = document.documentElement.getAttribute("data-theme") || (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
  const nx = cur === "dark" ? "light" : "dark"; LS.set("theme", nx); applyTheme(nx);
};

// ---------- вход ----------
function showLogin(msg) {
  if ($(".modal-bg #loginForm")) return;
  const m = modal(`<h2>Вход</h2>${msg ? `<div class="note warnbox">${esc(msg)}</div>` : ""}
    <form id="loginForm" class="form" style="grid-template-columns:1fr">
      <label>Имя<input type="text" name="name" autocomplete="username" required></label>
      <label>Пароль<input type="password" name="password" autocomplete="current-password" required></label>
      <div class="note" id="loginErr" hidden></div>
      <button class="btn primary" type="submit">Войти</button>
    </form>`);
  $("input", m.el).focus();
  $("#loginForm", m.el).onsubmit = async e => {
    e.preventDefault();
    const f = new FormData(e.target);
    try { await login(f.get("name"), f.get("password")); m.close(); renderShell(); route(); toast("Вход выполнен", "ok"); }
    catch (er) { const b = $("#loginErr", m.el); b.hidden = false; b.className = "note badbox"; b.textContent = er.message; }
  };
}
onAuthNeeded(msg => { renderShell(); showLogin(msg); });

// ---------- оболочка ----------
const NAV = [
  ["", "Обзор"], ["publications", "Публикации"], ["records", "Записи"], ["compare", "Сравнение"],
  ["issues", "Замечания"], ["passport", "Паспорт данных"], ["export", "Экспорт"],
];
function renderShell() {
  const items = NAV.map(([r, t]) => `<a href="#/${r}" data-r="${r}">${t}${r === "compare" && basket.list().length ? ` (${basket.list().length})` : ""}</a>`);
  if (isStaff()) items.push(`<a href="#/admin/${isAdmin() ? "corpus" : "editor"}" data-r="admin">Администрирование</a>`);
  $("#nav").innerHTML = items.join("");
  const ds = state.domains.length ? state.domains : [{ domain: state.domain, title: state.domain }];
  $("#domainSel").innerHTML = options(ds.map(d => [d.domain, d.title || d.domain]), state.domain);
  const u = state.user;
  $("#who").innerHTML = u ? `<span>${esc(u.name)} <span class="tag">${esc(u.role)}</span></span><button class="btn sm" id="out">Выйти</button>`
    : `<button class="btn sm" id="in">Войти</button>`;
  const o = $("#out"); if (o) o.onclick = () => { logout(); renderShell(); route(); };
  const i = $("#in"); if (i) i.onclick = () => showLogin();
  markNav();
}
function markNav() {
  const r = location.hash.replace(/^#\/?/, "").split(/[/?]/)[0];
  $$("#nav a").forEach(a => a.classList.toggle("on", a.dataset.r === r));
}
$("#domainSel").onchange = e => { setDomain(e.target.value); renderShell(); route(); };
window.addEventListener("basket", renderShell);

// ---------- маршрутизация ----------
function parseHash() {
  const h = location.hash.replace(/^#\/?/, "");
  const [p, q] = h.split("?");
  return { parts: p.split("/").filter(Boolean).map(decodeURIComponent), params: Object.fromEntries(new URLSearchParams(q || "")) };
}
export const go = (path, params) => { location.hash = "#/" + path + qs(params); };
let token = 0;
async function route() {
  markNav();
  const { parts, params } = parseHash();
  const my = ++token;
  window.scrollTo(0, 0);
  app.innerHTML = `<div class="empty"><span class="spin"></span>Загрузка…</div>`;
  try {
    let html;
    const [a, b] = parts;
    if (!a) html = await dashboard();
    else if (a === "publications") html = b ? await publication(b) : await publications(params);
    else if (a === "records") html = b ? await recordCard(b) : await records(params);
    else if (a === "compare") html = await compare(params);
    else if (a === "issues") html = await issues(params);
    else if (a === "passport") html = await passport();
    else if (a === "export") html = await exportPage();
    else if (a === "admin") {
      if (!isStaff()) { app.innerHTML = `<div class="empty">Раздел доступен администратору и разметчику. <a href="#/" id="lg">Войти</a></div>`; if (!state.token) showLogin(); return; }
      html = await adminRoute(parts.slice(1), params, app, () => my === token);
    }
    else html = `<div class="empty">Страница не найдена</div>`;
    if (my !== token) return;
    if (typeof html === "string") app.innerHTML = html;
    if (app._after) { const f = app._after; app._after = null; f(); }
  } catch (e) {
    if (my !== token) return;
    console.error(e);
    app.innerHTML = `<div class="card"><h2>Не удалось загрузить страницу</h2><p>${esc(e.message)}</p><button class="btn" onclick="location.reload()">Повторить</button></div>`;
  }
}
const after = f => { app._after = f; };
window.addEventListener("hashchange", route);

// ---------- дашборд ----------
async function dashboard() {
  const s = await get("/stats", { domain: state.domain });
  const rules = Object.entries(s.issues_by_rule || {}).sort((a, b) => b[1].open - a[1].open || b[1].total - a[1].total);
  const max = Math.max(1, ...rules.map(([, v]) => v.total));
  const st = s.by_status || {};
  const le = s.last_eval;
  const f1 = le && (le.f1 ?? le.summary?.f1 ?? le.f1_micro);
  const dom = state.domains.find(d => d.domain === state.domain);
  after(() => {});
  return `<h1>${esc(dom ? dom.title : "Обзор")}</h1>
  <p class="lead">Сводка по корпусу: сколько публикаций прошло все стадии, какая доля значений подтверждена и сколько замечаний осталось.</p>
  <div class="stats">
    <div class="stat"><b>${s.pubs ?? 0}</b><span>публикаций</span></div>
    <div class="stat"><b>${pct(s.coverage)}</b><span>покрытие корпуса (прошли все стадии)</span></div>
    <div class="stat"><b>${s.records ?? 0}</b><span>записей, из них отклонено ${s.rejected ?? 0}</span></div>
    <div class="stat"><b>${pct(s.verified_share)}</b><span>доля подтверждённых</span></div>
    <div class="stat"><b>${pct(s.quote_valid_share)}</b><span>доля значений с найденной цитатой</span></div>
    <div class="stat"><b>${s.ocr_queue ?? 0}</b><span>в очереди на OCR</span></div>
  </div>
  <div class="cols">
    <div class="card"><h3>Публикации по статусу</h3>
      <div class="bars">${Object.entries(st).map(([k, v]) => `<div class="bar" style="grid-template-columns:130px 1fr 40px"><a href="#/publications?status=${esc(k)}">${esc(badgeText(k))}</a><i style="width:${Math.max(2, 100 * v / Math.max(1, s.pubs))}%"></i><span>${v}</span></div>`).join("") || `<div class="muted">Нет данных</div>`}</div></div>
    <div class="card"><h3>Замечания по правилам</h3>
      <div class="bars">${rules.map(([k, v]) => `<div class="bar" style="grid-template-columns:150px 1fr 70px"><a href="#/issues?rule=${esc(k)}" class="cut">${esc(k)}</a><i style="width:${Math.max(2, 100 * v.total / max)}%"></i><span>${v.open} / ${v.total}</span></div>`).join("") || `<div class="muted">Замечаний нет</div>`}</div>
      <div class="muted" style="margin-top:8px;font-size:13px">открыто / всего</div></div>
  </div>
  <div class="card"><h3>Последний прогон проверки качества</h3>${le
    ? `<div class="row"><span><b style="font:700 28px var(--head)">${f1 !== undefined ? (+f1).toFixed(3) : "—"}</b> <span class="muted">сводная F1</span></span>
        <span class="muted">${esc(dt(le.ts || le.created))}</span>${le.regression ? `<span class="tag bad">регрессия</span>` : ""}
        ${le.note ? `<span class="muted">${esc(le.note)}</span>` : ""}</div>`
    : `<div class="muted">Прогонов ещё не было. Эталон и запуск — в разделе «Администрирование → Качество».</div>`}</div>`;
}
const badgeText = k => (badge(k).replace(/<[^>]+>/g, ""));

// ---------- публикации ----------
async function publications(p) {
  const list = await get("/publications", { domain: state.domain, status: p.status, q: p.q, split: p.split });
  const statuses = ["registered", "processing", "needs_ocr", "verified", "flagged", "error", "excluded"];
  const splits = [...new Set(list.map(x => x.split).filter(Boolean))];
  after(() => {
    const f = () => go("publications", { q: $("#fq").value.trim(), status: $("#fs").value, split: $("#fp").value });
    $("#fq").onkeydown = e => { if (e.key === "Enter") f(); };
    $("#fs").onchange = f; $("#fp").onchange = f; $("#fgo").onclick = f;
  });
  return `<h1>Публикации</h1><p class="lead">Каталог загруженных статей и состояние их обработки.</p>
  <div class="filters"><input type="search" id="fq" placeholder="Название, DOI, автор" value="${esc(p.q || "")}">
    <select id="fs">${options(statuses.map(s => [s, badgeText(s)]), p.status, "Любой статус")}</select>
    <select id="fp">${options(splits, p.split, "Любой набор")}</select>
    <button class="btn" id="fgo">Найти</button><span class="count">${list.length} шт.</span></div>
  <div class="tw"><table><thead><tr><th>ID</th><th>Название</th><th>Год</th><th>Статус</th><th class="num">Стр.</th><th class="num">Записей</th><th class="num">Замечаний</th><th>Набор</th></tr></thead><tbody>
  ${list.map(x => `<tr class="click" data-href="#/publications/${esc(x.id)}"><td class="mono">${esc(x.id)}</td><td>${esc(x.title || "—")}${x.message ? `<div class="warn">${esc(x.message)}</div>` : ""}</td>
    <td>${esc(x.year || "")}</td><td>${badge(x.status)}</td><td class="num">${x.pages ?? ""}</td><td class="num">${x.n_records ?? 0}</td><td class="num">${x.n_issues_open ?? 0}</td><td>${esc(x.split || "")}</td></tr>`).join("")
    || `<tr><td colspan="8" class="empty">Ничего не найдено</td></tr>`}</tbody></table></div>`;
}
document.addEventListener("click", e => {
  const tr = e.target.closest("tr[data-href]");
  if (tr && !e.target.closest("a,button,input,select,label")) location.hash = tr.dataset.href;
});

async function publication(id) {
  const d = await get("/publications/" + encodeURIComponent(id), { include_rejected: 1 });
  const p = d.publication, ents = d.records || {};
  const stages = d.stages || [];
  after(() => bindBasket());
  return `<div class="crumb"><a href="#/publications">Публикации</a> / ${esc(p.id)}</div>
  <h1 style="font-size:24px">${esc(p.title || p.id)}</h1>
  <div class="actions">${badge(p.status)}${srcLink(p.id, 1, "", "Открыть PDF")}</div>
  <div class="cols"><div class="card"><dl class="meta"><dt>Год</dt><dd>${esc(p.year || "—")}</dd><dt>DOI</dt><dd>${esc(p.doi || "—")}</dd><dt>Страниц</dt><dd>${esc(p.pages ?? "—")}</dd>
    <dt>Набор</dt><dd>${esc(p.split || "—")}</dd><dt>В области</dt><dd>${p.in_scope === false || p.in_scope === 0 ? "нет" : "да"}</dd>${p.message ? `<dt>Сообщение</dt><dd>${esc(p.message)}</dd>` : ""}</dl></div>
  <div class="card"><h3>Стадии обработки</h3>${stages.length ? `<div class="row">${stages.map(s => `<span class="tag ${s.status === "done" ? "ok" : s.status === "error" ? "bad" : ""}" title="${esc(s.message || "")}">${esc(s.stage)}: ${esc(s.status)}</span>`).join("")}</div>` : `<span class="muted">нет данных</span>`}</div></div>
  ${(d.issues || []).length ? `<div class="card"><h3>Замечания (${d.issues.length})</h3>${d.issues.map(issueBlock).join("")}</div>` : ""}
  ${Object.entries(ents).map(([en, rows]) => `<h3 style="margin-top:20px">${esc(entRu(en))} <span class="muted">(${rows.length})</span></h3>${recTable(rows)}`).join("") || `<div class="empty">Записей нет</div>`}`;
}
const issueBlock = i => `<div class="issue">${sevBadge(i.severity)} <span class="tag">${esc(i.rule_id)}</span> ${badge(i.status)}
  <div>${esc(i.description)}</div>${i.comment ? `<div class="muted">Комментарий: ${esc(i.comment)}</div>` : ""}
  <div class="row" style="margin-top:6px">${(i.record_ids || []).map(r => `<a class="tag ok" href="#" data-rid="${esc(r)}" data-pub="${esc(i.pub_id)}">${esc(r)}</a>`).join("")}${i.source_page ? srcLink(i.pub_id, i.source_page, i.source_fragment, "Источник, стр. " + i.source_page).replace('class="btn"', 'class="btn sm"') : ""}</div></div>`;

// переход от rid к id записи
const ridCache = {};
async function ridToId(pub, rid) {
  if (!ridCache[pub]) { const r = await get("/records", { pub, limit: 2000, dups: 1 }); ridCache[pub] = r.items || []; }
  const f = ridCache[pub].filter(x => x.rid === rid);
  return (f.find(x => x.entity === (state.resultEntity || "results")) || f[0] || {}).id;
}
document.addEventListener("click", async e => {
  const a = e.target.closest("a[data-rid]");
  if (!a) return;
  e.preventDefault();
  try { const id = await ridToId(a.dataset.pub, a.dataset.rid); if (id) location.hash = "#/records/" + id; else toast("Запись не найдена в списке (возможно, отклонена)", "bad"); } catch (er) { fail(er); }
});

// ---------- записи ----------
function valueOf(r) {
  const d = r.data || {};
  if (d.normalized_value !== undefined && d.normalized_value !== "") return `${fmt(d.normalized_value)} ${esc(d.normalized_unit || "")}`;
  if (d.metric_value) return `${fmt(d.metric_value)} ${esc(d.metric_unit || "")}`;
  if (d.raw_value !== undefined && d.raw_value !== "") return `${esc(d.raw_value)} ${esc(d.raw_unit || "")}`;
  return `<span class="muted">—</span>`;
}
function titleOf(r) {
  const d = r.data || {};
  const k = ["metric_name", "device_label", "material_name_raw", "layer_material", "measurement_type", "name", "title"].find(x => d[x]);
  return k ? esc(d[k]) : esc(r.rid);
}
function recTable(rows, { check = true } = {}) {
  if (!rows.length) return `<div class="empty">Нет записей</div>`;
  return `<div class="tw"><table><thead><tr>${check ? `<th class="chk-col" title="В сравнение"></th>` : ""}<th>ID</th><th>Сущность</th><th>Показатель</th><th class="num">Значение</th><th>Происхождение</th><th>Статус</th><th class="num">Стр.</th></tr></thead><tbody>
  ${rows.map(r => `<tr class="click" data-href="#/records/${r.id}">${check ? `<td><input type="checkbox" data-bk="${r.id}" ${basket.has(r.id) ? "checked" : ""} aria-label="В сравнение"></td>` : ""}
    <td class="mono">${esc(r.pub_id)} · ${esc(r.rid)}</td><td>${esc(entRu(r.entity))}</td><td>${titleOf(r)}${r.data && r.data.aggregation ? ` <span class="muted">(${esc(r.data.aggregation)})</span>` : ""}</td>
    <td class="num">${valueOf(r)}</td><td>${originRu(r.data && r.data.result_origin)}</td><td>${badge(r.status)}${r.quote_verified === 0 ? ` <span class="tag bad">цитата не найдена</span>` : ""}</td><td class="num">${r.source_page ?? ""}</td></tr>`).join("")}</tbody></table></div>`;
}
function bindBasket() {
  $$("input[data-bk]").forEach(c => c.onchange = () => basket.toggle(+c.dataset.bk));
}
async function records(p) {
  const schema = await loadSchema(state.domain).catch(() => ({ entities: {} }));
  state.resultEntity = schema.result_entity || state.resultEntity;
  const limit = 25, offset = +p.offset || 0;
  const searching = !!p.q;
  let body, total = 0;
  if (searching) {
    const r = await get("/search", { q: p.q, domain: state.domain });
    const items = r.items || []; total = items.length;
    body = `<div class="tw"><table><thead><tr><th>Тип</th><th>Где</th><th>Фрагмент</th></tr></thead><tbody>${items.map(i => {
      const href = i.kind === "record" ? `#/records/${i.id}` : `#/publications/${i.pub_id}`;
      return `<tr class="click" data-href="${href}"><td>${i.kind === "record" ? "Запись" : "Публикация"}</td><td class="mono">${esc(i.pub_id)}${i.rid ? " · " + esc(i.rid) : ""}${i.entity ? ` <span class="muted">${esc(entRu(i.entity))}</span>` : ""}</td><td>${i.snippet ? i.snippet.replace(/<(?!\/?mark\b)[^>]*>/g, "") : ""}</td></tr>`;
    }).join("") || `<tr><td colspan="3" class="empty">Ничего не найдено</td></tr>`}</tbody></table></div>`;
  } else {
    const r = await get("/records", { domain: state.domain, entity: p.entity, metric: p.metric, origin: p.origin, status: p.status, pub: p.pub, limit, offset });
    total = r.total || 0;
    body = recTable(r.items || []) + pager(total, limit, offset);
  }
  const ents = Object.keys(schema.entities || {}).filter(e => e !== "publications");
  const metricField = schema.entities && Object.values(schema.entities).map(e => e.fields && e.fields.metric_name).find(Boolean);
  const metrics = (metricField && metricField.vocab) || Object.keys(schema.metrics || {});
  after(() => {
    const f = () => go("records", { q: $("#fq").value.trim(), entity: $("#fe") && $("#fe").value, metric: $("#fm") && $("#fm").value, origin: $("#fo") && $("#fo").value, status: $("#fs") && $("#fs").value, pub: p.pub });
    $("#fq").onkeydown = e => { if (e.key === "Enter") f(); };
    $("#fgo").onclick = f;
    ["fe", "fm", "fo", "fs"].forEach(i => { const el = $("#" + i); if (el) el.onchange = f; });
    $$("[data-pg]").forEach(b => b.onclick = () => go("records", { ...p, offset: b.dataset.pg }));
    bindBasket();
  });
  return `<h1>Записи</h1><p class="lead">Извлечённые значения с привязкой к тексту статьи. Отметьте флажками записи, чтобы сравнить их.</p>
  <div class="filters"><input type="search" id="fq" placeholder="Полнотекстовый поиск по записям и статьям" value="${esc(p.q || "")}">
  ${searching ? "" : `<select id="fe">${options(ents.map(e => [e, entRu(e)]), p.entity, "Любая сущность")}</select>
  <select id="fm">${options(metrics, p.metric, "Любой показатель")}</select>
  <select id="fo">${options([["primary", "собственный"], ["cited", "из литературы"]], p.origin, "Любое происхождение")}</select>
  <select id="fs">${options(["extracted", "verified", "flagged", "manual"].map(s => [s, badgeText(s)]), p.status, "Любой статус")}</select>`}
  <button class="btn" id="fgo">Найти</button>${p.pub ? `<span class="tag ok">публикация ${esc(p.pub)} <a href="#/records">сбросить</a></span>` : ""}<span class="count">${total} шт.</span></div>${body}`;
}

// ---------- карточка записи ----------
async function recordCard(id) {
  const d = await get("/records/" + id);
  const r = d.record, data = r.data || {}, ctx = d.context || {};
  const unit = data.normalized_unit || data.metric_unit || data.raw_unit || "";
  const hasVal = ["normalized_value", "metric_value", "raw_value"].some(k => data[k] !== undefined && data[k] !== "");
  const skip = new Set(["publication_id", "source_fragment"]);
  after(() => {
    const b = $("#bk"); if (b) b.onclick = () => { const on = basket.toggle(r.id); b.textContent = on ? "Убрать из сравнения" : "Добавить к сравнению"; };
    $$("[data-act]").forEach(x => x.onclick = () => recAct(r, x.dataset.act));
  });
  return `<div class="crumb"><a href="#/records">Записи</a> / <a href="#/publications/${esc(r.pub_id)}">${esc(r.pub_id)}</a> / ${esc(r.rid)}</div>
  <div class="devhead"><h1 style="font-size:26px">${titleOf(r)} <span class="muted" style="font-size:16px">${esc(entRu(r.entity))}</span></h1><div class="row">${badge(r.status)}${r.quote_verified === 1 ? `<span class="tag ok">цитата найдена</span>` : r.quote_verified === 0 ? `<span class="tag bad">цитата не найдена</span>` : ""}</div></div>
  <div class="actions"><button class="btn" id="bk">${basket.has(r.id) ? "Убрать из сравнения" : "Добавить к сравнению"}</button>
    ${r.source_page ? srcLink(r.pub_id, r.source_page, r.source_fragment) : ""}
    ${isStaff() ? `<a class="btn" href="#/admin/editor?rec=${r.id}">Править</a>
      ${r.status !== "verified" && r.status !== "rejected" ? `<button class="btn" data-act="confirm">Подтвердить</button>` : ""}
      ${r.status !== "rejected" ? `<button class="btn danger" data-act="reject">Отклонить</button>` : `<button class="btn" data-act="restore">Восстановить</button>`}` : ""}</div>
  ${hasVal ? `<div class="card"><div class="big">${valueOf(r)}</div>${data.raw_value !== undefined && data.raw_value !== "" ? `<div class="muted">как в статье: ${esc(data.raw_value)} ${esc(data.raw_unit || "")}</div>` : ""}
    ${data.spread_value ? `<div class="muted">разброс: ${esc(data.spread_value)}${data.n_devices ? `, приборов: ${esc(data.n_devices)}` : ""}</div>` : ""}</div>` : ""}
  ${r.source_fragment ? `<div class="card"><h3>Цитата из статьи${r.source_page ? `, стр. ${r.source_page}` : ""}</h3><div class="quote">${esc(r.source_fragment)}</div></div>` : ""}
  <div class="cols"><div class="card"><h3>Поля записи</h3><dl class="kv">${Object.entries(data).filter(([k, v]) => v !== "" && v !== null && !skip.has(k)).map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join("")}</dl></div>
  <div class="card"><h3>Связанный контекст</h3>${Object.keys(ctx).length ? Object.entries(ctx).map(([en, rows]) => `<div style="margin-bottom:12px"><b>${esc(entRu(en))}</b>${Object.entries(rows).map(([rid, dd]) => `<div class="quote" style="margin:6px 0"><span class="mono">${esc(rid)}</span><br>${Object.entries(dd).filter(([k, v]) => v !== "" && !["publication_id", en.replace(/s$/, "") + "_id"].includes(k)).slice(0, 12).map(([k, v]) => `<span class="muted">${esc(k)}:</span> ${esc(v)}`).join(" · ")}</div>`).join("")}</div>`).join("") : `<span class="muted">Связанных записей нет</span>`}</div></div>
  ${(d.issues || []).length ? `<div class="card"><h3>Замечания</h3>${d.issues.map(issueBlock).join("")}</div>` : ""}
  <div class="card"><h3>История изменений</h3>${(d.history || []).length ? `<ul class="hist">${d.history.map(h => `<li><b>${esc(h.author || h.user || "")}</b> · ${esc(dt(h.ts))}${h.comment ? ` — ${esc(h.comment)}` : ""}<br><span class="muted mono">${esc(typeof h.changes === "string" ? h.changes : JSON.stringify(h.changes ?? h.diff ?? h.after ?? ""))}</span></li>`).join("")}</ul>` : `<span class="muted">Изменений не было</span>`}</div>`;
}
async function recAct(r, action) {
  const comment = action === "reject" ? prompt("Причина отклонения") : "";
  if (action === "reject" && comment === null) return;
  try { await post(`/records/${r.id}/action`, { action, comment: comment || "" }); toast("Готово", "ok"); route(); } catch (e) { fail(e); }
}

// ---------- сравнение ----------
async function compare() {
  const ids = basket.list();
  if (!ids.length) return `<h1>Сравнение</h1><p class="lead">Корзина пуста. Отметьте записи флажками на странице <a href="#/records">«Записи»</a>, затем вернитесь сюда. Система сравнит их только если у них совпадают условия, а несопоставимые пары покажет с причиной.</p>`;
  const res = await post("/compare", { domain: state.domain, record_ids: ids });
  const rows = res.rows || [];
  const crit = [...new Set(rows.flatMap(r => Object.keys(r.resolved || {})))];
  const byId = Object.fromEntries(rows.map(r => [r.record.id, r.record]));
  const lab = id => { const r = byId[id]; return r ? `${esc(r.pub_id)} · ${esc(r.rid)}` : "#" + id; };
  after(() => {
    $$("[data-rm]").forEach(b => b.onclick = () => { basket.toggle(+b.dataset.rm); route(); });
    $("#clr").onclick = () => { basket.clear(); route(); };
    const c = $("#cand"); if (c) c.onclick = async () => {
      try { const r = await get("/compare/candidates", { record_id: ids[0] }); const list = Array.isArray(r) ? r : (r.ids || r.items || []); const a = basket.list(); list.forEach(x => { const v = typeof x === "object" ? x.id : x; if (!a.includes(v)) a.push(v); }); basket.save(a); toast("Добавлено сопоставимых: " + list.length, "ok"); route(); } catch (e) { fail(e); }
    };
  });
  return `<h1>Сравнение</h1>
  <div class="actions"><button class="btn" id="cand">Добавить сопоставимые с первой записью</button><button class="btn" id="clr">Очистить корзину</button>
    <span class="tag ${res.comparable ? "ok" : "bad"}">${res.comparable ? "записи сопоставимы" : "есть несопоставимые пары"}</span></div>
  ${(res.blocked || []).length ? `<div class="blocked"><b>Заблокировано: сравнение этих записей некорректно</b>${res.blocked.map(b => `<div style="margin-top:8px">${lab(b.a)} и ${lab(b.b)}<ul>${(b.reasons || []).map(x => `<li>${esc(x.reason)}${x.criterion ? ` <span class="muted">(${esc(x.criterion)}: «${esc(x.a_value ?? "не указано")}» и «${esc(x.b_value ?? "не указано")}»)</span>` : ""}</li>`).join("")}</ul></div>`).join("")}</div>` : ""}
  <div class="tw"><table><thead><tr><th></th><th>Запись</th><th>Показатель</th><th class="num">Значение</th>${crit.map(c => `<th>${esc(c)}</th>`).join("")}</tr></thead><tbody>
  ${rows.map(r => `<tr><td><button class="btn sm" data-rm="${r.record.id}" title="Убрать">×</button></td><td><a href="#/records/${r.record.id}" class="mono">${lab(r.record.id)}</a></td><td>${titleOf(r.record)}</td><td class="num">${valueOf(r.record)}</td>
   ${crit.map(c => { const v = (r.resolved || {})[c]; return `<td>${v === undefined || v === null || v === "" ? `<span class="muted">не указано в статье</span>` : esc(v)}</td>`; }).join("")}</tr>`).join("")}</tbody></table></div>
  ${(res.groups || []).length ? `<div class="card" style="margin-top:14px"><h3>Группы сопоставимых записей</h3>${res.groups.map(g => `<div>${g.map(lab).join(", ")}</div>`).join("")}</div>` : ""}`;
}

// ---------- замечания ----------
async function issues(p) {
  const list = await get("/issues", { domain: state.domain, pub: p.pub, status: p.status, rule: p.rule, severity: p.severity });
  const all = p.rule ? list : list;
  const ruleIds = [...new Set(list.map(i => i.rule_id).concat(p.rule ? [p.rule] : []))].sort();
  after(() => {
    const f = () => go("issues", { rule: $("#fr").value, status: $("#fs").value, severity: $("#fv").value, pub: p.pub });
    ["fr", "fs", "fv"].forEach(i => $("#" + i).onchange = f);
  });
  return `<h1>Замечания</h1><p class="lead">Расхождения, найденные правилами проверки, моделью и сверкой цитат. Решение по каждому принимает разметчик.</p>
  <div class="filters"><select id="fr">${options(ruleIds, p.rule, "Любое правило")}</select>
  <select id="fs">${options(["open", "accepted", "fixed", "kept"].map(s => [s, badgeText(s)]), p.status, "Любой статус")}</select>
  <select id="fv">${options([["error", "Ошибка"], ["warning", "Предупреждение"], ["info", "Сведения"]], p.severity, "Любая важность")}</select>
  ${p.pub ? `<span class="tag ok">публикация ${esc(p.pub)} <a href="#/issues">сбросить</a></span>` : ""}<span class="count">${all.length} шт.</span></div>
  <div class="tw"><table><thead><tr><th>Важность</th><th>Правило</th><th>Описание</th><th>Записи</th><th>Статус</th><th>Источник</th></tr></thead><tbody>
  ${all.map(i => `<tr><td>${sevBadge(i.severity)}</td><td class="mono">${esc(i.rule_id)}<div class="muted">${esc(originRu(i.origin))}</div></td><td>${esc(i.description)}${i.comment ? `<div class="muted">${esc(i.comment)}</div>` : ""}</td>
  <td>${(i.record_ids || []).map(r => `<a class="tag ok" href="#" data-rid="${esc(r)}" data-pub="${esc(i.pub_id)}">${esc(i.pub_id)}·${esc(r)}</a>`).join(" ")}</td><td>${badge(i.status)}</td>
  <td>${i.source_page ? `<a target="_blank" rel="noopener" href="${esc(srcUrl(i.pub_id, i.source_page, i.source_fragment))}">стр. ${i.source_page}</a>` : ""}</td></tr>`).join("") || `<tr><td colspan="6" class="empty">Замечаний нет</td></tr>`}</tbody></table></div>`;
}

// ---------- паспорт ----------
async function passport() {
  const p = await get("/passport/" + state.domain);
  const ents = p.entities || {};
  const entList = Array.isArray(ents) ? ents.map(e => [e.name || e.entity, e]) : Object.entries(ents);
  const known = new Set(["domain", "title", "version", "scope", "entities", "metrics", "units", "rules", "thresholds", "vocabularies", "vocab", "description", "result_entity"]);
  const rest = Object.entries(p).filter(([k]) => !known.has(k));
  const fieldRows = e => {
    const f = e.fields || [];
    const arr = Array.isArray(f) ? f : Object.entries(f).map(([k, v]) => ({ name: k, ...(typeof v === "object" ? v : { type: v }) }));
    const cols = e.columns || [];
    const names = new Set(arr.map(x => x.name));
    cols.forEach(c => { if (c && typeof c === "object") { if (!names.has(c.name)) arr.push(c); } else if (!names.has(c)) arr.push({ name: c, type: "string" }); });
    return arr;
  };
  return `<h1>Паспорт данных</h1>
  <p class="lead">${esc(p.title || state.domain)}${p.version !== undefined ? ` · версия схемы ${esc(p.version)}` : ""}. Описание строится автоматически из активной схемы и правил области.</p>
  <div class="actions"><a class="btn" href="/api/passport/${esc(state.domain)}.md" target="_blank" rel="noopener">Открыть в Markdown</a></div>
  ${p.scope ? `<div class="card"><h3>Границы области</h3><p style="margin:0">${esc(p.scope)}</p></div>` : ""}
  <h2>Поля</h2>
  ${entList.map(([n, e]) => `<div class="card"><h3>${esc(entRu(n))} <span class="muted mono" style="font-size:13px">${esc(n)}</span></h3>${e.description ? `<p class="muted">${esc(e.description)}</p>` : ""}
    <div class="tw"><table><thead><tr><th>Поле</th><th>Тип</th><th>Допустимые значения</th><th>Описание</th></tr></thead><tbody>${fieldRows(e).map(f => `<tr><td class="mono">${esc(f.name)}</td><td>${esc(f.type || "string")}${f.default !== undefined ? ` <span class="muted">по умолчанию ${esc(f.default)}</span>` : ""}</td><td>${(f.vocab || f.values) ? `<span class="fields">${(f.vocab || f.values).map(v => `<code>${esc(v)}</code>`).join("")}</span>` : ""}</td><td>${esc(f.description || "")}</td></tr>`).join("")}</tbody></table></div></div>`).join("")}
  ${p.metrics ? `<h2>Показатели и единицы</h2><div class="card">${renderAny(p.metrics)}</div>` : ""}
  ${p.units ? `<h2>Единицы</h2><div class="card">${renderAny(p.units)}</div>` : ""}
  ${p.vocabularies || p.vocab ? `<h2>Словари</h2><div class="card">${renderAny(p.vocabularies || p.vocab)}</div>` : ""}
  ${p.rules ? `<h2>Правила проверки</h2><div class="card">${renderAny(p.rules)}</div>` : ""}
  ${p.thresholds ? `<h2>Пороги</h2><div class="card">${renderAny(p.thresholds)}</div>` : ""}
  ${rest.map(([k, v]) => `<h2>${esc(k)}</h2><div class="card">${renderAny(v)}</div>`).join("")}`;
}

// ---------- экспорт ----------
async function exportPage() {
  after(() => {
    $$("[data-fmt]").forEach(b => b.onclick = async () => {
      const st = $("#est").value, f = b.dataset.fmt;
      b.disabled = true;
      try { await download(`/export${qs({ domain: state.domain, format: f, status: st })}`, `${state.domain}_${st}.${f === "csv" ? "zip" : "json"}`); toast("Файл сохранён", "ok"); } catch (e) { fail(e); }
      b.disabled = false;
    });
  });
  return `<h1>Экспорт</h1><p class="lead">Выгрузка записей текущей области. CSV содержит по таблице на сущность, колонки строго по схеме, кодировка UTF-8 с BOM; отклонённые записи не выгружаются никогда.</p>
  <div class="card"><div class="filters"><label>Какие записи
    <select id="est"><option value="verified">только подтверждённые</option><option value="all">все (включая извлечённые, с замечаниями и ручные)</option></select></label></div>
  <div class="actions"><button class="btn primary" data-fmt="csv">Скачать CSV (zip)</button><button class="btn" data-fmt="json">Скачать JSON с источниками</button></div></div>`;
}

// ---------- запуск ----------
(async function init() {
  await loadMe();
  try { state.domains = await get("/domains"); if (state.domains.length && !state.domains.some(d => d.domain === state.domain)) setDomain(state.domains[0].domain); } catch (e) { /* нет сервера */ }
  renderShell();
  if (!location.hash) location.hash = "#/";
  route();
})();
