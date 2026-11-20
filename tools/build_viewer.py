"""Статический интерфейс: build_viewer.py <csv_dir> <out.html>. Поиск, фильтры, источник значения, замечания."""
import sys, csv, json, pathlib
d, out = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
rd = lambda n: list(csv.DictReader(open(d / n, encoding="utf-8-sig"))) if (d / n).exists() else []
data = {n: rd(n + ".csv") for n in ("publications", "materials", "devices", "results", "issues")}
html = """<!doctype html><meta charset=utf-8><title>Данные из публикаций</title>
<style>body{font:14px system-ui;margin:20px;color:#1f3555}h1{font-size:20px}input,select{padding:5px;margin:0 6px 8px 0}
table{border-collapse:collapse;width:100%}td,th{border:1px solid #d5dbe6;padding:4px 6px;text-align:left;vertical-align:top}th{background:#edf0f5;position:sticky;top:0}
.bad{background:#fff1ef}.cited{color:#888}.src{font-size:12px;color:#555}.tab button{padding:6px 12px;margin-right:4px}</style>
<h1>Извлечённые данные</h1>
<div class=tab><button onclick="show('results')">Результаты</button><button onclick="show('issues')">Замечания проверки</button><button onclick="show('publications')">Публикации</button></div><p>
<input id=q placeholder="Поиск по тексту"> <select id=pub><option value="">Все публикации</option></select>
<select id=met><option value="">Все показатели</option></select> <select id=org><option value="">Любое происхождение</option><option>primary</option><option>cited</option></select>
<span id=cnt></span><div id=t></div>
<script>const D=__DATA__;let tab='results';
const cols={results:['publication_id','result_id','device_id','metric_name','normalized_value','normalized_unit','aggregation','result_origin','source_page','source_table','source_fragment','extraction_status','duplicate_of'],
issues:['publication_id','issue_id','record_ids','check_name','description','source_page','source_fragment'],
publications:['publication_id','title','year','DOI','authors','in_scope','exclusion_reason']};
const issueRecs=new Set(D.issues.flatMap(i=>i.record_ids.split(';').filter(Boolean).map(r=>i.publication_id+'|'+r)));
const uniq=a=>[...new Set(a)].sort();
pub.innerHTML+=uniq(D.publications.map(p=>p.publication_id)).map(x=>`<option>${x}</option>`).join('');
met.innerHTML+=uniq(D.results.map(r=>r.metric_name)).map(x=>`<option>${x}</option>`).join('');
const esc=s=>String(s??'').replace(/[&<]/g,c=>({'&':'&amp;','<':'&lt;'}[c]));
function show(t){tab=t;render()}
function render(){const c=cols[tab],q=document.getElementById('q').value.toLowerCase();
 let rows=D[tab].filter(r=>(!pub.value||r.publication_id==pub.value)&&(tab!='results'||((!met.value||r.metric_name==met.value)&&(!org.value||r.result_origin==org.value)))&&(!q||JSON.stringify(r).toLowerCase().includes(q)));
 cnt.textContent=rows.length+' строк';
 t.innerHTML='<table><tr>'+c.map(x=>'<th>'+x+'</th>').join('')+'</tr>'+rows.slice(0,1500).map(r=>{
  const cls=(tab=='results'&&issueRecs.has(r.publication_id+'|'+r.result_id)?'bad ':'')+(r.result_origin=='cited'?'cited':'');
  return `<tr class="${cls}">`+c.map(x=>'<td'+(x.startsWith('source')?' class=src':'')+'>'+esc(r[x])+'</td>').join('')+'</tr>'}).join('')+'</table>'}
[q,pub,met,org].forEach(e=>e.oninput=render);render();</script>"""
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(html.replace("__DATA__", json.dumps(data, ensure_ascii=False)), encoding="utf-8")
print("ok", out, {k: len(v) for k, v in data.items()})
