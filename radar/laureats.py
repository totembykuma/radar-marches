"""Pages « qui a gagné quoi » : les entreprises qui ont remporté des marchés publics dans nos métiers.

Source : données essentielles de la commande publique (DECP, data.economie.gouv.fr, Licence Ouverte),
noms et communes : annuaire officiel recherche-entreprises.api.gouv.fr. Les entrepreneurs individuels
(nature juridique 1xxx, nom d'une personne) sont exclus : seules des sociétés ont une page.
Cache des noms : data/societes.json (SIREN → [nom, nature juridique, commune, département])."""
import json
import logging
import re
import statistics
import time
import unicodedata
from collections import Counter, defaultdict
from datetime import date, timedelta

import requests

from .config import ROOT
from .historique import ANNUAIRE, DECP, _int

log = logging.getLogger(__name__)
SOCIETES = ROOT / "data" / "societes.json"
EXPORT = DECP.replace("/records", "/exports/json")
SIRET = re.compile(r"^\d{14}$")
MAX_PAGES = 6000


def slug(s):
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-")[:60] or "entreprise"


def metier_du_cpv(cpv, metiers):
    """Métier dont un début de code CPV correspond le mieux (le plus long)."""
    cpv = (cpv or "").split("-")[0]
    best, n = None, 0
    for k, m in metiers.items():
        for c in m.get("cpv") or []:
            if cpv.startswith(c) and len(c) > n:
                best, n = k, len(c)
    return best


def extraire(metiers, http=None, mois=12):
    """Marchés attribués dans nos métiers sur `mois` mois, dédoublonnés, au format compact."""
    http = http or requests
    cpvs = sorted({c for m in metiers.values() for c in (m.get("cpv") or [])})
    depuis = (date.today() - timedelta(days=30 * mois)).isoformat()
    cols = ["id", "objet", "montant", "offresrecues", "datenotification", "codecpv", "acheteur_id", "lieuexecution_code"]
    cols += [f"titulaire_{x}_{i}" for i in (1, 2, 3) for x in ("id", "typeidentifiant")]
    r = http.get(EXPORT, timeout=300, params={
        "select": ", ".join(cols),
        "where": f"datenotification >= date'{depuis}' and (" + " or ".join(f'startswith(codecpv, "{c}")' for c in cpvs) + ")"})
    r.raise_for_status()
    vus, rows = set(), []
    for x in r.json():
        tit = [str(x.get(f"titulaire_id_{i}") or "").strip() for i in (1, 2, 3)
               if str(x.get(f"titulaire_typeidentifiant_{i}") or "").upper() == "SIRET"]
        tit = [t for t in tit if SIRET.match(t)]
        k = metier_du_cpv(x.get("codecpv"), metiers)
        if not tit or not k:
            continue
        cle = (x.get("id"), x.get("acheteur_id"), x.get("datenotification"), tuple(tit))
        if cle in vus:
            continue
        vus.add(cle)
        lieu = str(x.get("lieuexecution_code") or "").strip()
        rows.append({"d": (x.get("datenotification") or "")[:10], "o": re.sub(r"\s+", " ", (x.get("objet") or "")).strip()[:140],
                     "m": _int(x.get("montant")), "n": _int(x.get("offresrecues")), "a": str(x.get("acheteur_id") or "").strip(),
                     "l": lieu if re.match(r"^(\d{2,3}|2A|2B)$", lieu) else "", "k": k, "t": tit})
    rows.sort(key=lambda r: r["d"], reverse=True)
    return rows


