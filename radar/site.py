"""Génère le site statique : page de vente + pages SEO par métier et département, mises à jour chaque jour."""
import shutil
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

from markupsafe import Markup

from .config import ROOT
from .render import date_fr, date_limite_fr, render, render_string


def build(avis_open, biz, metiers, matcher, out_dir: Path, stats=None):
    out_dir = Path(out_dir)
    if out_dir.exists():
        shutil.rmtree(out_dir)
    (out_dir / "appels-offres").mkdir(parents=True)
    shutil.copytree(ROOT / "static", out_dir / "static")

    by_metier = defaultdict(list)
    for a in avis_open:
        if matcher.is_award_notice(a):
            continue
        a["date_limite_fr"] = date_limite_fr(a["date_limite"])
        for m in a.get("metiers") or matcher.metiers_of(a):
            by_metier[m].append(a)
    for lst in by_metier.values():
        lst.sort(key=lambda a: a["date_parution"], reverse=True)

    common = dict(biz=biz, metiers=metiers, date_fr=date_fr())
    pages = []

    def write(rel, html):
        p = out_dir / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(html, encoding="utf-8")
        pages.append(rel)

    stats = stats or {"total": len(avis_open), "metiers_hits": sum(len(v) for v in by_metier.values())}
    write("index.html", render("index.html", root="", path="/", stats=stats,
                               title=f"{biz['nom']} · Appels d'offres publics par métier, triés" + (" par IA" if biz.get("ia_active") else ""),
                               description=biz["slogan"], **common))
    write("appels-offres/index.html", render(
        "hub.html", root="../", path="/appels-offres/", counts={k: len(by_metier[k]) for k in metiers},
        title="Appels d'offres publics en cours par métier", description="Marchés publics en cours classés par métier, mis à jour chaque jour.", **common))

    for key, m in metiers.items():
        lst = by_metier[key]
        dep_counts = Counter(d for a in lst for d in a["departements"])
        deps = sorted(dep_counts)
        write(f"appels-offres/{key}/index.html", render(
            "listing.html", root="../../", path=f"/appels-offres/{key}/", avis=lst[:150], metier=m, metier_key=key,
            dep=None, deps=deps, dep_counts=dep_counts, h1=f"Appels d'offres {m['libelle'].lower()} en cours",
            title=f"Appels d'offres {m['libelle'].lower()} : marchés publics en cours ({date_fr()})",
            description=f"{len(lst)} marchés publics {m['libelle'].lower()} ouverts, mis à jour chaque jour.", **common))
        for d in deps:
            sub = [a for a in lst if d in a["departements"]]
            write(f"appels-offres/{key}/{d}.html", render(
                "listing.html", root="../../", path=f"/appels-offres/{key}/{d}.html", avis=sub, metier=m,
                metier_key=key, dep=d, deps=[], dep_counts={}, h1=f"Appels d'offres {m['libelle'].lower()} · département {d}",
                title=f"Appels d'offres {m['libelle'].lower()} {d} : marchés publics en cours",
                description=f"{len(sub)} marchés publics {m['libelle'].lower()} ouverts dans le département {d}.", **common))

    for name in ("mentions-legales", "cgv", "confidentialite"):
        src = (ROOT / "templates" / "legal" / f"{name}.html").read_text(encoding="utf-8")
        content = Markup(render_string(src, e=biz["editeur"], **common))
        write(f"{name}.html", render("legal.html", root="", path=f"/{name}.html", content=content,
                                     title=f"{name.replace('-', ' ').capitalize()} · {biz['nom']}", description="", **common))

    base = biz["site_url"].rstrip("/")
    urls = "".join(f"<url><loc>{base}/{p.removesuffix('index.html')}</loc><lastmod>{date.today()}</lastmod></url>" for p in pages)
    (out_dir / "sitemap.xml").write_text(
        f'<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{urls}</urlset>', encoding="utf-8")
    (out_dir / "robots.txt").write_text(f"User-agent: *\nAllow: /\nSitemap: {base}/sitemap.xml\n", encoding="utf-8")
    cname = base.split("://")[-1]
    if not cname.endswith("github.io"):
        (out_dir / "CNAME").write_text(cname, encoding="utf-8")
    return {"pages": len(pages), "par_metier": {k: len(v) for k, v in by_metier.items()}}
