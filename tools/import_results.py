"""Загружает готовые CSV и исходные PDF в каталог данных сервиса."""
import argparse
import csv
import shutil
from pathlib import Path

from app import config, db, evaluation, pipeline


def read_tables(schema, directory):
    tables = {}
    for name, entity in schema["entities"].items():
        with (directory / f"{name}.csv").open(encoding="utf-8-sig", newline="") as file:
            reader = csv.DictReader(file)
            if reader.fieldnames != entity["columns"]:
                raise ValueError(f"{name}.csv: порядок или названия колонок отличаются от схемы")
            tables[name] = list(reader)
            if any(None in row or None in row.values() for row in tables[name]):
                raise ValueError(f"{name}.csv: неверное число колонок в строке")
    return tables


def import_results(domain, directory, pdf_dir, split):
    cfg = config.load_domain(domain)
    schema = cfg["schema"]
    pub_table = schema["publication_entity"]
    tables = read_tables(schema, directory)
    pub_rows = tables.pop(pub_table)
    publications = {row["publication_id"]: row for row in pub_rows}
    if len(publications) != len(pub_rows) or not publications or any(
        not pid.replace("_", "").replace("-", "").isalnum() for pid in publications
    ):
        raise ValueError("Некорректные идентификаторы публикаций")
    entity_ids = {}
    by_pub = {pid: {} for pid in publications}
    for name, rows in tables.items():
        key = schema["entities"][name]["id"]
        seen = set()
        for row in rows:
            pid, rid = row["publication_id"], row[key]
            if pid not in publications or not rid or (pid, rid) in seen:
                raise ValueError(f"{name}: отсутствует публикация или идентификатор, либо повторяется ключ {pid}/{rid}")
            seen.add((pid, rid))
            by_pub[pid].setdefault(name, []).append(row)
        entity_ids[name] = seen
    for pid, pub in publications.items():
        path = pdf_dir / f"{pid}.pdf"
        if not path.is_file():
            raise FileNotFoundError(path)
        if pub["in_scope"] not in ("true", "false") or pub["in_scope"] == "false" and by_pub[pid]:
            raise ValueError(f"{pid}: неверный статус области или лишние строки исключённой публикации")
        for name, rows in by_pub[pid].items():
            for row in rows:
                for key, target in schema["entities"][name].get("refs", {}).items():
                    for ref in filter(None, (x.strip() for x in row.get(key, "").split(";"))):
                        if (pid, ref) not in entity_ids[target]:
                            raise ValueError(f"{pid}/{name}: {key}={ref} отсутствует в {target}")
    imported, skipped = [], []
    for pid, pub in publications.items():
        if evaluation.resolve_pub(domain, pid):
            skipped.append(pid)
            continue
        src = pdf_dir / f"{pid}.pdf"
        folder = db.DB_PATH.parent / "imports" / domain
        folder.mkdir(parents=True, exist_ok=True)
        dest = folder / src.name
        shutil.copyfile(src, dest)
        pages = pipeline._extract_pages(dest)
        if not pages:
            raise ValueError(f"{src}: PDF не содержит страниц")
        table_blocks = pipeline._table_blocks(dest, len(pages))
        pub_id = pipeline._new_pub_id(domain, pid)
        now = db.now()
        with db.tx() as conn:
            conn.execute(
                """INSERT INTO publications(id,domain,filename,pdf_path,sha256,doi,title,year,authors,pages,text_pages,
                                             in_scope,exclusion_reason,status,split,message,created,updated)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (pub_id, domain, src.name, str(dest.resolve()), pipeline._sha(dest), pub.get("DOI", ""), pub["title"],
                 pub["year"], pub["authors"], len(pages), sum(bool(t) for t in pages), int(pub["in_scope"] == "true"),
                 pub["exclusion_reason"], "registered", split, "Импорт готовой разметки", now, now),
            )
            conn.executemany(
                "INSERT INTO pages(pub_id,page,text) VALUES(?,?,?)",
                ((pub_id, i, (t + "\n" + table_blocks.get(i, "")).strip()) for i, t in enumerate(pages, 1)),
            )
            meta = {"id": pub_id, "domain": domain}
            for name, rows in by_pub[pid].items():
                if name == "issues":
                    for row in rows:
                        conn.execute(
                            """INSERT INTO issues(pub_id,domain,rule_id,severity,record_ids,description,source_page,
                                                  source_fragment,origin,status,created,updated)
                               VALUES(?,?,?,'warning',?,?,?,?, 'import','open',?,?)""",
                            (pub_id, domain, row["check_name"], db.jd([rid.strip() for rid in row["record_ids"].split(";") if rid.strip()]),
                             row["description"], pipeline._as_int(row["source_page"]), row["source_fragment"], now, now),
                        )
                else:
                    key = schema["entities"][name]["id"]
                    for row in rows:
                        pipeline._insert_record(conn, meta, name, row[key], row)
        pub_meta = db.q1("SELECT * FROM publications WHERE id=?", (pub_id,))
        pipeline._st_verify({"pub": pub_meta, "cfg": cfg})
        if pipeline.revalidate(pub_id) == "error":
            raise RuntimeError(db.q1("SELECT message FROM publications WHERE id=?", (pub_id,))["message"])
        for stage in pipeline.STAGES[:5]:
            pipeline._set_stage(pub_id, stage, "done", "Импорт готовой CSV-разметки")
        imported.append(pid)
    return {"imported": imported, "skipped_existing": skipped}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("domain", choices=config.list_domains())
    parser.add_argument("csv_dir", type=Path)
    parser.add_argument("pdf_dir", type=Path)
    parser.add_argument("--db", type=Path)
    parser.add_argument("--split", default="dev")
    args = parser.parse_args()
    if args.db:
        db.DB_PATH = args.db
    db.init_db()
    config.seed_versions()
    print(import_results(args.domain, args.csv_dir, args.pdf_dir, args.split))


if __name__ == "__main__":
    main()
