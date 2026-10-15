"""Génère le site statique : page de vente + pages SEO par métier et département, mises à jour chaque jour."""
import shutil
from collections import Counter, defaultdict
from datetime import date, timedelta
from pathlib import Path

import yaml
from markupsafe import Markup

from . import avis as avis_mod
from .config import ROOT
from .render import date_fr, date_limite_fr, render, render_string


def _jours_restants(s, today):
    if not s:
        return None
    try:
        return (date.fromisoformat(str(s)[:10]) - today).days
    except ValueError:
        return None


MOIS = ["janv.", "févr.", "mars", "avr.", "mai", "juin", "juil.", "août", "sept.", "oct.", "nov.", "déc."]


def _film(by_metier, histo=None, essais=8):
    """Données réelles du film de l'accueil : un vrai avis ouvert et, si possible, l'historique DECP de son acheteur."""
    cands = [(k, a) for k, lst in by_metier.items() for a in lst
             if (a.get("jours_restants") or 0) >= 7 and a.get("departements") and str(a.get("date_limite") or "")[:10]]
    cands.sort(key=lambda c: (not c[1].get("pme"), not c[1].get("acheteur_siret"), -len(by_metier[c[0]])))
    if not cands:
        return None
    key, a, fiche = cands[0][0], cands[0][1], None
    if histo:
        for k, x in [c for c in cands if c[1].get("acheteur_siret")][:essais]:
            f = histo.fiche(x["acheteur_siret"], k)
            if f and f.get("montant_median") and any(d.get("montant") for d in f["derniers"]):
                key, a, fiche = k, x, f
                break
        histo.sauver()
    dep = a["departements"][0]
    autres = [x for x in by_metier[key] if x is not a and dep in (x.get("departements") or []) and (x.get("jours_restants") or 0) > 0]
    try:
        dl = date.fromisoformat(str(a["date_limite"])[:10])
    except ValueError:
        return None
    if fiche:
        top = max(d["montant"] or 0 for d in fiche["derniers"]) or 1
        for d in fiche["derniers"]:
            d["pct"] = round(100 * (d["montant"] or 0) / top)
            d["date_fr"] = f"{d['date'][5:7]}/{d['date'][:4]}" if len(d["date"]) >= 7 else ""
        fiche["median_pct"] = round(100 * fiche["montant_median"] / top)
        fiche["gagne"] = next(d for d in fiche["derniers"] if d.get("montant"))
    return {"metier": key, "dep": dep, "avis": a, "autre": autres[0] if autres else None, "n_dep": 1 + len(autres),
            "fiche": fiche, "dl_jour": dl.day, "dl_mois": MOIS[dl.month - 1], "dl": dl.strftime("%d/%m"),
            "depot": (dl - timedelta(days=2)).strftime("%d/%m")}


