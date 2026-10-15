"""Aperçu réel de la fiche « avant de répondre » : couverture sur les avis récents + e-mail d'exemple
envoyé au propriétaire (offre Pro). N'affiche que des compteurs dans le journal."""
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from radar import boamp, historique, mailer  # noqa: E402
from radar.config import business, metiers as load_metiers  # noqa: E402
from radar.matching import Matcher  # noqa: E402
from radar.render import date_fr, date_limite_fr, render  # noqa: E402

biz, metiers = business(), load_metiers()
M, H = Matcher(metiers), historique.Historique(metiers)
avis = [a for a in boamp.fetch_since(date.today() - timedelta(days=3)) if not M.is_award_notice(a)]
paires = [(a, m) for a in avis for m in M.metiers_of(a)]
avec_siret = [(a, m) for a, m in paires if a["acheteur_siret"]]
print(f"avis x métier (3 jours) : {len(paires)}, avec SIRET acheteur : {len(avec_siret)}")
fiches = [(a, m, H.fiche(a["acheteur_siret"], m)) for a, m in avec_siret]
ok = [(a, m, f) for a, m, f in fiches if f]
print(f"avec fiche (historique dans le métier, 2 ans) : {len(ok)} ({100 * len(ok) // max(len(paires), 1)} % des alertes)")
print(f"  avec montant habituel : {sum(1 for *_, f in ok if f['montant_median'])}, "
      f"avec nb d'offres : {sum(1 for *_, f in ok if f['offres_median'])}, "
      f"gagnant nommé : {sum(1 for *_, f in ok if any(d['gagnant'] for d in f['derniers']))}")
H.sauver()
sel = []
for a, m, f in ok[:6]:
    a.update(fiche=f, fiche_complete=True, metier_libelle=metiers[m]["libelle"].lower(),
             date_limite_fr=date_limite_fr(a["date_limite"]))
    sel.append(a)
if sel and biz.get("email_proprietaire"):
    html = render("email_digest.html", biz=biz, avis=sel, date_fr=date_fr(), criteres="exemple (offre Pro, plusieurs métiers)")
    mailer.send(biz["email_proprietaire"], "[Aperçu] Alerte Pro avec la fiche « avant de répondre »", html, biz["email_expediteur"])
    print("aperçu envoyé au propriétaire")
