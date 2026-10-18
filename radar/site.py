"""Génère le site statique : page de vente + pages SEO par métier et département, mises à jour chaque jour."""
import hashlib
import json
import re
import shutil
from collections import Counter, defaultdict
from datetime import date, timedelta
from pathlib import Path

import yaml
from markupsafe import Markup

from . import avis as avis_mod
from . import barometre as baro_mod
from . import comptes, linkedin
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


FAUX_MOTS = ("prestations travaux entretien maintenance fourniture pose rénovation réhabilitation services marché accord-cadre "
             "lot bâtiments communaux site groupe scolaire équipements locaux voirie réseaux ensemble mise conformité").split()


def _flou(a, key, today):
    """Carte « floutée » d'un avis récent : métier, département, date limite et montant publié seulement.
    L'objet et l'acheteur réels ne figurent PAS dans la page (faux texte flouté), on ne peut donc pas les lire en retirant le flou."""
    h = hashlib.md5(str(a.get("id") or a.get("objet")).encode()).digest()
    faux = lambda n, o: " ".join(FAUX_MOTS[(h[(o + i) % 16] + i) % len(FAUX_MOTS)] for i in range(n)).capitalize()
    return {"metier": key, "departements": a.get("departements") or [], "jours_restants": a.get("jours_restants"),
            "date_limite_fr": a.get("date_limite_fr") or "", "pme": bool(a.get("pme")),
            "montant": _euros(a.get("montant_estime")) if a.get("montant_estime") else "",
            "faux_objet": faux(7 + h[0] % 5, 1), "faux_acheteur": faux(3, 9)}


def _recents(lst, today, key, n=3):
    seuil = (today - timedelta(days=RECENTS_JOURS - 1)).isoformat()
    return [_flou(a, key, today) for a in lst if (a.get("date_parution") or "") >= seuil
            and (a.get("jours_restants") is None or a["jours_restants"] >= 0)][:n]


def _apercu(by_metier, metiers, today):
    """Outil de découverte de l'accueil : vrais nombres de marchés ouverts par métier et département (rien d'inventé).
    {métier: [total, dont récents réservés aux abonnés, {dép: total}, {dép: récents}]}"""
    seuil = (today - timedelta(days=RECENTS_JOURS - 1)).isoformat()
    out = {}
    for k in metiers:
        lst = [a for a in by_metier.get(k, []) if a.get("jours_restants") is None or a["jours_restants"] >= 0]
        rec = [a for a in lst if (a.get("date_parution") or "") >= seuil]
        out[k] = [len(lst), len(rec), dict(Counter(d for a in lst for d in a["departements"] if d)),
                  dict(Counter(d for a in rec for d in a["departements"] if d))]
    return out


def _nom_dep(d):
    return f"{NOMS_DEP[d]} ({d})" if d in NOMS_DEP else f"département {d}"


MOIS = ["janv.", "févr.", "mars", "avr.", "mai", "juin", "juil.", "août", "sept.", "oct.", "nov.", "déc."]


