"""Sonde : les données essentielles des marchés (DECP, data.economie.gouv.fr) couvrent-elles mieux
l'historique des acheteurs que le BOAMP ? Compteurs et noms de champs seulement."""
import json, sys
import requests

BASE = "https://data.economie.gouv.fr/api/explore/v2.1/catalog/datasets"
for ds in ("decp-v3-marches-valides", "decp_augmente", "decp-2022-marches-valides"):
    r = requests.get(f"{BASE}/{ds}/records", params={"limit": 1, "order_by": "datenotification desc"}, timeout=60)
    print(ds, r.status_code)
    if r.ok:
        j = r.json()
        print("  total :", j.get("total_count"))
        rec = (j.get("results") or [{}])[0]
        print("  champs :", sorted(rec.keys()))
        print("  exemple :", json.dumps({k: rec[k] for k in list(rec)[:25]}, ensure_ascii=False)[:1500])
        r2 = requests.get(f"{BASE}/{ds}/records", params={"select": "count(*) as n",
                          "where": "datenotification >= date'2025-10-15'"}, timeout=60)
        print("  12 derniers mois :", r2.status_code, r2.text[:200])
r = requests.get("https://www.data.gouv.fr/api/1/datasets/", params={"q": "decp", "page_size": 5}, timeout=60)
for d in r.json().get("data", []):
    print("data.gouv :", d["title"], d["page"])
