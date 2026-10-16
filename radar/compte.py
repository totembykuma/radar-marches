"""Espace client « Mon compte », sans mot de passe ni base de données.

Chaque e-mail envoyé au client contient son lien personnel : /compte/#<données>. Les données (offre, statut,
critères, liens utiles) sont dans la partie après « # », que le navigateur n'envoie jamais au serveur :
rien de personnel n'est publié sur le site. Lien perdu ? La page /compte/ prépare un e-mail vers
compte_adresse (business.yaml) ; bienvenue.yml (toutes les 10 min) le lit et renvoie le lien.
"""
import base64
import hashlib
import json
import os
import re
from datetime import datetime, timezone

from . import mailer, parrainage
from .criteres import _adresse, _resend
from .render import render


def _date(ts):
    return datetime.fromtimestamp(int(ts), timezone.utc).strftime("%d/%m/%Y") if ts else ""


def lien(biz, sub, metiers):
    """Lien personnel vers l'espace client (vide pour un compte sans abonnement Stripe)."""
    if not sub.get("email"):
        return ""
    offre = (biz.get("offres") or {}).get(sub.get("plan")) or {}
    d = {"e": sub["email"], "o": offre.get("nom", sub.get("plan", "")), "s": sub.get("statut", ""),
         "fe": _date(sub.get("fin_essai")), "db": _date(sub.get("debut")),
         "m": [[k, metiers[k]["libelle"]] for k in sub.get("metiers") or [] if k in metiers],
         "d": sub.get("departements") or [], "k": sub.get("mots_cles") or [],
         "p": parrainage.lien(biz, sub), "pro": sub.get("plan") == "pro"}
    code = base64.urlsafe_b64encode(json.dumps(d, ensure_ascii=False, separators=(",", ":")).encode()).decode().rstrip("=")
    return f"{biz['site_url'].rstrip('/')}/compte/#{code}"


def _envoyer(biz, sub, metiers, sujet):
    html = render("email_compte.html", biz=biz, lien_compte=lien(biz, sub, metiers), ok=bool(sub.get("email")))
    mailer.send(sub["email"], sujet, html, biz["email_expediteur"], reply_to=biz.get("email_proprietaire") or biz["email_contact"])


def traiter(subs, biz, metiers, state, session=None):
    """Renvoie le lien de l'espace aux clients qui l'ont demandé, et une fois aux clients d'avant l'espace."""
    envoyes, erreurs = 0, []
    # Clients inscrits avant l'ouverture de l'espace : on leur envoie leur lien une seule fois.
    deja = set(state.setdefault("compte_lien_envoye", []))
    known = set(state.get("known") or [])
    for s in subs:
        h = hashlib.sha256(s["email"].lower().encode()).hexdigest()[:16]
        if s.get("source") != "stripe" or h in deja or h not in known:
            continue
        try:
            _envoyer(biz, s, metiers, f"{biz['nom']} : votre espace client")
            state["compte_lien_envoye"].append(h)
            deja.add(h)
            envoyes += 1
        except Exception as exc:
            erreurs.append(f"lien espace non envoyé : {str(exc)[:120]}")

    key, adresse = (os.getenv("RESEND_INBOUND_KEY") or "").strip(), (biz.get("compte_adresse") or "").lower()
    if not key or not adresse:
        return envoyes, erreurs
    vus = state.setdefault("compte_vus", [])
    par_email = {s["email"].lower(): s for s in subs}
    try:
        recus = _resend("/emails/receiving?limit=100", key, session).get("data") or []
    except Exception as exc:
        return envoyes, erreurs + [f"demandes d'espace non lues : {str(exc)[:120]}"]
    for e in recus:
        if e["id"] in vus or adresse not in " ".join(map(str, e.get("to") or [])).lower():
            continue
        vus.append(e["id"])
        exp = _adresse(e.get("from"))
        if not exp or not re.match(r"[^@]+@[^@]+\.[a-z]{2,}$", exp):
            continue
        try:
            s = par_email.get(exp)
            if s:
                _envoyer(biz, s, metiers, f"{biz['nom']} : votre lien vers votre espace client")
            else:
                mailer.send(exp, f"{biz['nom']} : adresse non reconnue", render("email_compte.html", biz=biz, ok=False),
                            biz["email_expediteur"], reply_to=biz["email_contact"])
            envoyes += 1
        except Exception as exc:
            erreurs.append(f"lien espace non envoyé : {str(exc)[:120]}")
    state["compte_vus"] = vus[-500:]
    return envoyes, erreurs