def _film_scenes():
    """Texte (config/film.yaml) et minutage de la voix off (config/film_temps.json, écrit par scripts/film_audio.py)."""
    from .config import ROOT
    scenes = yaml.safe_load((ROOT / "config" / "film.yaml").read_text(encoding="utf-8"))["scenes"]
    p = ROOT / "config" / "film_temps.json"
    temps = json.loads(p.read_text(encoding="utf-8"))["scenes"] if p.exists() else {}
    out = {}
    for s in scenes:
        t = temps.get(s["id"]) or {"ms": round(1000 * (1.1 + sum(len(x) / 15.5 + .38 for x in s["voix"]))), "marks": []}
        marks = list(t["marks"])
        if len(marks) != len(s["voix"]):   # texte changé ou voix pas encore enregistrée : estimation
            pos, marks = 700, []
            for x in s["voix"]:
                marks.append(pos)
                pos += round(1000 * (len(x) / 15.5 + .38))
        out[s["id"]] = {"titre": s["titre"], "cap": s["cap"], "ms": t["ms"], "m": [m / 1000 for m in marks]}
    return out


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
    sc = _film_scenes()
    return {"metier": key, "dep": dep, "avis": a, "autre": autres[0] if autres else None, "n_dep": 1 + len(autres),
            "sc": sc, "duree": round(sum(x["ms"] for x in sc.values()) / 1000),
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


def _vitrine(by_metier, today, n_flous=4, n_clairs=2):
    """Accueil, 2e section : les derniers marchés, mêlant récents floutés (abonnés) et plus anciens en clair."""
    vus, flous, clairs = set(), [], []
    for k, lst in sorted(by_metier.items(), key=lambda kv: -len(kv[1])):
        vis, _ = _publics(lst, today)
        for f in _recents(lst, today, k, 2):
            flous.append(f)
        for a in vis:
            if id(a) not in vus and (a.get("jours_restants") or 0) >= 7 and len([c for c in clairs if c[0] == k]) < 1:
                vus.add(id(a)); clairs.append((k, a))
                break
    seuil = (today - timedelta(days=RECENTS_JOURS - 1)).isoformat()
    nb = len({id(a) for lst in by_metier.values() for a in lst if (a.get("date_parution") or "") >= seuil})
    flous, clairs = flous[:n_flous], clairs[:n_clairs]
    cartes = []
    while flous or clairs:
        if clairs:
            cartes.append(("clair", *clairs.pop(0)))
        cartes += [("flou", f["metier"], f) for f in flous[:2]]
        flous = flous[2:]
    return {"cartes": cartes, "recents": nb} if cartes else None


def _zone(dep, nom, par_metier, today, slug=None):
    """Ville ou département : avis par métier, sans les récents (réservés aux abonnés)."""
    uniques = {id(a): a for lst in par_metier.values() for a in lst}
    acheteurs = Counter(a["acheteur"] for a in uniques.values() if a.get("acheteur"))
    pm = [(k, *_publics(lst, today)) for k, lst in sorted(par_metier.items(), key=lambda kv: -len(kv[1]))]
    return {"nom": nom, "dep": dep, "slug": slug, "total": len(uniques), "par_metier": pm,
            "caches": len(uniques) - len({id(a) for _, vis, _ in pm for a in vis}),
            "flous": [f for k, lst in par_metier.items() for f in _recents(lst, today, k, 1)][:3],
            "acheteurs": [n for n, _ in acheteurs.most_common(5)]}


def _arrondi(d):
    """Tracé allégé pour la carte décorative de l'accueil : 1 point sur 2, coordonnées entières."""
    out = []
    for sous in re.findall(r"M[^MZ]*Z?", d):
        pts = re.findall(r"(-?\d+\.?\d*),(-?\d+\.?\d*)", sous)
        pts = [pts[0]] + pts[1:-1:2] + [pts[-1]] if len(pts) > 6 else pts
        out.append("M" + "L".join(f"{round(float(x))},{round(float(y))}" for x, y in pts) + "Z")
    return "".join(out)


def _radar_accueil(by_metier):
    """Carte de France de l'accueil : départements de métropole + un point par département ayant des marchés ouverts."""
    n = Counter(d for lst in by_metier.values() for a in {id(a): a for a in lst}.values() for d in a["departements"] if d)
    deps, points = [], []
    for d in CARTE["departements"]:
        nums = [float(x) for x in re.findall(r"-?\d+\.?\d*", d["d"])]
        xs, ys = nums[0::2], nums[1::2]
        if max(xs) >= 630:  # outre-mer, dessiné en encart sur la vraie carte
            continue
        deps.append(_arrondi(d["d"]))
        if n.get(d["code"]):
            points.append({"x": round((min(xs) + max(xs)) / 2), "y": round((min(ys) + max(ys)) / 2), "n": n[d["code"]], "nom": d["nom"]})
    top = max((p["n"] for p in points), default=1)
    for i, p in enumerate(sorted(points, key=lambda p: -p["n"])):
        p["r"] = round(2.5 + 6 * (p["n"] / top) ** .5, 1)
        p["delai"] = round((i * 0.37) % 4, 2)
    return {"deps": deps, "points": points}


def build(avis_open, biz, metiers, matcher, out_dir: Path, stats=None, histo=None, laureats=None):
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
    exemple = [a for a in _publics(by_metier.get(ex_key, []), today)[0] if (a.get("jours_restants") or 0) > 0][:3]   # jamais un récent (réservé)
    film = _film(by_metier, histo)
    vitrine = _vitrine(by_metier, today)
    avis_clients = avis_mod.resume(avis_mod.charger())
    apercu = _apercu(by_metier, metiers, today)
    write("index.html", render("index.html", root="", path="/", stats=stats, counts=counts, apercu=apercu,
                               noms_dep=NOMS_DEP, comptes_actifs=bool(comptes.config_publique()),
                               ex_key=ex_key, exemple=exemple, film=film, vitrine=vitrine, avis_clients=avis_clients, radar=_radar_accueil(by_metier),
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
            "listing.html", root="../../", path=f"/appels-offres/{key}/", avis=vis[:150], visibles=len(vis), caches=caches, total=len(lst), flous=_recents(lst, today, key),
            metier=m, metier_key=key, dep=None, deps=deps, dep_counts=dep_counts, noms_dep=NOMS_DEP,
            carte_n=dep_counts, carte_liens={d: f"{d}.html" for d in deps}, h1=f"Appels d'offres {m['libelle'].lower()} en cours",
            title=f"Appels d'offres {m['libelle'].lower()} : marchés publics en cours ({date_fr()})",
            description=f"{len(lst)} marchés publics {m['libelle'].lower()} ouverts, mis à jour chaque jour.", **common))
        for d in deps:
            sub = [a for a in lst if d in a["departements"]]
            vis, caches = _publics(sub, today)
            nom = NOMS_DEP.get(d, "")
            write(f"appels-offres/{key}/{d}.html", render(
                "listing.html", root="../../", path=f"/appels-offres/{key}/{d}.html", avis=vis, visibles=len(vis), caches=caches, total=len(sub), flous=_recents(sub, today, key),
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
    conf = comptes.config_publique()
    if conf:   # comptes avec mot de passe (Supabase) : connexion, inscription, mot de passe oublié, retour des liens e-mail
        cjs = compte_js(biz, metiers, conf)
        for rel, vue, titre in (("compte/", "espace", "Mon compte"), ("compte/inscription/", "inscription", "Créer mon compte"),
                                ("compte/oubli/", "oubli", "Mot de passe oublié"), ("compte/confirmation/", "confirmation", "Confirmation")):
            write(f"{rel}index.html", render("compte.html", root="../" * rel.count("/"), path=f"/{rel}", noindex=True, vue=vue,
                                             comptes_conf=conf, compte_js=cjs, title=f"{titre} · {biz['nom']}", description="", **common))
    else:
        write("compte/index.html", render("compte.html", root="../", path="/compte/", noindex=True,
                                          title=f"Mon compte · {biz['nom']}", description="", **common))
    write("merci/index.html", render("merci.html", root="../", path="/merci/", noindex=True,
                                     title=f"Merci, votre veille est active · {biz['nom']}", description="", **common))
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

    baros = baro_mod.tous()
    if laureats and laureats.get("fiches"):
        _entreprises(write, common, laureats, metiers)
    if baros:
        _barometres(write, common, baros, metiers)
    publics = {k: _publics(v, today)[0] for k, v in by_metier.items()}
    posts = linkedin.semaine(biz, metiers, publics, laureats=laureats, barometre=baros[0] if baros else None, today=today)
    (out_dir / "linkedin").mkdir(exist_ok=True)
    (out_dir / "linkedin" / "semaine.json").write_text(json.dumps(posts, ensure_ascii=False, indent=1), encoding="utf-8")
    (out_dir / "linkedin" / "flux.xml").write_text(linkedin.flux_rss(posts, biz, today=today), encoding="utf-8")

    base = biz["site_url"].rstrip("/")
    # Les pages légales (adresse de l'éditeur) restent accessibles mais hors Google.
    pages = [p for p in pages if p not in ("mentions-legales.html", "cgv.html", "confidentialite.html", "rappel/index.html", "merci/index.html", "compte/index.html", "compte/inscription/index.html", "compte/oubli/index.html", "compte/confirmation/index.html", "criteres/index.html", "avis/index.html", "dossier/envoyer/index.html")]
    urls = "".join(f"<url><loc>{base}/{p.removesuffix('index.html')}</loc><lastmod>{date.today()}</lastmod></url>" for p in pages)
    (out_dir / "sitemap.xml").write_text(
        f'<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{urls}</urlset>', encoding="utf-8")
    shutil.copy(ROOT / "static" / "icons" / "favicon.ico", out_dir / "favicon.ico")   # Google cherche aussi /favicon.ico
    (out_dir / "robots.txt").write_text(f"User-agent: *\nAllow: /\nSitemap: {base}/sitemap.xml\n", encoding="utf-8")
    (out_dir / f"{indexnow_cle(biz)}.txt").write_text(indexnow_cle(biz), encoding="utf-8")
    cname = base.split("://")[-1]
    if not cname.endswith("github.io"):
        (out_dir / "CNAME").write_text(cname, encoding="utf-8")
    return {"pages": len(pages), "par_metier": {k: len(v) for k, v in by_metier.items()}}


def compte_js(biz, metiers, conf):
    """Paramètres publics de l'espace client (aucune donnée personnelle, aucune clé secrète)."""
    from .comptes import DCE_MAX_OCTETS
    d = biz.get("dossier") or {}
    from .paliers import texte_grille
    offres = {k: {"nom": o["nom"], "prix": o["prix_mensuel"], "annuel": o.get("prix_annuel"), "lien": o["lien_paiement"],
                  "lien_annuel": o.get("lien_annuel"), "grille": texte_grille(o)}
              for k, o in biz["offres"].items() if o.get("lien_paiement") and (k != "cle" or biz.get("cle_actif"))}
    return {"url": conf["url"], "cle": conf["cle"], "site": biz["site_url"].rstrip("/"), "root": "/", "offres": offres,
            "essai": biz.get("essai_gratuit_jours", 7), "portail": biz.get("portail_client") or "",
            "metiers": [[k, m["libelle"]] for k, m in sorted(metiers.items(), key=lambda x: x[1]["libelle"])],
            "metiers_nom": {k: m["libelle"] for k, m in metiers.items()}, "max_octets": DCE_MAX_OCTETS,
            "dossier": {"prix": d.get("prix"), "prix_pro": d.get("prix_pro"), "prix_cle": d.get("prix_cle"), "lien": d.get("lien_paiement"),
                        "lien_pro": d.get("lien_pro"), "lien_cle": d.get("lien_cle"), "delai": d.get("delai_min_jours", 5)}
            if biz.get("dossier_actif") else None}


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


def _euros(n):
    return f"{n:,}".replace(",", " ") + " €" if n else ""


def _entreprises(write, common, data, metiers):
    """Une page par entreprise lauréate, une page par département et un sommaire."""
    fiches, reperes = data["fiches"], data["reperes"]
    for f in fiches:
        m = metiers.get(f["metiers"][0]) if f["metiers"] else None
        ville = f" ({f['commune']})" if f["commune"] else ""
        write(f"entreprises/{f['slug']}.html", render(
            "entreprise.html", root="../", path=f"/entreprises/{f['slug']}.html", f=f, metier=m, metier_key=f["metiers"][0] if m else None,
            dep=f["deps"][0] if f["deps"] else f["dep"], reperes=reperes, euros=_euros, nom_dep=_nom_dep, date_lim=date_limite_fr,
            h1=f"{f['nom']} : marchés publics remportés",
            title=f"{f['nom']}{ville} : {len(f['marches'])} marché{'s' if len(f['marches']) > 1 else ''} public{'s' if len(f['marches']) > 1 else ''} remporté{'s' if len(f['marches']) > 1 else ''}",
            description=f"Les marchés publics attribués à {f['nom']}{ville} : acheteurs, montants, nombre d'offres reçues. Données officielles mises à jour chaque jour.",
            **common))
    par_dep = defaultdict(list)
    for f in fiches:
        if f["dep"]:
            par_dep[f["dep"]].append(f)
    for d, lst in par_dep.items():
        write(f"entreprises/departements/{d}.html", render(
            "entreprises.html", root="../../", path=f"/entreprises/departements/{d}.html", liste=lst, dep=d, nom_dep=_nom_dep, deps=[],
            metier=None, euros=_euros, h1=f"Entreprises lauréates de marchés publics · {_nom_dep(d)}",
            title=f"Qui a gagné les marchés publics · {_nom_dep(d)} : {len(lst)} entreprises",
            description=f"Les {len(lst)} entreprises du département {_nom_dep(d)} qui ont remporté des marchés publics ces 12 derniers mois, avec acheteurs et montants.",
            **common))
    par_metier = {k: [f for f in fiches if f["metiers"] and f["metiers"][0] == k][:12] for k in metiers}
    write("entreprises/index.html", render(
        "entreprises.html", root="../", path="/entreprises/", liste=None, par_metier=par_metier, dep=None, nom_dep=_nom_dep,
        deps=sorted((d, len(v)) for d, v in par_dep.items()), nb=len(fiches), nb_marches=data["nb_marches"], reperes=reperes,
        metier=None, euros=_euros, h1="Qui a gagné quoi : les entreprises lauréates des marchés publics",
        title="Qui a gagné les marchés publics ? Entreprises lauréates par métier et département",
        description=f"{len(fiches)} entreprises qui ont remporté des marchés publics ces 12 derniers mois : acheteurs, montants, concurrents. Données officielles.",
        **common))


def _barometres(write, common, baros, metiers):
    from .render import MOIS
    for i, b in enumerate(baros):
        a, m = b["mois"].split("-")
        b["titre_mois"] = f"{MOIS[int(m) - 1]} {a}"
    for i, b in enumerate(baros):
        rows = []
        for k, v in b["par_metier"].items():
            if k in metiers:
                avant = (b.get("precedent") or {}).get("par_metier", {}).get(k)
                rows.append({"k": k, "n": v["n"], "pme": v["pme"], "var": baro_mod.variation(v["n"], avant), "top_deps": v["top_deps"],
                             "rep": (b.get("reperes") or {}).get(k) or {}})
        rows.sort(key=lambda r: -r["n"])
        var = baro_mod.variation(b["appels"], (b.get("precedent") or {}).get("appels"))
        ctx = dict(b=b, rows=rows, var=var, archives=baros, nom_dep=_nom_dep, euros=_euros,
                   title=f"Baromètre des appels d'offres publics · {b['titre_mois']}",
                   description=f"{b['appels']} appels d'offres publiés en {b['titre_mois']} dans 17 métiers : chiffres par métier, région et département, évolution sur un mois.")
        write(f"barometre/{b['mois']}.html", render("barometre.html", root="../", path=f"/barometre/{b['mois']}.html",
                                                     h1=f"Baromètre des appels d'offres · {b['titre_mois']}", **ctx, **common))
        if i == 0:
            write("barometre/index.html", render("barometre.html", root="../", path="/barometre/",
                                                 h1=f"Baromètre des appels d'offres publics · {b['titre_mois']}", **ctx, **common))
