"""Génère le site statique : page de vente + pages SEO par métier et département, mises à jour chaque jour."""
import hashlib
import json
import shutil
from collections import Counter, defaultdict
from datetime import date, timedelta
from pathlib import Path

import yaml
from markupsafe import Markup

from . import avis as avis_mod
from .config import ROOT
from .render import date_fr, date_limite_fr, render, render_string
from .textutil import slugify


def _jours_restants(s, today):
    if not s:
        return None
    try:
        return (date.fromisoformat(str(s)[:10]) - today).days
    except ValueError:
        return None


CARTE = json.loads((ROOT / "config" / "carte_france.json").read_text(encoding="utf-8"))
NOMS_DEP = {d["code"]: d["nom"] for d in CARTE["departements"]}
RECENTS_JOURS = 3   # les marchés publiés ces 3 derniers jours sont réservés aux abonnés (pas sur les pages publiques)


def _publics(lst, today):
    """(avis visibles publiquement, nombre d'avis récents réservés aux abonnés)."""
    seuil = (today - timedelta(days=RECENTS_JOURS - 1)).isoformat()
    vis = [a for a in lst if not (a.get("date_parution") or "") >= seuil]
    return vis, len(lst) - len(vis)


def _nom_dep(d):
    return f"{NOMS_DEP[d]} ({d})" if d in NOMS_DEP else f"département {d}"


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


VILLE_MIN, VILLES_MAX = 5, 250   # une page par ville seulement si assez d'avis (pas de pages creuses)


def _villes(by_metier, metiers):
    """Villes d'acheteurs avec au moins VILLE_MIN avis ouverts de nos métiers, les plus fournies d'abord."""
    groupes = defaultdict(dict)
    for key in metiers:
        for a in by_metier.get(key, []):
            if a.get("ville") and (a.get("jours_restants") is None or a["jours_restants"] >= 0):
                dep = next((d for d in a["departements"] if d), "")
                groupes[(a["ville"], dep)].setdefault(key, []).append(a)
    out = []
    for (nom, dep), pm in groupes.items():
        uniques = {id(a): a for lst in pm.values() for a in lst}
        if len(uniques) < VILLE_MIN or not dep:
            continue
        acheteurs = Counter(a["acheteur"] for a in uniques.values() if a.get("acheteur"))
        out.append({"nom": nom, "dep": dep, "slug": f"{slugify(nom)}-{slugify(dep)}", "total": len(uniques),
                    "par_metier": sorted(pm.items(), key=lambda kv: -len(kv[1])),
                    "acheteurs": [n for n, _ in acheteurs.most_common(5)]})
    out.sort(key=lambda v: -v["total"])
    return out[:VILLES_MAX]


