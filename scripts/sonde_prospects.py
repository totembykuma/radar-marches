"""Sonde : peut-on trouver automatiquement des e-mails d'entreprises à prospecter ?
N'affiche que des compteurs et des chemins de champs (aucune adresse : le dépôt est public)."""
import collections, json, re
from datetime import date, timedelta
import requests

API = "https://boamp-datadila.opendatasoft.com/api/explore/v2.1/catalog/datasets/boamp/exports/json"
EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
since = (date.today() - timedelta(days=30)).isoformat()
agg = requests.get(API.replace("/exports/json", "/records"), params={
    "select": "nature_libelle, count(*) as n", "group_by": "nature_libelle",
    "where": f"dateparution >= date'{since}'", "limit": 50}, timeout=60).json()
print("natures (30 j) :", [(x.get("nature_libelle"), x.get("n")) for x in agg.get("results", [])])
natures = [x["nature_libelle"] for x in agg.get("results", []) if x.get("nature_libelle") in ("Résultat de marché",)]
recs = []
for nat in natures:
    r = requests.get(API, params={"where": f"dateparution >= date'{since}' and nature_libelle = \"{nat}\"", "limit": 3000}, timeout=120)
    r.raise_for_status()
    recs += r.json()
print("avis d'attribution (30 j) :", len(recs))
paths = collections.Counter()
with_winner_email, winner_emails, buyer_like = 0, set(), 0

def walk(node, path, out):
    if isinstance(node, dict):
        for k, v in node.items():
            walk(v, path + "/" + k, out)
    elif isinstance(node, list):
        for v in node:
            walk(v, path, out)
    elif isinstance(node, str):
        for m in EMAIL.findall(node):
            out.append((path, m.lower()))

for rec in recs:
    try:
        data = json.loads(rec.get("donnees") or "{}")
    except ValueError:
        continue
    found = []
    walk(data, "", found)
    acheteur_emails = set()
    got = False
    for p, m in found:
        generic = re.sub(r"\d+", "#", p)
        paths[generic] += 1
        if re.search(r"titulaire|attributaire|tenderer|winner|lauréat|laureat|TITULAIRE|ATTRIBUT", p, re.I):
            winner_emails.add(m); got = True
    with_winner_email += got
print("avis avec e-mail de titulaire :", with_winner_email)
print("e-mails de titulaires distincts :", len(winner_emails))
print("domaines distincts :", len({e.split('@')[1] for e in winner_emails}))
print("chemins de champs contenant des e-mails (top 25) :")
for p, n in paths.most_common(25):
    print(f"  {n:5d}  {p}")

r2 = requests.get("https://recherche-entreprises.api.gouv.fr/search",
                  params={"activite_principale": "81.21Z", "departement": "91", "per_page": 1}, timeout=30)
print("annuaire entreprises 81.21Z en 91 :", r2.status_code, r2.json().get("total_results") if r2.ok else r2.text[:200])
if r2.ok and r2.json().get("results"):
    print("champs disponibles :", sorted(r2.json()["results"][0].keys()))
