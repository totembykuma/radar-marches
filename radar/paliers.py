"""Prix dégressifs selon l'ancienneté + essai de 7 jours au plus.

Grille (config/business.yaml, offres.<x>.paliers) : prix_mensuel les premiers mois payés, puis chaque palier
à partir de son mois (« des_mois »). L'ancienneté = mois payés depuis la fin de l'essai, comptée par abonnement Stripe :
résilier puis se réabonner crée un nouvel abonnement, qui repart au 1er mois ; changer de carte, se reconnecter ou
annuler une résiliation programmée garde le même abonnement, donc l'ancienneté.
Pendant le mois qui précède un changement de palier, le prix de l'abonnement Stripe passe au palier suivant, sans
prorata : la facture suivante est calculée par Stripe au bon montant, à la date habituelle. Lancé par bienvenue.yml
(toutes les 10 min) et daily : un mois entier de marge avant chaque facture.
Essai : un essai plus long que essai_gratuit_jours est raccourci (l'essai unique par client est dans anti_abus.py).
Seuls les abonnements créés après nouvelle_grille_depuis (écrit par stripe_setup) sont concernés ; les plus anciens
gardent leurs conditions, sauf nouvelle_grille_anciens: true (nouvelle grille appliquée, essai inchangé).
"""
import logging
import time
from datetime import datetime, timezone

from . import mailer
from .stripe_setup import _call

log = logging.getLogger(__name__)
MOIS_S = 30.436875 * 86400          # durée moyenne d'un mois : arrondi exact jusqu'à des dizaines d'années
ACTIFS = {"active", "trialing", "past_due"}


def grille(offre):
    """[(1, 29), (3, 19.99), (6, 9.99)] : prix appliqué à partir de chaque mois payé."""
    return sorted([(1, offre["prix_mensuel"])] + [(int(p["des_mois"]), p["prix"]) for p in offre.get("paliers") or []])


def prix_du_mois(offre, n):
    prix = offre["prix_mensuel"]
    for des, p in grille(offre):
        if n >= des:
            prix = p
    return prix


def centimes(prix):
    return int(round(float(prix) * 100))


def prix_fr(prix):
    """29 → « 29 € » ; 19.99 → « 19,99 € »."""
    c = centimes(prix)
    return (f"{c // 100}" if c % 100 == 0 else f"{c // 100},{c % 100:02d}") + " €"


def texte_grille(offre):
    """« 29 € les 2 premiers mois, 19,99 € du 3e au 5e mois, puis 9,99 € à partir du 6e mois »."""
    g = grille(offre)
    if len(g) == 1:
        return f"{prix_fr(g[0][1])} par mois"
    parts = []
    for i, (des, p) in enumerate(g):
        fin = g[i + 1][0] - 1 if i + 1 < len(g) else None
        if fin is None:
            parts.append(f"puis {prix_fr(p)} à partir du {des}e mois")
        elif des == 1:
            parts.append(f"{prix_fr(p)} {'le 1er mois' if fin == 1 else f'les {fin} premiers mois'}")
        else:
            parts.append(f"{prix_fr(p)} {'le' if fin == des else 'du'} {des}e{'' if fin == des else f' au {fin}e'} mois")
    return ", ".join(parts)


def tarif_abonne(biz, s):
    """Texte du tarif d'un abonné (dict de subscribers) : la grille, ou son prix fixe s'il garde ses anciennes conditions."""
    offre = biz["offres"][s["plan"]]
    depuis = _ts(biz.get("nouvelle_grille_depuis") or "")
    if depuis and ((s.get("debut") or 0) >= depuis or biz.get("nouvelle_grille_anciens")):
        return texte_grille(offre)
    return f"{prix_fr((s.get('prix_mois') or centimes(offre['prix_mensuel'])) / 100)} par mois"


def _fin_periode(sub):
    items = (sub.get("items") or {}).get("data") or []
    return sub.get("current_period_end") or max([it.get("current_period_end") or 0 for it in items] or [0])


def mois_suivant(sub):
    """Numéro du mois payé que couvrira la prochaine facture (1 = premier mois après l'essai)."""
    debut = sub.get("billing_cycle_anchor") or sub.get("trial_end") or sub.get("start_date") or sub.get("created")
    return max(1, round((_fin_periode(sub) - debut) / MOIS_S) + 1)