class Annuaire:
    """SIREN → [nom, nature juridique, commune, département], avec cache et budget d'appels."""
    def __init__(self, http=None):
        self.http = http or requests
        try:
            self.cache = json.loads(SOCIETES.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.cache = {}
        self.appels = 0

    def get(self, siret):
        return self.cache.get(siret[:9])

    def chercher(self, siret):
        siren = siret[:9]
        for essai in range(3):
            try:
                time.sleep(0.16)   # limite de l'annuaire : 7 requêtes par seconde
                r = self.http.get(ANNUAIRE, params={"q": siren, "per_page": 1}, timeout=20)
                self.appels += 1
                if r.status_code == 429:
                    time.sleep(2 + 3 * essai)
                    continue
                res = (r.json().get("results") or [{}])[0] if r.ok else {}
                if res.get("siren") != siren:
                    self.cache[siren] = ["", "", "", ""]
                else:
                    siege = res.get("siege") or {}
                    self.cache[siren] = [(res.get("nom_raison_sociale") or res.get("nom_complet") or "").strip(),
                                         str(res.get("nature_juridique") or ""), (siege.get("libelle_commune") or "").strip(),
                                         str(siege.get("departement") or "")]
                return
            except Exception as exc:
                log.warning("Annuaire indisponible : %s", exc)
                return

    def completer(self, sirets, budget):
        """Cherche les SIRET inconnus, dans l'ordre donné, dans la limite du budget."""
        n = 0
        for s in sirets:
            if n >= budget:
                break
            if s[:9] not in self.cache:
                self.chercher(s)
                n += 1
        return n

    def sauver(self):
        try:
            SOCIETES.write_text(json.dumps(self.cache, ensure_ascii=False, separators=(",", ":"), sort_keys=True), encoding="utf-8")
        except OSError as exc:
            log.warning("Cache des sociétés non enregistré : %s", exc)


def societe(info):
    """Une vraie société affichable (pas un entrepreneur individuel, nom connu)."""
    return bool(info and info[0] and not info[1].startswith("1"))


def charger(metiers, budget=0, http=None, mois=12):
    """Récupère les marchés, complète les noms (budget d'appels à l'annuaire) et prépare les fiches entreprises."""
    rows = extraire(metiers, http=http, mois=mois)
    ann = Annuaire(http=http)
    nb = Counter(t for r in rows for t in r["t"])
    titulaires = [s for s, _ in nb.most_common()]
    if budget:
        fait = ann.completer(titulaires, int(budget * 0.7))
        retenus = {s[:9] for s in titulaires if societe(ann.get(s))}
        acheteurs = Counter(r["a"] for r in rows if SIRET.match(r["a"]) and any(t[:9] in retenus for t in r["t"]))
        ann.completer([a for a, _ in acheteurs.most_common()], budget - fait)
        ann.sauver()
    return preparer(rows, ann, metiers)


def preparer(rows, ann, metiers, max_pages=MAX_PAGES):
    par_siren = defaultdict(list)
    for r in rows:
        for t in dict.fromkeys(x[:9] for x in r["t"]):
            par_siren[t].append(r)
    fiches = []
    for siren, lst in par_siren.items():
        info = ann.cache.get(siren)
        if not societe(info):
            continue
        fiches.append({"siren": siren, "nom": info[0], "commune": info[2].title(), "dep": info[3], "marches": lst})
    fiches.sort(key=lambda f: (-len(f["marches"]), f["nom"]))
    fiches = fiches[:max_pages]
    vus = Counter()
    for f in fiches:
        s = slug(f["nom"])
        f["slug"] = f"{s}-{f['siren']}"
        vus[s] += 1
        f["metiers"] = [k for k, _ in Counter(r["k"] for r in f["marches"]).most_common()]
        f["deps"] = [d for d, _ in Counter(r["l"] for r in f["marches"] if r["l"]).most_common()]
        f["total"] = sum(r["m"] for r in f["marches"] if r["m"] and r["m"] < 50_000_000)
        f["depuis"] = min(r["d"] for r in f["marches"])
        for r in f["marches"]:
            info = ann.cache.get(r["a"][:9]) or ["", "", "", ""]
            r["acheteur"] = info[0].title() if info[0] else ""
    # Concurrents : mêmes métier et département principaux.
    groupes = defaultdict(list)
    for f in fiches:
        if f["metiers"] and f["deps"]:
            groupes[(f["metiers"][0], f["deps"][0])].append(f)
    for f in fiches:
        g = groupes.get((f["metiers"][0], f["deps"][0])) if f["metiers"] and f["deps"] else []
        f["concurrents"] = [x for x in (g or []) if x is not f][:8]
    # Repères par métier (12 mois) : utiles aux pages entreprises, au baromètre et aux posts.
    reperes = {}
    for k in metiers:
        lst = [r for r in rows if r["k"] == k]
        mt = [r["m"] for r in lst if r["m"] and 0 < r["m"] < 50_000_000]
        of = [r["n"] for r in lst if r["n"] and 0 < r["n"] < 200]
        reperes[k] = {"n": len(lst), "montant_median": int(statistics.median(mt)) if mt else None,
                      "offres_median": round(statistics.median(of), 1) if of else None}
    return {"fiches": fiches, "reperes": reperes, "nb_marches": len(rows), "mois": 12}