def build(avis_open, biz, metiers, matcher, out_dir: Path, stats=None, histo=None):
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
        a["date_parution_fr"] = date_limite_fr(a["date_parution"])
        for m in a.get("metiers") or matcher.metiers_of(a):
            by_metier[m].append(a)
    today = date.today()
    for lst in by_metier.values():
        lst.sort(key=lambda a: a["date_parution"], reverse=True)
        for a in lst:
            a["jours_restants"] = _jours_restants(a.get("date_limite"), today)

    common = dict(biz=biz, metiers=metiers, date_fr=date_fr())
    pages = []

    def write(rel, html):
        p = out_dir / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(html, encoding="utf-8")
        pages.append(rel)

    stats = stats or {"total": len(avis_open), "metiers_hits": sum(len(v) for v in by_metier.values())}
    counts = {k: len(by_metier[k]) for k in metiers}
    stats["ouverts"] = len({id(a) for lst in by_metier.values() for a in lst})
    # Aperçu « e-mail du matin » de la page d'accueil : de vrais avis du métier le plus fourni.
    ex_key = max(counts, key=counts.get) if counts else None
    exemple = [a for a in by_metier.get(ex_key, []) if (a.get("jours_restants") or 0) > 0][:3]
    film = _film(by_metier, histo)
    avis_clients = avis_mod.resume(avis_mod.charger())
    write("index.html", render("index.html", root="", path="/", stats=stats, counts=counts,
                               ex_key=ex_key, exemple=exemple, film=film, avis_clients=avis_clients,
                               title=f"{biz['nom']} · Appels d'offres publics par métier, triés" + (" par IA" if biz.get("ia_active") else ""),
                               description=biz["slogan"], **common))
    write("appels-offres/index.html", render(
        "hub.html", root="../", path="/appels-offres/", counts=counts,
        title="Appels d'offres publics en cours par métier", description="Marchés publics en cours classés par métier, mis à jour chaque jour.", **common))

    for key, m in metiers.items():
        lst = by_metier[key]
        dep_counts = Counter(d for a in lst for d in a["departements"])
        deps = sorted(dep_counts)
        write(f"appels-offres/{key}/index.html", render(
            "listing.html", root="../../", path=f"/appels-offres/{key}/", avis=lst[:150], total=len(lst), metier=m, metier_key=key,
            dep=None, deps=deps, dep_counts=dep_counts, h1=f"Appels d'offres {m['libelle'].lower()} en cours",
            title=f"Appels d'offres {m['libelle'].lower()} : marchés publics en cours ({date_fr()})",
            description=f"{len(lst)} marchés publics {m['libelle'].lower()} ouverts, mis à jour chaque jour.", **common))
        for d in deps:
            sub = [a for a in lst if d in a["departements"]]
            write(f"appels-offres/{key}/{d}.html", render(
                "listing.html", root="../../", path=f"/appels-offres/{key}/{d}.html", avis=sub, total=len(sub), metier=m,
                metier_key=key, dep=d, deps=[], dep_counts={}, h1=f"Appels d'offres {m['libelle'].lower()} · département {d}",
                title=f"Appels d'offres {m['libelle'].lower()} {d} : marchés publics en cours",
                description=f"{len(sub)} marchés publics {m['libelle'].lower()} ouverts dans le département {d}.", **common))

    guides = yaml.safe_load((ROOT / "config" / "guides.yaml").read_text(encoding="utf-8"))
    write("guides/index.html", render("guide.html", root="../", path="/guides/", guide=None, guides=guides,
                                      h1="Guides : répondre à un appel d'offres public",
                                      title=f"Guides pour répondre à un appel d'offres public · {biz['nom']}",
                                      description="Guides gratuits et concrets pour répondre aux marchés publics : étapes, MAPA, documents, mémoire technique, prix.", **common))
    for g in guides:
        src = (ROOT / "templates" / "guides" / f"{g['slug']}.html").read_text(encoding="utf-8")
        content = Markup(render_string(src, root="../", **common))
        write(f"guides/{g['slug']}.html", render("guide.html", root="../", path=f"/guides/{g['slug']}.html", guide=g, guides=guides,
                                                 content=content, h1=g["titre"], title=g["titre"], description=g["description"], **common))

    write("criteres/index.html", render("criteres.html", root="../", path="/criteres/", noindex=True,
                                        title=f"Modifier mes critères · {biz['nom']}", description="", **common))
    write("avis/index.html", render("avis.html", root="../", path="/avis/", noindex=True,
                                    title=f"Donner mon avis · {biz['nom']}", description="", **common))
    write("parrainage/index.html", render("parrainage.html", root="../", path="/parrainage/",
                                          title=f"Parrainage : 1 mois offert · {biz['nom']}",
                                          description="Parrainez un confrère : 1 mois offert pour chacun, automatiquement.", **common))
    write("rappel/index.html", render("rappel.html", root="../", path="/rappel/", noindex=True,
                                      title=f"Ajouter un rappel · {biz['nom']}", description="", **common))

    for name in ("mentions-legales", "cgv", "confidentialite"):
        src = (ROOT / "templates" / "legal" / f"{name}.html").read_text(encoding="utf-8")
        content = Markup(render_string(src, e=biz["editeur"], **common))
        write(f"{name}.html", render("legal.html", root="", path=f"/{name}.html", content=content, noindex=True,
                                     title=f"{name.replace('-', ' ').capitalize()} · {biz['nom']}", description="", **common))

    base = biz["site_url"].rstrip("/")
    # Les pages légales (adresse de l'éditeur) restent accessibles mais hors Google.
    pages = [p for p in pages if p not in ("mentions-legales.html", "cgv.html", "confidentialite.html", "rappel/index.html", "criteres/index.html", "avis/index.html")]
    urls = "".join(f"<url><loc>{base}/{p.removesuffix('index.html')}</loc><lastmod>{date.today()}</lastmod></url>" for p in pages)
    (out_dir / "sitemap.xml").write_text(
        f'<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{urls}</urlset>', encoding="utf-8")
    (out_dir / "robots.txt").write_text(f"User-agent: *\nAllow: /\nSitemap: {base}/sitemap.xml\n", encoding="utf-8")
    cname = base.split("://")[-1]
    if not cname.endswith("github.io"):
        (out_dir / "CNAME").write_text(cname, encoding="utf-8")
    return {"pages": len(pages), "par_metier": {k: len(v) for k, v in by_metier.items()}}