def _email(sub):
    c = sub.get("customer")
    return (c.get("email") or "") if isinstance(c, dict) else ""


def _offre_du_prix(biz, price_id):
    for k, o in biz["offres"].items():
        if price_id in (o.get("stripe_paliers") or []):
            return k, o
    return None, None


def _ts(iso):
    try:
        return datetime.fromisoformat(str(iso)).timestamp()
    except ValueError:
        return None


def lister(call=None):
    call = call or _call
    params = {"status": "all", "limit": 100, "expand[]": ["data.customer", "data.default_payment_method"]}
    out = []
    while True:
        page = call("GET", "subscriptions", params=params)
        out += page["data"]
        if not page.get("has_more") or not page["data"]:
            return out
        params = dict(params, starting_after=page["data"][-1]["id"])


def _prevenir(biz, sub, sujet, modele, **ctx):
    from .render import render
    email = _email(sub)
    if not email:
        return
    html = render(modele, biz=biz, **ctx)
    mailer.send(email, sujet, html, biz["email_expediteur"], reply_to=biz.get("email_proprietaire") or biz["email_contact"])


def traiter(biz, call=None, subs=None, maintenant=None):
    """Applique la durée maximale d'essai et les paliers. Ne fait rien tant que nouvelle_grille_depuis est absent."""
    call = call or _call
    res = {"essais_raccourcis": 0, "paliers": 0, "erreurs": []}
    depuis = _ts(biz.get("nouvelle_grille_depuis") or "")
    if not depuis:
        return res
    tous = subs if subs is not None else lister(call)
    now = maintenant or time.time()
    jours = int(biz.get("essai_gratuit_jours") or 7)
    for sub in tous:
        if sub.get("status") not in ACTIFS:
            continue
        nouveau = (sub.get("created") or 0) >= depuis
        meta = sub.get("metadata") or {}
        items = (sub.get("items") or {}).get("data") or []
        try:
            # 1. Essai jamais plus long que prévu (un seul essai par client : radar/anti_abus.py, lancé juste avant)
            if nouveau and sub["status"] == "trialing" and meta.get("essai") != "accorde":
                debut = sub.get("trial_start") or sub.get("created") or now
                fin_max = debut + jours * 86400
                if (sub.get("trial_end") or 0) > fin_max + 3600:
                    sub.update(call("POST", f"subscriptions/{sub['id']}", data={
                        "trial_end": int(fin_max) if fin_max > now + 120 else "now", "proration_behavior": "none"}))
                    res["essais_raccourcis"] += 1
            # 2. Palier de prix de la prochaine facture
            if not (nouveau or biz.get("nouvelle_grille_anciens")) or meta.get("paliers") == "non" \
                    or sub.get("cancel_at_period_end") or sub.get("status") not in ACTIFS or len(items) != 1:
                continue
            item = items[0]
            price = item.get("price") or {}
            k, offre = _offre_du_prix(biz, price.get("id"))
            if not offre or (price.get("recurring") or {}).get("interval") != "month":
                continue
            n = mois_suivant(sub)
            cible = prix_du_mois(offre, n)
            ids = offre["stripe_paliers"]
            cible_id = ids[[p for _, p in grille(offre)].index(cible)]
            if cible_id == price.get("id"):
                continue
            sub.update(call("POST", f"subscriptions/{sub['id']}", data={
                "items[0][id]": item["id"], "items[0][price]": cible_id, "proration_behavior": "none",
                "metadata[palier_mois]": str(n)}))
            res["paliers"] += 1
            if centimes(cible) < centimes((price.get("unit_amount") or 0) / 100):
                date_fr = datetime.fromtimestamp(_fin_periode(sub), timezone.utc).strftime("%d/%m/%Y")
                _prevenir(biz, sub, f"Bonne nouvelle : votre abonnement {biz['nom']} baisse à {prix_fr(cible)}",
                          "email_baisse_prix.html", offre=offre, prix=prix_fr(cible), date_fr=date_fr, mois=n,
                          grille=texte_grille(offre))
        except Exception as exc:  # une erreur sur un abonnement ne bloque pas les autres
            res["erreurs"].append(f"paliers/essai ({sub.get('id')}) : {str(exc)[:160]}")
    return res
