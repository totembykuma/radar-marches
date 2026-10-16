"""Alerte gratuite limitée : 3 marchés de son métier par semaine, avec une invitation à l'essai complet.

1. Le formulaire /alerte-gratuite/ du site prépare un e-mail vers l'adresse de réception Resend (business.yaml :
   alerte_gratuite.adresse). Chaque matin, daily lit ces e-mails : inscription (métier + département) ou désinscription
   (objet « STOP »). La première alerte part tout de suite, puis une par semaine.
2. Le dépôt est public : les adresses sont chiffrées dans data/alerte_gratuite.json (clé tirée d'un secret GitHub),
   seule une empreinte sert à les reconnaître. Un abonné payant ou en essai sort automatiquement de la liste gratuite.
Inactif tant que RESEND_INBOUND_KEY ou l'adresse de réception manquent.
"""
import base64
import hashlib
import json
import logging
import os
import re
from datetime import date, timedelta

from cryptography.fernet import Fernet, InvalidToken

from . import mailer
from .config import ROOT
from .criteres import _adresse, _resend, lire_demande
from .matching import parse_departements
from .render import date_limite_fr, render
from .subscribers import resolve_metier
from .textutil import norm

log = logging.getLogger(__name__)
FICHIER = ROOT / "data" / "alerte_gratuite.json"
PAR_SEMAINE = 3


def h(email):
    return hashlib.sha256(email.lower().strip().encode()).hexdigest()[:16]


def _fernet():
    secret = (os.getenv("ALERTE_GRATUITE_CLE") or os.getenv("RESEND_INBOUND_KEY") or "").strip()
    if not secret:
        return None
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(b"radar-alerte-gratuite|" + secret.encode()).digest()))


def charger():
    try:
        return json.loads(FICHIER.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"abonnes": []}


def sauver(data):
    FICHIER.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def semaine(d=None):
    y, w, _ = (d or date.today()).isocalendar()
    return f"{y}-W{w:02d}"


def selection(avis_open, metier, dep, matcher, today=None):
    """(3 marchés à envoyer, nombre total de marchés ouverts du métier dans la zone)."""
    today = today or date.today()
    limite = (today + timedelta(days=7)).isoformat()   # assez de temps pour répondre
    lst = [a for a in avis_open if not matcher.is_award_notice(a) and metier in (a.get("metiers") or matcher.metiers_of(a))
           and (a.get("date_limite") or "9999") >= today.isoformat()]
    local = [a for a in lst if dep and dep in a["departements"]]
    zone = local if len(local) >= PAR_SEMAINE or (local and len(lst) < PAR_SEMAINE) else lst
    choix = sorted([a for a in zone if (a.get("date_limite") or "9999") >= limite] or zone,
                   key=lambda a: a.get("date_parution") or "", reverse=True)[:PAR_SEMAINE]
    for a in choix:
        a["date_limite_fr"] = date_limite_fr(a.get("date_limite"))
    return choix, len(zone), zone is local


def _envoyer(email, entree, biz, metiers, avis_open, matcher, bienvenue=False):
    conf = biz["alerte_gratuite"]
    choix, total, local = selection(avis_open, entree["metier"], entree.get("dep"), matcher)
    m = metiers[entree["metier"]]
    stop = f"mailto:{conf['adresse']}?subject=STOP%20alerte%20gratuite"
    html = render("email_gratuit.html", biz=biz, avis=choix, total=total, metier=m, dep=entree.get("dep") if local else None,
                  bienvenue=bienvenue, stop=stop,
                  lien_essai=biz["site_url"].rstrip("/") + "/?utm_source=alerte-gratuite&utm_medium=email#tarifs")
    sujet = (f"Votre alerte gratuite est active : {len(choix)} marchés « {m['libelle'].lower()} »" if bienvenue
             else f"Vos {len(choix)} marchés « {m['libelle'].lower()} » de la semaine")
    return mailer.send(email, sujet, html, biz["email_expediteur"], reply_to=biz["email_contact"],
                       headers={"List-Unsubscribe": f"<{stop}>"})


