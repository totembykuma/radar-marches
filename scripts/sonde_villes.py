"""Sonde : où se trouve la ville de l'acheteur dans les avis BOAMP, et GoatCounter est-il lisible ? (compteurs seulement)"""
import json, re, sys, collections, requests
sys.path.insert(0, ".")
API = "https://boamp-datadila.opendatasoft.com/api/explore/v2.1/catalog/datasets/boamp"
rows = requests.get(f"{API}/records", params={"limit": 100, "order_by": "dateparution desc"}, timeout=60).json()["results"]
paths = collections.Counter()
ex = {}
for r in rows:
    try:
        d = json.loads(r.get("donnees") or "{}")
    except ValueError:
        continue
    fmt = next(iter(d), "?")
    def walk(n, p):
        if isinstance(n, dict):
            for k, v in n.items(): walk(v, p + "/" + k)
        elif isinstance(n, list):
            for v in n[:3]: walk(v, p + "[]")
        elif re.search(r"ville|city|cp$|postal|commune|town", p.split("/")[-1], re.I):
            key = re.sub(r"\[\]", "", p)
            paths[key] += 1
            ex.setdefault(key, str(n)[:40])
    walk(d, fmt)
for k, n in paths.most_common(25):
    print(n, k, "=>", ex[k])
for u in ("https://radarmarches.goatcounter.com/counter/TOTAL.json", "https://radarmarches.goatcounter.com/api/v0/stats/total"):
    try:
        x = requests.get(u, timeout=20); print("goat", u.split(".com")[1], x.status_code, x.text[:120])
    except Exception as e:
        print("goat erreur", e)
