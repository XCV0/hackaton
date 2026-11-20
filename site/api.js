// Клиент API и общие помощники интерфейса.
const LS = {
  get(k, d = null) { try { const v = localStorage.getItem(k); return v === null ? d : v; } catch (e) { return d; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch (e) { /* нет хранилища */ } },
  del(k) { try { localStorage.removeItem(k); } catch (e) { /* нет хранилища */ } },
};
export { LS };

export const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
export const $ = (s, r = document) => r.querySelector(s);
export const $$ = (s, r = document) => [...r.querySelectorAll(s)];
export const num = v => (v === undefined || v === null || v === "" ? NaN : parseFloat(String(v).replace(",", ".")));
export const fmt = v => { const n = num(v); return isNaN(n) ? "—" : (Math.abs(n) >= 100 ? n.toFixed(0) : +n.toPrecision(4)).toString(); };
export const pct = v => (v === null || v === undefined || isNaN(+v) ? "—" : (+v * 100).toFixed(+v === 1 || +v === 0 ? 0 : 1) + " %");
export const dt = s => { if (!s) return "—"; const d = new Date(typeof s === "number" && s < 1e12 ? s * 1000 : s); return isNaN(d) ? String(s) : d.toLocaleString("ru-RU", { dateStyle: "short", timeStyle: "short" }); };

// ---------- состояние ----------
export const state = {
  token: LS.get("token"),
  user: null,
  domain: LS.get("domain", "solar"),
  domains: [],
};
export function setDomain(d) { state.domain = d; LS.set("domain", d); }
export const role = () => (state.user && state.user.role) || "researcher";
export const isStaff = () => ["admin", "annotator"].includes(role());
export const isAdmin = () => role() === "admin";

let authHandler = () => {};
export function onAuthNeeded(fn) { authHandler = fn; }
export function setSession(token, user) {
  state.token = token; state.user = user;
  if (token) LS.set("token", token); else LS.del("token");
}

export class ApiError extends Error {
  constructor(message, status, body) { super(message); this.status = status; this.body = body; }
}

const STATUS_RU = {
  400: "Некорректный запрос", 401: "Нужно войти в систему", 403: "Недостаточно прав для этого действия",
  404: "Не найдено", 409: "Конфликт данных", 413: "Файл слишком большой", 422: "Данные не прошли проверку",
  429: "Слишком много запросов, повторите позже", 500: "Внутренняя ошибка сервера", 502: "Сервер недоступен",
  503: "Сервис временно недоступен", 504: "Сервер не ответил вовремя",
};

function detailText(body) {
  if (!body) return "";
  const d = body.detail ?? body.message ?? body.error;
  if (typeof d === "string") return d;
  if (Array.isArray(d)) return d.map(x => (x.loc ? x.loc.slice(1).join(".") + ": " : "") + (x.msg || JSON.stringify(x))).join("; ");
  if (d && typeof d === "object") return JSON.stringify(d);
  return "";
}

export function qs(params) {
  const u = new URLSearchParams();
  Object.entries(params || {}).forEach(([k, v]) => { if (v !== undefined && v !== null && v !== "" && v !== false) u.set(k, v === true ? "1" : v); });
  const s = u.toString();
  return s ? "?" + s : "";
}

async function raw(path, opts = {}) {
  const headers = { ...(opts.headers || {}) };
  if (state.token) headers.Authorization = "Bearer " + state.token;
  let body = opts.body;
  if (body !== undefined && !(body instanceof FormData) && typeof body !== "string") {
    body = JSON.stringify(body); headers["Content-Type"] = "application/json";
  }
  let res;
  try {
    res = await fetch("/api" + path, { method: opts.method || "GET", headers, body });
  } catch (e) {
    throw new ApiError("Нет связи с сервером. Проверьте, что он запущен.", 0);
  }
  if (!res.ok) {
    let b = null; try { b = await res.json(); } catch (e) { /* не JSON */ }
    const det = detailText(b);
    const msg = (STATUS_RU[res.status] || "Ошибка " + res.status) + (det ? ": " + det : "");
    if (res.status === 401) { setSession(null, null); authHandler(msg); }
    throw new ApiError(msg, res.status, b);
  }
  return res;
}

export async function api(path, opts = {}) {
  const res = await raw(path, opts);
  const ct = res.headers.get("content-type") || "";
  if (ct.includes("json")) return res.json();
  return res.text();
}
export const get = (p, params) => api(p + qs(params));
export const post = (p, body) => api(p, { method: "POST", body: body ?? {} });
export const patch = (p, body) => api(p, { method: "PATCH", body });
export const put = (p, body) => api(p, { method: "PUT", body });
export const del = p => api(p, { method: "DELETE" });

export async function download(path, filename) {
  const res = await raw(path);
  const blob = await res.blob();
  const cd = res.headers.get("content-disposition") || "";
  const m = /filename\*?=(?:UTF-8'')?"?([^";]+)/i.exec(cd);
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob); a.download = m ? decodeURIComponent(m[1]) : filename;
  document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 4000);
}

