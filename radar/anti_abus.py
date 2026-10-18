"""Un seul essai gratuit par client : la même carte bancaire ou la même adresse ne redonne pas d'essai.

L'essai démarre au paiement Stripe (lien de paiement), avant toute création de compte : c'est donc là qu'on contrôle.
À chaque passage (bienvenue.yml toutes les 10 min, daily), chaque abonnement en essai est comparé aux abonnements
plus anciens qui ont déjà eu un essai : même empreinte de carte (`card.fingerprint`, identique pour une même carte
chez Stripe, même avec un autre compte client) ou même adresse e-mail. Si oui, l'essai est arrêté tout de suite
(`trial_end=now`) : le client paie dès aujourd'hui, au tarif de son offre.
Rien n'est stocké : les empreintes sont relues chez Stripe à chaque passage (aucune donnée dans le dépôt public).
"""
import logging
import os

import requests

from .subscribers import STRIPE, _stripe_list

log = logging.getLogger(__name__)
MARQUE = "essai_refuse"     # métadonnée posée sur l'abonnement dont l'essai a été arrêté (raison : carte / adresse)


def _empreinte(pm):
    return ((pm or {}).get("card") or {}).get("fingerprint") if isinstance(pm, dict) else None


def _email(sub):
    c = sub.get("customer")
    return ((c.get("email") or "") if isinstance(c, dict) else "").strip().lower() or None


def _cartes_client(cust_id, key, http, cache):
    """Empreintes de toutes les cartes d'un client Stripe (y compris celles d'abonnements résiliés)."""
    if cust_id not in cache:
        cache[cust_id] = {_empreinte(pm) for pm in _stripe_list(f"customers/{cust_id}/payment_methods", {"type": "card"}, key, http)} - {None}
    return cache[cust_id]


def doublons(subs):
    """Abonnements en essai qui n'y ont pas droit. `subs` : abonnements Stripe avec customer et
    default_payment_method développés, et `_cartes` (empreintes du client). Renvoie [(abonnement, raison)]."""
    deja = [s for s in subs if s.get("trial_start")]
    out = []
    for s in subs:
        if s.get("status") != "trialing" or (s.get("metadata") or {}).get(MARQUE):
            continue
        cust = s["customer"]["id"] if isinstance(s.get("customer"), dict) else s.get("customer")
        cartes = set(s.get("_cartes") or ()) | ({_empreinte(s.get("default_payment_method"))} - {None})
        email = _email(s)
        for o in deja:
            if o["id"] == s["id"] or (o.get("trial_start") or 0) > (s.get("trial_start") or 0):
                continue
            o_cust = o["customer"]["id"] if isinstance(o.get("customer"), dict) else o.get("customer")
            o_cartes = set(o.get("_cartes") or ()) | ({_empreinte(o.get("default_payment_method"))} - {None})
            if (o.get("trial_start") or 0) == (s.get("trial_start") or 0) and o["id"] > s["id"]:
                continue                                    # deux essais à la même seconde : on garde le premier
            if cartes & o_cartes or (cust and cust == o_cust):
                out.append((s, "carte"))
                break
            if email and email == _email(o):
                out.append((s, "adresse"))
                break
    return out


def appliquer(biz, mailer=None, session=None, simuler=False):
    """Arrête les essais en double chez Stripe ; renvoie un résumé court (jamais d'adresse ni d'empreinte)."""
    key = (os.getenv("STRIPE_SECRET_KEY") or "").strip()
    res = {"essais_verifies": 0, "essais_refuses": 0, "erreurs": []}
    if not key:
        return res
    http = session or requests
    subs = list(_stripe_list("subscriptions", {"status": "all", "expand[]": ["data.customer", "data.default_payment_method"]}, key, http))
    res["essais_verifies"] = sum(1 for s in subs if s.get("status") == "trialing")
    if not res["essais_verifies"]:
        return res
    cache = {}
    for s in subs:
        if s.get("trial_start"):
            cust = s["customer"]["id"] if isinstance(s.get("customer"), dict) else s.get("customer")
            try:
                s["_cartes"] = _cartes_client(cust, key, http, cache)
            except requests.HTTPError as exc:   # clé sans droit de lecture des moyens de paiement : on garde la carte de l'abonnement
                code = getattr(exc.response, "status_code", "?")
                if f"cartes illisibles ({code})" not in res["erreurs"]:
                    res["erreurs"].append(f"cartes illisibles ({code})")
                s["_cartes"] = set()
    for s, raison in doublons(subs):
        if simuler:
            res["essais_refuses"] += 1
            continue
        try:
            r = http.post(f"{STRIPE}/subscriptions/{s['id']}", auth=(key, ""), timeout=60,
                          data={"trial_end": "now", "proration_behavior": "none", f"metadata[{MARQUE}]": raison})
            r.raise_for_status()
            res["essais_refuses"] += 1
        except requests.RequestException as exc:
            res["erreurs"].append(f"essai non arrêté ({getattr(getattr(exc, 'response', None), 'status_code', 'réseau')})")
            continue
        email = _email(s)
        if mailer and email:
            try:
                from .render import render
                mailer.send(email, f"{biz['nom']} : votre abonnement démarre aujourd'hui",
                            render("email_essai_refuse.html", biz=biz, raison=raison),
                            biz["email_expediteur"], reply_to=biz.get("email_proprietaire") or biz["email_contact"])
            except Exception as exc:                        # l'e-mail est un plus, l'arrêt de l'essai est fait
                res["erreurs"].append(f"e-mail essai refusé non envoyé : {type(exc).__name__}")
    return res
