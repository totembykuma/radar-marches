"""Baromètre mensuel des appels d'offres : chiffres du mois par métier, région et département.

Calculé le 1er de chaque mois (workflow barometre.yml) sur les avis du BOAMP du mois écoulé,
enregistré dans data/barometre/AAAA-MM.json (quelques Ko), publié sur /barometre/ par le site."""
import json
from collections import Counter
from datetime import date, timedelta

from . import boamp
from .config import ROOT

DOSSIER = ROOT / "data" / "barometre"
REGIONS = {
    "Auvergne-Rhône-Alpes": "01 03 07 15 26 38 42 43 63 69 73 74",
    "Bourgogne-Franche-Comté": "21 25 39 58 70 71 89 90",
    "Bretagne": "22 29 35 56",
    "Centre-Val de Loire": "18 28 36 37 41 45",
    "Corse": "2A 2B 20",
    "Grand Est": "08 10 51 52 54 55 57 67 68 88",
    "Hauts-de-France": "02 59 60 62 80",
    "Île-de-France": "75 77 78 91 92 93 94 95",
    "Normandie": "14 27 50 61 76",
    "Nouvelle-Aquitaine": "16 17 19 23 24 33 40 47 64 79 86 87",
    "Occitanie": "09 11 12 30 31 32 34 46 48 65 66 81 82",
    "Pays de la Loire": "44 49 53 72 85",
    "Provence-Alpes-Côte d'Azur": "04 05 06 13 83 84",
    "Outre-mer": "971 972 973 974 976",
}
REGION_DE = {d: r for r, ds in REGIONS.items() for d in ds.split()}


def mois_precedent(d=None):
    d = (d or date.today()).replace(day=1) - timedelta(days=1)
    return d.strftime("%Y-%m")


def bornes(mois):
    debut = date.fromisoformat(mois + "-01")
    fin = (debut + timedelta(days=32)).replace(day=1) - timedelta(days=1)
    return debut, fin


def compter(mois, metiers, matcher, session=None):
    debut, fin = bornes(mois)
    avis = boamp.fetch_since(debut, fin, session=session)
    appels = [a for a in avis if not matcher.is_award_notice(a)]
    par_metier, par_dep, par_region, pme, acheteurs = Counter(), {}, Counter(), Counter(), Counter()
    total = 0
    for a in appels:
        ms = a.get("metiers") or matcher.metiers_of(a)
        if not ms:
            continue
        total += 1
        deps = [d for d in a.get("departements") or [] if d]
        for r in {REGION_DE.get(d) for d in deps} - {None}:
            par_region[r] += 1
        if a.get("acheteur"):
            acheteurs[a["acheteur"][:80]] += 1
        for m in ms:
            par_metier[m] += 1
            pme[m] += bool(a.get("pme"))
            for d in deps:
                par_dep.setdefault(m, Counter())[d] += 1
    return {"mois": mois, "avis_boamp": len(avis), "appels": total,
            "par_metier": {m: {"n": par_metier[m], "pme": pme[m], "top_deps": par_dep.get(m, Counter()).most_common(5)} for m in metiers},
            "par_region": par_region.most_common(),
            "top_deps": Counter({d: n for c in par_dep.values() for d, n in c.items()}).most_common(10),
            "top_acheteurs": acheteurs.most_common(10)}


def calculer(mois, metiers, matcher, reperes=None, session=None):
    """Chiffres du mois, comparés au mois précédent (relu s'il est déjà enregistré)."""
    cur = compter(mois, metiers, matcher, session=session)
    prev_mois = mois_precedent(bornes(mois)[0])
    prev = charger_mois(prev_mois) or compter(prev_mois, metiers, matcher, session=session)
    cur["precedent"] = {"mois": prev_mois, "appels": prev["appels"],
                        "par_metier": {m: v["n"] for m, v in prev["par_metier"].items()}}
    cur["reperes"] = reperes or {}
    cur["calcule_le"] = date.today().isoformat()
    return cur


def charger_mois(mois):
    try:
        return json.loads((DOSSIER / f"{mois}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def enregistrer(b):
    DOSSIER.mkdir(parents=True, exist_ok=True)
    (DOSSIER / f"{b['mois']}.json").write_text(json.dumps(b, ensure_ascii=False, indent=0), encoding="utf-8")


def tous():
    """Tous les baromètres enregistrés, du plus récent au plus ancien."""
    out = []
    for p in sorted(DOSSIER.glob("*.json"), reverse=True):
        try:
            out.append(json.loads(p.read_text(encoding="utf-8")))
        except ValueError:
            pass
    return out


def variation(n, avant):
    if not avant:
        return None
    return round(100 * (n - avant) / avant)
