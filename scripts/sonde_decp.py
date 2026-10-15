"""Sonde 2 : peut-on relier les appels d'offres BOAMP ouverts à l'historique DECP de l'acheteur (via son SIRET) ?
Compteurs, chemins de champs et quelques exemples publics seulement."""
import collections, json, re, sys, time
from pathlib import Path
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from radar import boamp  # noqa: E402
from radar.config import metiers  # noqa: E402
from radar.matching import Matcher  # noqa: E402

DECP = "https://data.economie.gouv.fr/api/explore/v2.1/catalog/datasets/decp-2022-marches-valides"
SIRET = re.compile(r"^\d{14}$")
M = Matcher(metiers())

# 1) où est le SIRET de l'acheteur dans les avis BOAMP ?
r = requests.get(f"{boamp.API}/exports/json", timeout=300, params={
    "where": "dateparution >= date'2026-09-15' and nature_libelle = \"Avis de marché\"", "limit": 3000})
recs = r.json()
paths = collections.Counter()
def walk(node, path, out):
    if isinstance(node, dict):
        for k, v in node.items():
            walk(v, path + "/" + k, out)
    elif isinstance(node, list):
        for v in node:
            walk(v, path, out)
    else:
        s = re.sub(r"\s", "", str(node))
        if SIRET.match(s):
            out.append((path, s))
siret_of = {}
for rec in recs:
    try:
        d = json.loads(rec.get("donnees") or "{}")
    except ValueError:
        continue
    found = []
    walk(d, "", found)
    for p, s in found:
        paths[p] += 1
    if found:
        siret_of[rec.get("idweb")] = found[0][1]
print("avis de marché :", len(recs), "avec un n° à 14 chiffres :", len(siret_of))
for p, n in paths.most_common(12):
    print(f"  {n:5d} {p}")

# 2) pour un échantillon, l'acheteur a-t-il un historique DECP sur 24 mois ?
ech = [rec for rec in recs if rec.get("idweb") in siret_of][:200]
avec = n_lots = 0
cpv = collections.Counter()
ex = []
t0 = time.time()
for rec in ech:
    s = siret_of[rec["idweb"]]
    q = requests.get(f"{DECP}/records", timeout=60, params={
        "where": f"acheteur_id = \"{s}\" and datenotification >= date'2024-10-15'",
        "select": "objet, codecpv, montant, offresrecues, titulaire_id_1, datenotification",
        "order_by": "datenotification desc", "limit": 100})
    rows = q.json().get("results", []) if q.ok else []
    if rows:
        avec += 1
        n_lots += len(rows)
        cpv.update((x.get("codecpv") or "")[:4] for x in rows)
        if len(ex) < 4:
            ex.append({"ouvert": (rec.get("objet") or "")[:70], "acheteur": rec.get("nomacheteur"),
                       "historique": rows[:3]})
print(f"échantillon : {len(ech)} acheteurs, avec historique DECP 24 mois : {avec}, lignes : {n_lots}, {time.time()-t0:.0f}s")
print("CPV les plus fréquents :", cpv.most_common(15))
print(json.dumps(ex, ensure_ascii=False, indent=1)[:4000])

# 3) taille d'un export DECP 24 mois
c = requests.get(f"{DECP}/records", params={"select": "count(*) as n", "where": "datenotification >= date'2024-10-15'"}, timeout=60)
print("lignes DECP 24 mois :", c.text[:80])
e = requests.get("https://recherche-entreprises.api.gouv.fr/search", params={"q": "44306698000055"}, timeout=30)
print("annuaire (nom d'un titulaire par SIRET) :", e.status_code, (e.json().get("results") or [{}])[0].get("nom_complet") if e.ok else e.text[:100])
