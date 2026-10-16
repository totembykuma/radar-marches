"""Changement de critères en libre-service, sans intervention humaine.

Le formulaire /criteres/ du site prépare un e-mail structuré vers l'adresse de réception Resend
(business.yaml : criteres_adresse). Chaque matin, on lit les e-mails reçus, on vérifie que l'expéditeur
est bien un abonné, on met à jour les métadonnées de son abonnement Stripe et on lui confirme le changement.
Inactif tant que RESEND_INBOUND_KEY (clé Resend complète) ou criteres_adresse manquent.
"""
import logging
import os
import re

import requests

from . import mailer
from .matching import parse_departements
from .render import render
from .subscribers import resolve_metier
from .textutil import norm

log = logging.getLogger(__name__)
RESEND = "https://api.resend.com"


def _resend(path, key, session=None):
    r = (session or requests).get(RESEND + path, headers={"Authorization": f"Bearer {key}"}, timeout=30)
    if r.status_code >= 400:
        raise RuntimeError(f"Resend {path} : {r.status_code}")
    return r.json()


def _adresse(x):
    m = re.search(r"[\w.+-]+@[\w-]+(\.[\w-]+)+", x or "")
    return m.group(0).lower() if m else ""


def lire_demande(texte):
    """Extrait métier, départements et mots-clés du corps de l'e-mail préparé par le formulaire."""
    out = {}
    for ligne in (texte or "").splitlines():
        if ":" not in ligne:
            continue
        cle, val = ligne.split(":", 1)
        cle, val = norm(cle).strip(" >*"), val.strip()
        if cle.startswith("metier"):
            out["metier"] = val
        elif cle.startswith("departement"):
            out["departements"] = val
        elif cle.startswith("mots"):
            out["motscles"] = val
    return out


def _stripe_update(sub_id, meta, session=None):
    key = (os.getenv("STRIPE_SECRET_KEY") or "").strip()
    data = {f"metadata[{k}]": v for k, v in meta.items()}
    r = (session or requests).post(f"https://api.stripe.com/v1/subscriptions/{sub_id}", data=data, auth=(key, ""), timeout=60)
    if r.status_code >= 400:
        raise RuntimeError(f"Stripe abonnement : {r.status_code}")


def traiter(subs, biz, metiers, state, criteres_txt, session=None):
    """Applique les demandes reçues. Retourne (nb appliquées, erreurs)."""
    key, adresse = (os.getenv("RESEND_INBOUND_KEY") or "").strip(), (biz.get("criteres_adresse") or "").lower()
    if not key or not adresse:
        return 0, []
    vus = state.setdefault("criteres_vus", [])
    par_email = {s["email"].lower(): s for s in subs if s.get("stripe_subscription")}
    faits, erreurs = 0, []
    for e in _resend("/emails/receiving?limit=100", key, session).get("data") or []:
        if e["id"] in vus or adresse not in " ".join(map(str, e.get("to") or [])).lower():
            continue
        vus.append(e["id"])
        if "crit" not in norm(e.get("subject") or ""):
            continue
        exp = _adresse(e.get("from"))
        s = par_email.get(exp)
        try:
            if not s:
                mailer.send(exp, f"{biz['nom']} : adresse non reconnue", render("email_criteres.html", biz=biz, ok=False),
                            biz["email_expediteur"], reply_to=biz["email_contact"])
                continue
            corps = _resend(f"/emails/receiving/{e['id']}", key, session)
            d = lire_demande(corps.get("text") or re.sub(r"<[^>]+>", "\n", corps.get("html") or ""))
            offre, meta = biz["offres"][s["plan"]], {}
            m = resolve_metier(d.get("metier"), metiers)
            if m:
                meta["metier"], s["metiers"] = m, [m]
            deps = parse_departements(d.get("departements") or "")[: offre["departements_max"]]
            if deps:
                meta["departements"], s["departements"] = ", ".join(deps), deps
            if s["plan"] in ("pro", "cle") and "motscles" in d:
                mots = [x.strip() for x in d["motscles"].split(",") if x.strip() and norm(x.strip()) != "aucun"]
                meta["motscles"], s["mots_cles"] = ", ".join(mots) or ",", mots
            if meta:
                _stripe_update(s["stripe_subscription"], meta, session)
                faits += 1
            mailer.send(exp, f"{biz['nom']} : vos critères sont à jour", render("email_criteres.html", biz=biz, ok=True,
                        criteres=criteres_txt(s, metiers)), biz["email_expediteur"], reply_to=biz["email_contact"])
        except Exception as exc:
            erreurs.append(f"critères non appliqués : {str(exc)[:120]}")
    state["criteres_vus"] = vus[-500:]
    return faits, erreurs
