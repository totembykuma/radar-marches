"""Aperçu réel d'une alerte avec résumés IA (vrais avis BOAMP, aucun e-mail envoyé) → apercu-ia/alerte.html."""
import sys
from datetime import date, timedelta
from pathlib import Path

from radar import ai, boamp, historique
from radar.cli import lien_dossier
from radar.config import business, metiers as load_metiers
from radar.matching import Matcher
from radar.render import date_fr, date_limite_fr, render

metier = sys.argv[1] if len(sys.argv) > 1 else "nettoyage"
dep = sys.argv[2] if len(sys.argv) > 2 else "75"
biz, metiers = business(), load_metiers()
biz["dossier_actif"] = biz["ia_active"] = True
m = Matcher(metiers)
avis = boamp.fetch_open(days=10)
for a in avis:
    a["metiers"] = m.metiers_of(a)
sub = {"email": "client@exemple.fr", "plan": "pro", "metiers": [metier], "departements": [dep], "mots_cles": []}
sel = m.for_subscriber(avis, sub)[:4]
if not sel:
    sub["departements"] = []
    sel = m.for_subscriber(avis, sub)[:4]
ai.enrich(sel, model=biz["ia"]["modele"], batch=25)
histo = historique.Historique(metiers)
sel.sort(key=lambda a: (-(a.get("score") or 0), a["date_limite"] or "9999"))
for a in sel:
    a["date_limite_fr"] = date_limite_fr(a["date_limite"])
    a["fiche"] = histo.fiche(a.get("acheteur_siret"), metier) if a.get("acheteur_siret") else None
    a["fiche_complete"] = True
    a["metier_libelle"] = metiers[metier]["libelle"].lower()
html = render("email_digest.html", biz=biz, avis=sel, date_fr=date_fr(), criteres=f"{metiers[metier]['libelle']} · {dep}",
              lien_parrainage="", lien_dossier=lien_dossier(biz, sub), lien_compte="")
Path("apercu-ia").mkdir(exist_ok=True)
Path("apercu-ia/alerte.html").write_text(html, encoding="utf-8")
print("avis:", len(avis), "selection:", len(sel), "resumes IA:", sum(1 for a in sel if a.get("resume")))