def _zone(dep, nom, par_metier, today, slug=None):
    """Ville ou département : avis par métier, sans les récents (réservés aux abonnés)."""
    uniques = {id(a): a for lst in par_metier.values() for a in lst}
    acheteurs = Counter(a["acheteur"] for a in uniques.values() if a.get("acheteur"))
    pm = [(k, *_publics(lst, today)) for k, lst in sorted(par_metier.items(), key=lambda kv: -len(kv[1]))]
    return {"nom": nom, "dep": dep, "slug": slug, "total": len(uniques), "par_metier": pm,
            "caches": len(uniques) - len({id(a) for _, vis, _ in pm for a in vis}),
            "acheteurs": [n for n, _ in acheteurs.most_common(5)]}


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

    css_v = hashlib.md5((ROOT / "static" / "style.css").read_bytes()).hexdigest()[:8]   # nouvelle adresse à chaque changement : pas d'ancien style en cache
    common = dict(biz=biz, metiers=metiers, date_fr=date_fr(), carte=CARTE, recents_jours=RECENTS_JOURS, css_v=css_v)
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
    villes = _villes(by_metier, metiers)
    # Une page par département (tous métiers), ouverte depuis la carte de France du hub.
    par_dep = defaultdict(lambda: defaultdict(list))
    for key in metiers:
        for a in by_metier.get(key, []):
            for d in a["departements"]:
                if d:
                    par_dep[d][key].append(a)
    zones_dep = [_zone(d, _nom_dep(d), pm, today) for d, pm in par_dep.items()]
    write("appels-offres/index.html", render(
        "hub.html", root="../", path="/appels-offres/", counts=counts, nb_villes=len(villes),
        carte_n={z["dep"]: z["total"] for z in zones_dep}, carte_liens={z["dep"]: f"departements/{z['dep']}.html" for z in zones_dep},
        title="Appels d'offres publics en cours par métier", description="Marchés publics en cours classés par métier, mis à jour chaque jour.", **common))

    for key, m in metiers.items():
        lst = by_metier[key]
        dep_counts = Counter(d for a in lst for d in a["departements"])
        deps = sorted(dep_counts)
        vis, caches = _publics(lst, today)
        write(f"appels-offres/{key}/index.html", render(
            "listing.html", root="../../", path=f"/appels-offres/{key}/", avis=vis[:150], visibles=len(vis), caches=caches, total=len(lst),
            metier=m, metier_key=key, dep=None, deps=deps, dep_counts=dep_counts, noms_dep=NOMS_DEP,
            carte_n=dep_counts, carte_liens={d: f"{d}.html" for d in deps}, h1=f"Appels d'offres {m['libelle'].lower()} en cours",
            title=f"Appels d'offres {m['libelle'].lower()} : marchés publics en cours ({date_fr()})",
            description=f"{len(lst)} marchés publics {m['libelle'].lower()} ouverts, mis à jour chaque jour.", **common))
        for d in deps:
            sub = [a for a in lst if d in a["departements"]]
            vis, caches = _publics(sub, today)
            nom = NOMS_DEP.get(d, "")
            write(f"appels-offres/{key}/{d}.html", render(
                "listing.html", root="../../", path=f"/appels-offres/{key}/{d}.html", avis=vis, visibles=len(vis), caches=caches, total=len(sub),
                metier=m, metier_key=key, dep=d, deps=[], dep_counts={}, villes=[v for v in villes if v["dep"] == d][:15],
                h1=f"Appels d'offres {m['libelle'].lower()} · {_nom_dep(d)}",
                title=f"Appels d'offres {m['libelle'].lower()} {d}{' ' + nom if nom else ''} : marchés publics en cours",
                description=f"{len(sub)} marchés publics {m['libelle'].lower()} ouverts dans le département {_nom_dep(d)}.", **common))

    for z in zones_dep:
        noms = ", ".join(metiers[k]["libelle"].lower() for k, *_ in z["par_metier"][:3])
        write(f"appels-offres/departements/{z['dep']}.html", render(
            "ville.html", root="../../", path=f"/appels-offres/departements/{z['dep']}.html", ville=z, departement=True,
            autres=[v for v in villes if v["dep"] == z["dep"]][:15], metier=metiers[z["par_metier"][0][0]], dep=z["dep"],
            h1=f"Appels d'offres publics · {z['nom']}",
            title=f"Appels d'offres {z['nom']} : {z['total']} marchés publics en cours",
            description=f"{z['total']} marchés publics en cours dans le département {z['nom']} : {noms}. Mis à jour chaque matin.", **common))

    for v in villes:
        autres = [x for x in villes if x["dep"] == v["dep"] and x is not v][:12]
        noms = ", ".join(metiers[k]["libelle"].lower() for k, _ in v["par_metier"][:3])
        v = _zone(v["dep"], v["nom"], dict(v["par_metier"]), today, slug=v["slug"])
        write(f"appels-offres/villes/{v['slug']}.html", render(
            "ville.html", root="../../", path=f"/appels-offres/villes/{v['slug']}.html", ville=v, autres=autres,
            metier=metiers[v["par_metier"][0][0]], dep=v["dep"],
            h1=f"Appels d'offres à {v['nom']} ({v['dep']})",
            title=f"Appels d'offres {v['nom']} ({v['dep']}) : {v['total']} marchés publics en cours",
            description=f"{v['total']} marchés publics en cours à {v['nom']} : {noms}. Mis à jour chaque matin.", **common))
    villes_dep = sorted(((d, [v for v in villes if v["dep"] == d]) for d in {v["dep"] for v in villes}), key=lambda x: x[0])
    write("appels-offres/villes/index.html", render(
        "ville.html", root="../../", path="/appels-offres/villes/", ville=None, par_dep=villes_dep,
        h1="Appels d'offres publics par ville", title="Appels d'offres publics par ville : marchés en cours",
        description=f"Marchés publics en cours dans {len(villes)} villes de France, classés par métier et mis à jour chaque jour.", **common))

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

    if biz.get("alerte_gratuite"):
        write("alerte-gratuite/index.html", render("alerte_gratuite.html", root="../", path="/alerte-gratuite/",
                                                   title=f"Alerte gratuite : 3 appels d'offres de votre métier par semaine · {biz['nom']}",
                                                   description="Recevez gratuitement chaque semaine 3 marchés publics de votre métier et de votre département.", **common))
    write("criteres/index.html", render("criteres.html", root="../", path="/criteres/", noindex=True,
                                        title=f"Modifier mes critères · {biz['nom']}", description="", **common))
    write("avis/index.html", render("avis.html", root="../", path="/avis/", noindex=True,
                                    title=f"Donner mon avis · {biz['nom']}", description="", **common))
    write("parrainage/index.html", render("parrainage.html", root="../", path="/parrainage/",
                                          title=f"Parrainage : 1 mois offert · {biz['nom']}",
                                          description="Parrainez un confrère : 1 mois offert pour chacun, automatiquement.", **common))
    write("rappel/index.html", render("rappel.html", root="../", path="/rappel/", noindex=True,
                                      title=f"Ajouter un rappel · {biz['nom']}", description="", **common))

    if biz.get("dossier_actif"):
        write("dossier/index.html", render("dossier.html", root="../", path="/dossier/",
                                           title=f"{biz['dossier']['nom']} : réponse aux appels d'offres rédigée pour vous · {biz['nom']}",
                                           description="Mémoire technique, lettre de candidature et DUME préparés sous 24 h : vous relisez, chiffrez, signez.", **common))
        write("dossier/envoyer/index.html", render("dossier_envoyer.html", root="../../", path="/dossier/envoyer/", noindex=True,
                                                   title=f"Envoyer mon dossier · {biz['nom']}", description="", **common))

    for name in ("mentions-legales", "cgv", "confidentialite"):
        src = (ROOT / "templates" / "legal" / f"{name}.html").read_text(encoding="utf-8")
        content = Markup(render_string(src, e=biz["editeur"], **common))
        write(f"{name}.html", render("legal.html", root="", path=f"/{name}.html", content=content, noindex=True,
                                     title=f"{name.replace('-', ' ').capitalize()} · {biz['nom']}", description="", **common))

    base = biz["site_url"].rstrip("/")
    # Les pages légales (adresse de l'éditeur) restent accessibles mais hors Google.
    pages = [p for p in pages if p not in ("mentions-legales.html", "cgv.html", "confidentialite.html", "rappel/index.html", "criteres/index.html", "avis/index.html", "dossier/envoyer/index.html")]
    urls = "".join(f"<url><loc>{base}/{p.removesuffix('index.html')}</loc><lastmod>{date.today()}</lastmod></url>" for p in pages)
    (out_dir / "sitemap.xml").write_text(
        f'<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{urls}</urlset>', encoding="utf-8")
    (out_dir / "robots.txt").write_text(f"User-agent: *\nAllow: /\nSitemap: {base}/sitemap.xml\n", encoding="utf-8")
    (out_dir / f"{indexnow_cle(biz)}.txt").write_text(indexnow_cle(biz), encoding="utf-8")
    cname = base.split("://")[-1]
    if not cname.endswith("github.io"):
        (out_dir / "CNAME").write_text(cname, encoding="utf-8")
    return {"pages": len(pages), "par_metier": {k: len(v) for k, v in by_metier.items()}}


def indexnow_cle(biz):
    """Clé IndexNow (publique par nature : Bing, Yandex, Seznam… vérifient le fichier du même nom à la racine du site)."""
    return hashlib.md5(("indexnow|" + biz["site_url"]).encode()).hexdigest()


def indexnow(biz, out_dir, session=None):
    """Signale les pages du site aux moteurs IndexNow (Bing, Yandex…) : indexation plus rapide, sans compte."""
    import re
    import requests
    base = biz["site_url"].rstrip("/")
    urls = re.findall(r"<loc>([^<]+)</loc>", (Path(out_dir) / "sitemap.xml").read_text(encoding="utf-8"))[:10000]
    cle = indexnow_cle(biz)
    r = (session or requests).post("https://api.indexnow.org/indexnow", timeout=60, json={
        "host": base.split("://")[-1], "key": cle, "keyLocation": f"{base}/{cle}.txt", "urlList": urls})
    return r.status_code, len(urls)
