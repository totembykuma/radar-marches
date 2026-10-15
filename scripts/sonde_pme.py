"""Sonde : quels champs BOAMP permettent de repérer les marchés accessibles aux PME (compteurs seulement)."""
import collections, json, re, requests
API = "https://boamp-datadila.opendatasoft.com/api/explore/v2.1/catalog/datasets/boamp/records"
recs = []
for off in (0, 100):
    r = requests.get(API, params={"limit": 100, "offset": off, "order_by": "dateparution desc",
                                  "where": "nature_libelle='Avis de marché'"}, timeout=60)
    r.raise_for_status(); recs += r.json()["results"]
print("n", len(recs))
print("champs", sorted(recs[0].keys()))
for k in ("famille", "famille_libelle", "procedure_libelle", "type_procedure", "procedure_categorise", "type_marche", "nature_categorise_libelle"):
    c = collections.Counter(str(x.get(k))[:50] for x in recs)
    print(k, c.most_common(8))
keys = collections.Counter(); ex = {}
def walk(n, p=""):
    if isinstance(n, dict):
        for k, v in n.items(): walk(v, p + "/" + k)
    elif isinstance(n, list):
        for v in n: walk(v, p)
    else:
        if re.search(r"(?i)valeur|montant|estim|lot|duree|duration|amount|pme|sme", p.split("/")[-1]):
            keys[p] += 1; ex.setdefault(p, str(n)[:40])
for x in recs:
    d = x.get("donnees")
    try: walk(json.loads(d) if isinstance(d, str) else d)
    except Exception: pass
for p, n in keys.most_common(30): print(n, p, "=>", ex[p])