export async function loadMe() {
  if (!state.token) { state.user = null; return null; }
  try { const m = await get("/auth/me"); const u = m.user || m; state.user = u && u.name && !u.anonymous ? u : null; } catch (e) { state.user = null; }
  return state.user;
}
export async function login(name, password) {
  const r = await post("/auth/login", { name, password });
  setSession(r.token, r.user);
  return r.user;
}
export function logout() { setSession(null, null); }

// ---------- интерфейс ----------
export function toast(msg, kind = "") {
  let box = $("#toasts");
  if (!box) { box = document.createElement("div"); box.id = "toasts"; document.body.appendChild(box); }
  const t = document.createElement("div"); t.className = "toast " + kind; t.textContent = msg;
  box.appendChild(t); setTimeout(() => t.remove(), kind === "bad" ? 7000 : 3500);
}
export const fail = e => { console.error(e); toast(e && e.message ? e.message : String(e), "bad"); };

export function modal(html, { wide = false } = {}) {
  const bg = document.createElement("div"); bg.className = "modal-bg";
  bg.innerHTML = `<div class="modal ${wide ? "wide" : ""}" role="dialog"><button class="btn close" data-x>Закрыть</button>${html}</div>`;
  const close = () => bg.remove();
  bg.addEventListener("click", e => { if (e.target === bg || e.target.closest("[data-x]")) close(); });
  document.body.appendChild(bg);
  return { el: bg.firstElementChild, close };
}

export const STATUS = {
  extracted: ["Извлечено", "tag"], verified: ["Подтверждено", "tag ok"], flagged: ["Есть замечание", "tag cited"],
  manual: ["Вручную", "tag man"], rejected: ["Отклонено", "tag bad"],
  registered: ["Зарегистрирована", "tag"], processing: ["В обработке", "tag ok"], needs_ocr: ["Нужен OCR", "tag cited"],
  error: ["Ошибка", "tag bad"], excluded: ["Вне области", "tag dup"],
  open: ["Открыто", "tag cited"], accepted: ["Принято", "tag ok"], fixed: ["Исправлено", "tag ok"], kept: ["Оставлено", "tag"],
  pending: ["Ожидает", "tag"], running: ["Идёт", "tag ok"], done: ["Готово", "tag ok"],
};
export const badge = s => { const [t, c] = STATUS[s] || [s || "—", "tag"]; return `<span class="${c}">${esc(t)}</span>`; };
export const SEV = { error: ["Ошибка", "tag bad"], warning: ["Предупреждение", "tag cited"], info: ["Сведения", "tag"] };
export const sevBadge = s => { const [t, c] = SEV[s] || [s || "—", "tag"]; return `<span class="${c}">${esc(t)}</span>`; };
export const ORIGIN = { primary: "собственный", cited: "из литературы", rule: "правило", model: "модель", verify: "проверка цитаты" };
export const originRu = o => ORIGIN[o] || o || "—";

export function srcUrl(pub, page, q) {
  return "viewer.html" + qs({ pub, page: page || 1, q: q || "" });
}
export const srcLink = (pub, page, q, label = "Показать в источнике") =>
  `<a class="btn" target="_blank" rel="noopener" href="${esc(srcUrl(pub, page, q))}">${esc(label)}</a>`;

export function pager(total, limit, offset) {
  const page = Math.floor(offset / limit) + 1, pages = Math.max(1, Math.ceil(total / limit));
  return `<div class="pager"><button class="btn" data-pg="${Math.max(0, offset - limit)}" ${offset <= 0 ? "disabled" : ""}>Назад</button>
    <span>Страница ${page} из ${pages}</span>
    <button class="btn" data-pg="${offset + limit}" ${offset + limit >= total ? "disabled" : ""}>Вперёд</button></div>`;
}

export function options(items, sel, blank) {
  return (blank !== undefined ? `<option value="">${esc(blank)}</option>` : "") +
    items.map(i => { const [v, l] = Array.isArray(i) ? i : [i, i]; return `<option value="${esc(v)}" ${String(v) === String(sel ?? "") ? "selected" : ""}>${esc(l)}</option>`; }).join("");
}

