#!/usr/bin/env python3
"""扫描 /var/www/plan/<项目>/<页面>/meta.json，写出 /var/www/plan/catalog.json（项目顺序见 projects.json）。"""
import json, os
ROOT = "/var/www/plan"
projects = json.load(open(os.path.join(ROOT, "projects.json"), encoding="utf-8"))
pages = []
for p in projects:
    d = os.path.join(ROOT, p["id"])
    for slug in sorted(os.listdir(d)) if os.path.isdir(d) else []:
        m = os.path.join(d, slug, "meta.json")
        if os.path.exists(m):
            meta = json.load(open(m, encoding="utf-8"))
            meta.update(project=p["id"], url="/%s/%s/" % (p["id"], slug))
            pages.append(meta)
pages.sort(key=lambda g: g.get("updated", ""), reverse=True)
tmp = os.path.join(ROOT, "catalog.json.tmp")
json.dump({"projects": projects, "pages": pages}, open(tmp, "w", encoding="utf-8"), ensure_ascii=False)
os.replace(tmp, os.path.join(ROOT, "catalog.json"))
print(len(pages), "pages")
