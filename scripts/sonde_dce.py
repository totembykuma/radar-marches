"""Sonde : où sont les dossiers de consultation (DCE) dans les avis BOAMP, et peut-on les télécharger sans compte ?
Imprime des compteurs et quelques exemples d'adresses (pas de données personnelles)."""
import collections, json, re, sys
from datetime import date, timedelta
from urllib.parse import urlparse
import requests
sys.path.insert(0, ".")
from radar import boamp

def urls(o, chemin=""):
    if isinstance(o, dict):
        for k, v in o.items():
            yield from urls(v, chemin + "/" + k)
    elif isinstance(o, list):
        for v in o:
            yield from urls(v, chemin)
    elif isinstance(o, str) and re.match(r"https?://", o.strip()):
        yield chemin, o.strip()

r = requests.get(f"{boamp.API}/exports/json", params={"where": f"dateparution >= date'{(date.today()-timedelta(days=2)).isoformat()}'", "limit": 300}, timeout=120)
rows = r.json()
cles, domaines, exemples = collections.Counter(), collections.Counter(), {}
avec = 0
for x in rows:
    try:
        d = json.loads(x.get("donnees") or "{}")
    except Exception:
        continue
    vus = list(urls(d))
    if vus:
        avec += 1
    for k, u in vus:
        k2 = re.sub(r"/\d+", "", k)[-90:]
        cles[k2] += 1
        dom = urlparse(u).netloc.lower().removeprefix("www.")
        domaines[dom] += 1
        exemples.setdefault(dom, u)
print(f"avis: {len(rows)}, avec au moins une adresse: {avec}")
print("CLES:"); [print(f"  {n:4d} {k}") for k, n in cles.most_common(25)]
print("DOMAINES:"); [print(f"  {n:4d} {k}") for k, n in domaines.most_common(25)]
print("ESSAIS DE TELECHARGEMENT (sans compte):")
ua = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/124 Safari/537.36"}
for dom, u in list(exemples.items())[:25]:
    try:
        rr = requests.get(u, headers=ua, timeout=20, allow_redirects=True)
        t = rr.headers.get("content-type", "")[:40]
        zips = len(re.findall(r'href="[^"]+\.(?:zip|pdf)"', rr.text, re.I)) if "html" in t else 0
        dl = len(re.findall(r"t[ée]l[ée]charg", rr.text, re.I)) if "html" in t else 0
        print(f"  {dom}: {rr.status_code} {t} liens_zip_pdf={zips} mots_telecharg={dl} url={u[:110]}")
    except Exception as e:
        print(f"  {dom}: ERREUR {type(e).__name__}")