// ---------- корзина сравнения ----------
export const basket = {
  list() { try { return JSON.parse(LS.get("basket_" + state.domain, "[]")); } catch (e) { return []; } },
  save(a) { LS.set("basket_" + state.domain, JSON.stringify(a)); window.dispatchEvent(new Event("basket")); },
  has(id) { return this.list().includes(id); },
  toggle(id) { const a = this.list(); const i = a.indexOf(id); if (i >= 0) a.splice(i, 1); else a.push(id); this.save(a); return i < 0; },
  clear() { this.save([]); },
};

// ---------- схема области (для паспорта, фильтров и редактора) ----------
let yamlLib = null;
export function loadYaml() {
  if (window.jsyaml) return Promise.resolve(window.jsyaml);
  if (yamlLib) return yamlLib;
  yamlLib = new Promise((res, rej) => {
    const s = document.createElement("script");
    s.src = "https://cdnjs.cloudflare.com/ajax/libs/js-yaml/4.1.0/js-yaml.min.js";
    s.onload = () => res(window.jsyaml); s.onerror = () => rej(new Error("Не удалось загрузить разбор YAML"));
    document.head.appendChild(s);
  });
  return yamlLib;
}

const schemaCache = {};
export async function loadSchema(domain, force = false) {
  if (!force && schemaCache[domain]) return schemaCache[domain];
  let out = null;
  if (isStaff()) {
    try {
      const [cfg, y] = await Promise.all([get("/config/" + domain), loadYaml()]);
      const s = y.load(cfg.schema_yaml) || {};
      out = { result_entity: s.result_entity, entities: {}, metrics: s.metrics || {}, raw: s };
      Object.entries(s.entities || {}).forEach(([n, e]) => {
        out.entities[n] = { columns: e.columns || Object.keys(e.fields || {}), fields: e.fields || {}, refs: e.refs || {}, id: e.id };
      });
    } catch (e) { out = null; }
  }
  if (!out) {
    try {
      const p = await get("/passport/" + domain);
      out = { result_entity: p.result_entity, entities: {}, metrics: {}, raw: p };
      const ents = p.entities || {};
      const list = Array.isArray(ents) ? ents.map(e => [e.name || e.entity, e]) : Object.entries(ents);
      list.forEach(([n, e]) => {
        const f = e.fields || [];
        const fields = {};
        let columns = e.columns;
        if (columns && columns.length && typeof columns[0] === "object") {
          columns.forEach(c => { fields[c.name] = { type: c.type, vocab: c.vocab, default: c.default, description: c.description }; });
          columns = columns.map(c => c.name);
        }
        (Array.isArray(f) ? f : Object.entries(f).map(([k, v]) => ({ name: k, ...(typeof v === "object" ? v : { type: v }) })))
          .forEach(x => { fields[x.name] = { type: x.type, vocab: x.vocab || x.values, default: x.default, description: x.description }; });
        out.entities[n] = { columns: columns || Object.keys(fields), fields, refs: e.refs || {}, id: e.id };
      });
    } catch (e) { out = { entities: {}, metrics: {}, raw: {} }; }
  }
  schemaCache[domain] = out;
  return out;
}
export const dropSchemaCache = () => Object.keys(schemaCache).forEach(k => delete schemaCache[k]);

// ---------- универсальный вывод структуры (паспорт, инвентаризация) ----------
export function renderAny(v, depth = 0) {
  if (v === null || v === undefined || v === "") return `<span class="muted">—</span>`;
  if (typeof v === "boolean") return v ? "да" : "нет";
  if (typeof v !== "object") return esc(v);
  if (Array.isArray(v)) {
    if (!v.length) return `<span class="muted">пусто</span>`;
    if (v.every(x => x === null || typeof x !== "object")) return `<span class="fields">${v.map(x => `<code>${esc(x)}</code>`).join("")}</span>`;
    if (v.every(x => x && typeof x === "object" && !Array.isArray(x))) {
      const keys = [...new Set(v.flatMap(x => Object.keys(x)))];
      return `<div class="tw"><table><thead><tr>${keys.map(k => `<th>${esc(k)}</th>`).join("")}</tr></thead><tbody>${
        v.map(x => `<tr>${keys.map(k => `<td>${depth > 2 ? esc(JSON.stringify(x[k] ?? "")) : renderAny(x[k], depth + 1)}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`;
    }
    return v.map(x => renderAny(x, depth + 1)).join("<br>");
  }
  const ent = Object.entries(v);
  if (!ent.length) return `<span class="muted">пусто</span>`;
  return `<dl class="kv">${ent.map(([k, x]) => `<dt>${esc(k)}</dt><dd>${depth > 3 ? esc(JSON.stringify(x)) : renderAny(x, depth + 1)}</dd>`).join("")}</dl>`;
}
