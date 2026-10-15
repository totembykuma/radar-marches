"""Avis clients 100 % automatiques.

1. Un mois après le début de l'abonnement (abonné payant, hors essai), un e-mail demande un avis (une seule fois).
2. Le formulaire /avis/ du site prépare un e-mail vers l'adresse de réception Resend.
3. Chaque matin, on lit ces e-mails : seuls les abonnés (adresse reconnue) sont publiés, sans tri sur la note.
   Les liens, e-mails et numéros sont retirés ; un texte injurieux n'est pas publié (le propriétaire est prévenu).
Les avis publiés sont dans data/avis.json et s'affichent sur l'accueil.
"""
import hashlib
import json
import logging
import os
import re
from datetime import datetime, timezone

from . import mailer
from .config import ROOT
from .criteres import _adresse, _resend
from .render import render
from .textutil import norm

log = logging.getLogger(__name__)
FICHIER = ROOT / "data" / "avis.json"
MOIS_FR = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre", "novembre", "décembre"]
INJURES = {"connard", "connards", "conne", "con", "cons", "salope", "encule", "enculé", "merde", "putain", "pute", "batard",
           "bâtard", "nique", "abruti", "abrutis", "debile", "débile"}


def charger(path=None):
    try:
        return json.loads((path or FICHIER).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []


def _h(x):
    return hashlib.sha256(x.lower().encode()).hexdigest()[:16]


def lire(texte):
    """Extrait note, nom affiché, activité, accord de publication et texte de l'e-mail préparé par le formulaire."""
    out, lignes = {}, (texte or "").splitlines()
    for i, ligne in enumerate(lignes):
        if ":" not in ligne:
            continue
        cle, val = ligne.split(":", 1)
        cle, val = norm(cle).strip(" >*"), val.strip()
        if cle.startswith("note"):
            m = re.search(r"[1-5]", val)
            out["note"] = int(m.group(0)) if m else None
        elif cle.startswith("nom"):
            out["nom"] = val
        elif cle.startswith("activite"):
            out["activite"] = val
        elif cle.startswith("publication"):
            out["accord"] = norm(val).startswith(("oui", "accept"))
        elif cle.startswith("avis"):
            suite = [val] + [l for l in lignes[i + 1:]]
            out["texte"] = "\n".join(suite)
            break
    return out


def nettoyer(t, n=600):
    t = re.split(r"\n\s*(--|Le .{5,80} a écrit|Envoyé de mon|Sent from)", t or "")[0]
    t = re.sub(r"\S+@\S+|https?://\S+|www\.\S+|(\+33|0)[\s.]?[1-9]([\s.]?\d{2}){4}", "", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t[:n].rsplit(" ", 1)[0] + "…" if len(t) > n else t


def injurieux(t):
    return bool(set(re.findall(r"[a-zàâçéèêëîïôûùüÿ]+", (t or "").lower())) & INJURES)


def depuis_fr(ts):
    if not ts:
        return ""
    d = datetime.fromtimestamp(ts, timezone.utc)
    return f"{MOIS_FR[d.month - 1]} {d.year}"


def demander(subs, biz, state, lien_parrainage, now_ts=None):
    """E-mail « votre avis » un mois après le début de l'abonnement, une seule fois, aux abonnés payants."""
    faits = state.setdefault("avis_demandes", [])
    now_ts = now_ts or datetime.now(timezone.utc).timestamp()
    n, erreurs = 0, []
    for s in subs:
        if s.get("statut") != "active" or not s.get("debut") or now_ts - s["debut"] < 30 * 86400:
            continue
        k = _h(s.get("stripe_subscription") or s["email"])
        if k in faits:
            continue
        html = render("email_avis.html", biz=biz, lien=lien_parrainage(biz, s), offre=biz["offres"][s["plan"]])
        try:
            res = mailer.send(s["email"], f"Un mois avec {biz['nom']} : votre avis compte", html,
                              biz["email_expediteur"], reply_to=biz["email_contact"])
            if not res.get("simulated") or os.getenv("RADAR_DEMO") == "1":
                faits.append(k)
            n += 1
        except Exception as exc:
            erreurs.append(f"demande d'avis non envoyée : {str(exc)[:120]}")
    state["avis_demandes"] = faits[-1000:]
    return n, erreurs


def recevoir(subs, biz, metiers, state, alerter, path=None, session=None):
    """Publie les avis reçus. Retourne (nb publiés, erreurs)."""
    key = (os.getenv("RESEND_INBOUND_KEY") or "").strip()
    adresse = (biz.get("avis_adresse") or biz.get("criteres_adresse") or "").lower()
    if not key or not adresse:
        return 0, []
    vus = state.setdefault("avis_vus", [])
    par_email = {s["email"].lower(): s for s in subs}
    publies, erreurs = charger(path), []
    n = 0
    for e in _resend("/emails/receiving?limit=100", key, session).get("data") or []:
        if e["id"] in vus or adresse not in " ".join(map(str, e.get("to") or [])).lower():
            continue
        if "avis" not in norm(e.get("subject") or ""):
            continue
        vus.append(e["id"])
        exp = _adresse(e.get("from"))
        s = par_email.get(exp)
        try:
            if not s:
                continue  # seuls les abonnés peuvent publier un avis
            corps = _resend(f"/emails/receiving/{e['id']}", key, session)
            d = lire(corps.get("text") or re.sub(r"<[^>]+>", "\n", corps.get("html") or ""))
            texte = nettoyer(d.get("texte"))
            if not d.get("note") or not d.get("accord") or len(texte) < 5:
                continue
            nom = nettoyer(d.get("nom") or "", 40) or "Client"
            avis = {"id": _h(e["id"]), "client": _h(exp), "note": d["note"], "texte": texte, "nom": nom,
                    "activite": nettoyer(d.get("activite") or "", 60) or (metiers.get((s.get("metiers") or [""])[0]) or {}).get("libelle", ""),
                    "depuis": depuis_fr(s.get("debut")), "date": datetime.now(timezone.utc).date().isoformat()}
            if injurieux(texte + " " + nom):
                alerter("Avis client non publié (vocabulaire injurieux)", f"<p>Note {d['note']}/5 :</p><blockquote>{texte}</blockquote>")
                continue
            publies = [a for a in publies if a.get("client") != avis["client"]] + [avis]  # 1 avis par client : le plus récent
            n += 1
            alerter(f"Nouvel avis client publié : {d['note']}/5",
                    f"<p><b>{nom}</b> ({avis['activite']}) : {d['note']}/5</p><blockquote>{texte}</blockquote>"
                    "<p>Il est en ligne sur l'accueil du site. Rien à faire.</p>")
        except Exception as exc:
            erreurs.append(f"avis client non traité : {str(exc)[:120]}")
    if n:
        (path or FICHIER).write_text(json.dumps(publies, ensure_ascii=False, indent=1), encoding="utf-8")
    state["avis_vus"] = vus[-500:]
    return n, erreurs


def resume(publies):
    if not publies:
        return None
    return {"nombre": len(publies), "moyenne": round(sum(a["note"] for a in publies) / len(publies), 1),
            "liste": sorted(publies, key=lambda a: a["date"], reverse=True)}