def traiter(biz, metiers, matcher, state, subs, avis_open, quota, session=None):
    """Lit inscriptions et désinscriptions, puis envoie l'alerte de la semaine à ceux qui ne l'ont pas encore reçue.
    Retourne (stats, erreurs). quota = nombre maximum d'e-mails à envoyer aujourd'hui."""
    conf = biz.get("alerte_gratuite") or {}
    key, adresse, f = (os.getenv("RESEND_INBOUND_KEY") or "").strip(), (conf.get("adresse") or "").lower(), _fernet()
    stats, erreurs = {"inscrits": 0, "desinscrits": 0, "envoyes": 0}, []
    if not key or not adresse or not f:
        return stats, erreurs
    data = charger()
    par_h = {x["id"]: x for x in data["abonnes"]}
    clients = {h(s["email"]) for s in subs}
    envois = []                                       # (adresse, entrée, bienvenue)

    def email_de(x):
        try:
            return f.decrypt(x["e"].encode()).decode()
        except InvalidToken:
            return None

    vus = state.setdefault("gratuit_vus", [])
    for e in _resend("/emails/receiving?limit=100", key, session).get("data") or []:
        if e["id"] in vus or adresse not in " ".join(map(str, e.get("to") or [])).lower():
            continue
        vus.append(e["id"])
        exp, sujet = _adresse(e.get("from")), norm(e.get("subject") or "")
        if not exp:
            continue
        if "stop" in sujet or "desinscri" in sujet:
            if par_h.pop(h(exp), None):
                stats["desinscrits"] += 1
            continue
        if h(exp) in clients:
            continue                                  # déjà abonné : il reçoit déjà tout
        try:
            corps = _resend(f"/emails/receiving/{e['id']}", key, session)
        except Exception as exc:
            erreurs.append(f"alerte gratuite : demande illisible ({str(exc)[:80]})")
            continue
        d = lire_demande(corps.get("text") or re.sub(r"<[^>]+>", "\n", corps.get("html") or ""))
        metier = resolve_metier(d.get("metier"), metiers)
        if not metier:
            continue
        deps = parse_departements(d.get("departements") or "")
        x = {"id": h(exp), "e": f.encrypt(exp.encode()).decode(), "metier": metier, "dep": deps[0] if deps else "",
             "depuis": (par_h.get(h(exp)) or {}).get("depuis") or date.today().isoformat(), "semaine": ""}
        nouveau = h(exp) not in par_h
        par_h[x["id"]] = x
        stats["inscrits"] += nouveau
        envois.append((exp, x, True))
    state["gratuit_vus"] = vus[-500:]

    for i in list(par_h):                             # devenu client (essai ou payant) : il sort de la liste gratuite
        if i in clients:
            par_h.pop(i)
    deja = {x["id"] for _, x, _ in envois}
    for x in par_h.values():
        if x["id"] not in deja and x.get("semaine") != semaine():
            email = email_de(x)
            if email:
                envois.append((email, x, False))
            else:
                erreurs.append("alerte gratuite : une adresse n'a pas pu être déchiffrée (secret changé ?)")

    for email, x, bienvenue in envois[:max(0, quota)]:
        try:
            res = _envoyer(email, x, biz, metiers, avis_open, matcher, bienvenue)
        except Exception as exc:
            erreurs.append(f"alerte gratuite non envoyée : {str(exc)[:120]}")
            continue
        if not res.get("simulated") or os.getenv("RADAR_DEMO") == "1":
            x["semaine"] = semaine()
        stats["envoyes"] += 1
    data["abonnes"] = list(par_h.values())
    sauver(data)
    stats["total"] = len(data["abonnes"])
    return stats, erreurs


def emails_connus():
    """Empreintes des inscrits gratuits (pour ne pas leur envoyer de relance de prospection)."""
    return {x["id"] for x in charger()["abonnes"]}
