import { LS, esc } from "./api.js";
const t = LS.get("theme"); if (t) document.documentElement.setAttribute("data-theme", t);
const P = new URLSearchParams(location.search);
const pub = P.get("pub"), Q = P.get("q") || "";
let page = Math.max(1, parseInt(P.get("page") || "1", 10)), scale = 1.3, doc = null, busy = false, again = false;
const $ = s => document.querySelector(s);
const msg = (t, cls = "") => { $("#msg").innerHTML = t ? `<div class="note ${cls}">${t}</div>` : ""; };

// Нормализация как в app/verify.py: регистр, лигатуры (NFKC), юникод-минусы, мягкие переносы,
// склеенные пробелы и переносы слов; для сопоставления пробелы и дефисы отбрасываются.
const DASH = /[‐-―−‒﹣－]/g;
function normChars(str) {
  const out = [];
  for (const ch of str) {
    if (ch === "­") continue;
    let c = ch.replace(DASH, "-").normalize("NFKC").toLowerCase();
    for (const x of c) { if (/\s/.test(x) || x === "-") continue; out.push(x); }
  }
  return out;
}
const normQuote = s => normChars(String(s).replace(/(\S)-\s*\n\s*(\S)/g, "$1$2")).join("");

function buildIndex(items) {
  const chars = [], map = [];
  items.forEach((it, i) => { normChars(it.str).forEach(c => { chars.push(c); map.push(i); }); });
  return { text: chars.join(""), map };
}
function highlight(divs, items) {
  divs.forEach(d => d.classList.remove("hl", "hl2"));
  const q = normQuote(Q);
  if (!q) return { found: null };
  const { text, map } = buildIndex(items);
  let idx = text.indexOf(q);
  const hit = new Set();
  if (idx >= 0) { for (let k = idx; k < idx + q.length; k++) hit.add(map[k]); hit.forEach(i => divs[i] && divs[i].classList.add("hl")); return { found: "full", first: [...hit][0] }; }
  // запасной путь: длинные числа и слова фрагмента
  const toks = [...new Set((Q.match(/\d[\d.,]*\d|\d{3,}|[A-Za-zА-Яа-я]{7,}/g) || []).map(x => normQuote(x)).filter(x => x.length >= 3))];
  let any = 0, first = null;
  toks.forEach(tk => {
    let from = 0, p;
    while ((p = text.indexOf(tk, from)) >= 0) {
      for (let k = p; k < p + tk.length; k++) { const d = divs[map[k]]; if (d) { d.classList.add("hl2"); if (first === null) first = map[k]; } }
      any++; from = p + tk.length;
    }
  });
  return { found: any ? "tokens" : null, first, n: any, total: toks.length };
}

async function render() {
  if (busy) { again = true; return; }
  busy = true;
  try {
    const pg = await doc.getPage(page);
    const vp = pg.getViewport({ scale });
    const cv = $("#cv"), box = $("#box"), tl = $("#tl");
    const ratio = window.devicePixelRatio || 1;
    cv.width = Math.floor(vp.width * ratio); cv.height = Math.floor(vp.height * ratio);
    cv.style.width = vp.width + "px"; cv.style.height = vp.height + "px";
    box.style.width = vp.width + "px"; box.style.height = vp.height + "px";
    tl.innerHTML = ""; tl.style.setProperty("--scale-factor", vp.scale); tl.style.width = vp.width + "px"; tl.style.height = vp.height + "px";
    await pg.render({ canvasContext: cv.getContext("2d"), viewport: vp, transform: ratio !== 1 ? [ratio, 0, 0, ratio, 0, 0] : null }).promise;
    const content = await pg.getTextContent();
    const divs = [];
    const task = pdfjsLib.renderTextLayer({ textContentSource: content, container: tl, viewport: vp, textDivs: divs });
    await (task.promise || task);
    // textDivs содержит элементы в порядке items (только с текстом); сопоставляем по индексу непустых
    const items = content.items.filter(i => typeof i.str === "string");
    const aligned = [];
    let di = 0;
    items.forEach(it => { aligned.push(divs[di] || null); di++; });
    $("#pg").textContent = `стр. ${page} из ${doc.numPages}`;
    $("#prev").disabled = page <= 1; $("#next").disabled = page >= doc.numPages;
    $("#zv").textContent = Math.round(scale / 1.3 * 100) + " %";
    if (Q) {
      const r = highlight(aligned, items);
      if (r.found === "full") msg("");
      else if (r.found === "tokens") msg(`Фрагмент целиком на странице ${page} не найден, подсвечены совпадающие числа и длинные слова (${r.n}).`, "warnbox");
      else msg(`Фрагмент на странице ${page} не найден. Возможно, он на соседней странице, разбит таблицей или текст страницы распознан иначе. Попробуйте листать страницы.`, "warnbox");
      const f = r.first != null && aligned[r.first];
      if (f) f.scrollIntoView({ block: "center", behavior: "smooth" });
    }
  } catch (e) {
    console.error(e); msg("Не удалось отобразить страницу: " + esc(e.message), "badbox");
  }
  busy = false;
  if (again) { again = false; render(); }
}

function go(n) { page = Math.min(Math.max(1, n), doc.numPages); const u = new URL(location.href); u.searchParams.set("page", page); history.replaceState(null, "", u); render(); }

(async function () {
  if (!pub) { msg("Не указана публикация (параметр pub).", "badbox"); return; }
  $("#pubname").textContent = pub;
  if (Q) $("#vq").innerHTML = `<b>Цитата:</b> <span class="hl">${esc(Q)}</span>`;
  if (!window.pdfjsLib) { msg("Не удалось загрузить pdf.js с cdnjs.cloudflare.com. Проверьте доступ к интернету.", "badbox"); return; }
  pdfjsLib.GlobalWorkerOptions.workerSrc = "https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/pdf.worker.min.js";
  try {
    const headers = {}; const tok = LS.get("token"); if (tok) headers.Authorization = "Bearer " + tok;
    const res = await fetch("/api/pdf/" + encodeURIComponent(pub), { headers });
    if (!res.ok) throw new Error(res.status === 404 ? "PDF этой публикации не найден на сервере" : res.status === 401 || res.status === 403 ? "Нет доступа: войдите в систему на главной странице" : "Ошибка сервера " + res.status);
    const buf = await res.arrayBuffer();
    doc = await pdfjsLib.getDocument({ data: buf }).promise;
  } catch (e) { msg("Не удалось открыть PDF: " + esc(e.message), "badbox"); return; }
  if (page > doc.numPages) page = doc.numPages;
  $("#prev").onclick = () => go(page - 1); $("#next").onclick = () => go(page + 1);
  $("#zi").onclick = () => { scale = Math.min(3, scale + 0.2); render(); };
  $("#zo").onclick = () => { scale = Math.max(0.6, scale - 0.2); render(); };
  document.addEventListener("keydown", e => { if (e.key === "ArrowLeft") go(page - 1); if (e.key === "ArrowRight") go(page + 1); });
  if (innerWidth < 700) scale = 0.9;
  render();
})();
