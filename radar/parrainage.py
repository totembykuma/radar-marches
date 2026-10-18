"""Parrainage 100 % automatique : 1 mois offert au parrain ET au filleul.

Chaque abonné a un lien personnel (…/?ami=CODE). Le site ajoute ce code aux liens de paiement Stripe
(paramètre client_reference_id), on le retrouve donc sur la session de paiement du filleul.
Chaque matin :
- nouveau filleul (même en essai) : 1 mois de son offre crédité sur son compte Stripe ;
- filleul devenu payant (essai terminé) : 1 mois de l'offre du parrain crédité au parrain, qui est prévenu par e-mail.
Le crédit est un solde client Stripe, déduit automatiquement de la prochaine facture. Si la clé n'a pas ce droit,
on applique à la place un bon de réduction « une fois » sur l'abonnement.
"""
import base64
import hashlib
import logging
import re

from . import mailer
from .paliers import prix_fr
from .render import render
from .stripe_setup import _call

log = logging.getLogger(__name__)


def code(sub_id):
    return base64.b32encode(hashlib.sha256(f"parrainage|{sub_id}".encode()).digest()).decode()[:6]


def lien(biz, sub):
    sid = sub.get("stripe_subscription")
    return f"{biz['site_url'].rstrip('/')}/parrainage/?ami={code(sid)}" if sid else ""


def _ref(s):
    r = re.sub(r"[^A-Z0-9]", "", str(s.get("ref") or "").upper())
    return r if len(r) == 6 else ""


def crediter(s, biz, motif):
    """Offre 1 mois de l'offre de l'abonné s. Lève RuntimeError si Stripe refuse."""
    # 1 mois au prix que paie l'abonné (palier d'ancienneté en cours), sinon au prix de départ de son offre
    montant = int(s.get("prix_mois") or round(biz["offres"][s["plan"]]["prix_mensuel"] * 100))
    if s.get("customer"):
        try:
            _call("POST", f"customers/{s['customer']}/balance_transactions", data={
                "amount": -montant, "currency": "eur", "description": f"Parrainage : 1 mois offert ({motif})"})
            return "solde"
        except RuntimeError as exc:
            log.warning("Crédit de solde refusé, bon de réduction à la place : %s", str(exc)[:120])
    cid = f"radar-parrainage-{s['plan']}" + ("" if montant == round(biz["offres"][s["plan"]]["prix_mensuel"] * 100) else f"-{montant}")
    try:
        _call("GET", f"coupons/{cid}")
    except RuntimeError:
        _call("POST", "coupons", data={"id": cid, "name": "Parrainage : 1 mois offert", "amount_off": montant,
                                       "currency": "eur", "duration": "once"})
    sub = _call("GET", f"subscriptions/{s['stripe_subscription']}")
    data, i = {}, 0
    for d in sub.get("discounts") or []:  # garder les réductions déjà en place (promo…)
        data[f"discounts[{i}][discount]"] = d if isinstance(d, str) else d.get("id")
        i += 1
    data[f"discounts[{i}][coupon]"] = cid
    _call("POST", f"subscriptions/{s['stripe_subscription']}", data=data)
    return "bon"


def traiter(subs, biz, state):
    """Retourne (nb de mois offerts, erreurs). Chaque récompense n'est donnée qu'une fois (state['parrainage'])."""
    faits = state.setdefault("parrainage", [])
    par_code = {code(s["stripe_subscription"]): s for s in subs if s.get("stripe_subscription")}
    n, erreurs = 0, []
    for f in subs:
        p = par_code.get(_ref(f))
        if not p or not f.get("stripe_subscription") or p is f or p["email"].lower() == f["email"].lower():
            continue
        k = f"f|{f['stripe_subscription']}"
        if k not in faits:
            try:
                crediter(f, biz, "filleul")
                faits.append(k)
                n += 1
            except Exception as exc:
                erreurs.append(f"parrainage : mois offert au filleul non appliqué ({str(exc)[:100]})")
        f["filleul"] = k in faits
        k = f"p|{f['stripe_subscription']}"
        if f.get("statut") == "active" and k not in faits:
            try:
                crediter(p, biz, "parrain")
                faits.append(k)
                n += 1
                html = render("email_parrainage.html", biz=biz, offre=biz["offres"][p["plan"]], lien=lien(biz, p),
                              montant=prix_fr((p.get("prix_mois") or round(biz["offres"][p["plan"]]["prix_mensuel"] * 100)) / 100))
                mailer.send(p["email"], f"Un mois offert sur {biz['nom']} grâce à votre filleul", html,
                            biz["email_expediteur"], reply_to=biz["email_contact"])
            except Exception as exc:
                erreurs.append(f"parrainage : mois offert au parrain non appliqué ({str(exc)[:100]})")
    state["parrainage"] = faits[-1000:]
    return n, erreurs
