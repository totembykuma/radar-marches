"""Sonde : pour les appels d'offres ouverts, peut-on retrouver ce que le MÊME acheteur a déjà attribué
(même métier, 12 derniers mois) : gagnant et montant ? N'affiche que des compteurs et quelques exemples publics."""
import collections, json, sys, time
from datetime import date, timedelta
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from radar import boamp, prospects  # noqa: E402
from radar.config import metiers  # noqa: E402
from radar.matching import Matcher  # noqa: E402
from radar.textutil import norm  # noqa: E402

M = Matcher(metiers())
t0 = time.time()
hist = collections.defaultdict(list)   # (acheteur, metier) -> attributions
n_res = n_montant = n_gagnant = 0
fin = date.today()
for mois in range(12):
    debut = fin - timedelta(days=30)
    r = requests.get(f"{boamp.API}/exports/json", timeout=300, params={
        "where": f"dateparution >= date'{debut}' and dateparution < date'{fin}' and nature_libelle = \"Résultat de marché\""})
    r.raise_for_status()
    for rec in r.json():
        a = boamp.normalize(rec)
        n_res += 1
        mt, ti = prospects.montant(rec), prospects.titulaires(rec)
        n_montant += mt is not None
        n_gagnant += bool(ti)
        for m in M.metiers_of(a):
            hist[(norm(a["acheteur"]), m)].append({"date": a["date_parution"][:10], "objet": a["objet"][:80],
                                                  "montant": mt, "gagnants": ti[:3]})
    fin = debut
print(f"résultats de marché sur 12 mois : {n_res} (avec montant {n_montant}, avec gagnant {n_gagnant}) en {time.time()-t0:.0f}s")

ouverts = [a for a in boamp.fetch_open(days=30) if not M.is_award_notice(a)]
alertes = avec = avec_montant = 0
exemples = []
for a in ouverts:
    for m in M.metiers_of(a):
        alertes += 1
        h = hist.get((norm(a["acheteur"]), m))
        if h:
            avec += 1
            avec_montant += any(x["montant"] for x in h)
            if len(exemples) < 8 and any(x["montant"] and x["gagnants"] for x in h):
                exemples.append({"ouvert": a["objet"][:80], "acheteur": a["acheteur"][:60], "metier": m, "avant": h[:2]})
print(f"alertes (avis ouverts x métier, 30 j) : {alertes}")
print(f"  avec un marché passé du même acheteur et même métier : {avec} ({100*avec//max(alertes,1)} %)")
print(f"  dont au moins un montant connu : {avec_montant} ({100*avec_montant//max(alertes,1)} %)")
print(json.dumps(exemples, ensure_ascii=False, indent=1))
