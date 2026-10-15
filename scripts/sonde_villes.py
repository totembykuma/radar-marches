"""Sonde : pages par ville construites sur les vrais avis BOAMP (compteurs seulement)."""
import sys, tempfile, collections
from pathlib import Path
sys.path.insert(0, ".")
from radar import boamp, site
from radar.config import business, metiers as load_metiers
from radar.matching import Matcher

biz, metiers = business(), load_metiers()
m = Matcher(metiers)
avis = boamp.fetch_open(days=30)
ok = [a for a in avis if not m.is_award_notice(a) and m.metiers_of(a)]
print("avis ouverts de nos métiers :", len(ok), "· avec ville :", sum(1 for a in ok if a["ville"]))
out = Path(tempfile.mkdtemp())
res = site.build(avis, biz, metiers, m, out)
villes = sorted((out / "appels-offres" / "villes").glob("*.html"))
print("pages du site :", res["pages"], "· pages ville :", len(villes) - 1)
print("exemples :", ", ".join(p.stem for p in villes[:12]))
print("plus petite page (avis) :", min(p.read_text().count('class="avis"') for p in villes if p.name != "index.html"))
sm = (out / "sitemap.xml").read_text()
print("sitemap : urls", sm.count("<loc>"), "· villes", sm.count("/villes/"))
