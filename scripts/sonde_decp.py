"""Sonde ponctuelle : champs des données essentielles et de l'annuaire (résumé court)."""
import json, requests
D = "https://data.economie.gouv.fr/api/explore/v2.1/catalog/datasets/decp-2022-marches-valides"
r = requests.get(D + "/records", params={"limit": 1, "order_by": "datenotification desc"}, timeout=60).json()
rec = (r.get("results") or [{}])[0]
print("CHAMPS", json.dumps({k: (str(v)[:60] if v is not None else None) for k, v in rec.items()}, ensure_ascii=False))
w = 'datenotification >= date\'2025-10-01\' and (startswith(codecpv, "9090") or startswith(codecpv, "4531"))'
r = requests.get(D + "/records", params={"where": w, "limit": 0}, timeout=60).json()
print("TOTAL_12M_2METIERS", r.get("total_count"))
r = requests.get(D + "/records", params={"select": "count(*) as n", "group_by": "year(datenotification) as y, month(datenotification) as m",
     "where": "datenotification >= date'2026-03-01'", "limit": 20}, timeout=60).json()
print("PAR_MOIS", r.get("results"))
s = requests.get("https://recherche-entreprises.api.gouv.fr/search", params={"q": "35620029500224", "per_page": 1}, timeout=30).json()
res = (s.get("results") or [{}])[0]
print("ANNUAIRE", {k: res.get(k) for k in ("nom_raison_sociale", "nom_complet", "nature_juridique", "siren", "section_activite_principale", "activite_principale")},
      {k: (res.get("siege") or {}).get(k) for k in ("libelle_commune", "departement", "code_postal", "siret")})
B = "https://boamp-datadila.opendatasoft.com/api/explore/v2.1/catalog/datasets/boamp/records"
r = requests.get(B, params={"select": "count(*) as n", "where": "dateparution >= date'2026-09-01' and dateparution < date'2026-10-01'", "limit": 1}, timeout=60).json()
print("BOAMP_SEPT", r.get("results"))
