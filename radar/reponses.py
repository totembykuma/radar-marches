"""Agent de réponse aux prospects : lit les réponses aux e-mails de prospection et y répond seul.

Les e-mails de prospection ont pour adresse de réponse `prospection.reponses_adresse` (réception Resend).
À chaque passage de bienvenue.yml (toutes les 10 min) :
- STOP, désinscription ou demande de suppression → l'adresse et son domaine ne sont plus jamais contactés, confirmation courte ;
- réponse automatique (absence, accusé de réception) → ignorée ;
- question simple (prix, essai, métiers, fonctionnement) → l'IA répond, avec seulement les faits fournis ci-dessous ;
- tout le reste (négociation, plainte, demande particulière, doute de l'IA, IA indisponible) → transmis au propriétaire.
Au plus MAX_REPONSES réponses automatiques par adresse, puis transmission : pas de conversation sans fin entre robots.
Inactif tant que RESEND_INBOUND_KEY ou `prospection.reponses_adresse` manquent. Rien de personnel n'est stocké :
seulement des empreintes (hash) d'adresses, comme pour la prospection.
"""
import html as html_lib
import json
import logging
import os
import re
from datetime import date

from markupsafe import Markup

from . import ai, mailer, prospects
from .criteres import _adresse, _resend
from .render import render
from .textutil import norm

log = logging.getLogger(__name__)

MAX_REPONSES = 2          # réponses automatiques par adresse, ensuite le propriétaire prend la main
MAX_PAR_PASSAGE = 15      # garde-fou d'envoi par passage
STOP = re.compile(r"\b(stop|desinscri\w*|desabonn\w*|unsubscribe|supprim\w*|retire[rz]?|effacer|rgpd|"
                  r"ne (plus|pas) (me |nous )?(contacter|recevoir|ecrire)|pas interesse\w*|plus de mail)\b")
AUTO = re.compile(r"\b(absent\w*|absence|out of office|automatic reply|reponse automatique|message automatique|"
                  r"accuse de reception|conges|auto.?reply|ne pas repondre|noreply|no-reply|mailer-daemon)\b")


def _sans_citation(texte):
    """Garde ce que la personne a écrit, sans le message cité en dessous."""
    out = []
    for ligne in (texte or "").splitlines():
        l = ligne.strip()
        if l.startswith(">") or re.match(r"^(le .+ a ecrit|on .+ wrote|-----+ ?(original|message))", norm(l)):
            break
        if re.match(r"^(de|from) ?:", norm(l)) and out:
            break
        out.append(ligne)
    return "\n".join(out).strip()[:3000]


def _faits(biz, metiers):
    """Ce que l'IA a le droit d'affirmer : seulement ces faits, tirés de business.yaml et metiers.yaml."""
    site = biz["site_url"].rstrip("/")
    lignes = [f"Service : {biz['nom']}, alertes e-mail chaque matin (jours ouvrés) des appels d'offres publics (BOAMP) "
              "du métier et des départements choisis, avec résumé, date limite et fiche « avant de répondre » "
              "(qui a gagné les marchés précédents de l'acheteur, à quel prix)."]
    for k, o in (biz.get("offres") or {}).items():
        if k == "cle" and not biz.get("cle_actif"):
            continue
        pal = ", ".join(f"{str(p['prix']).replace('.', ',')} € dès le {p['des_mois']}e mois" for p in o.get("paliers") or [])
        deps = "départements illimités" if (o.get("departements_max") or 0) >= 100 else f"{o.get('departements_max')} département(s)"
        lignes.append(f"Offre {o['nom']} : {o['prix_mensuel']} €/mois les 2 premiers mois{', puis ' + pal if pal else ''} ; "
                      f"{deps}, {o.get('metiers_max') or 1} métier(s)"
                      + (f" ; ou {o['prix_annuel']} € pour 12 mois payés en une fois" if o.get("prix_annuel") else "") + ".")
    lignes += [
        "Essai gratuit 7 jours, avec carte bancaire, un seul essai par entreprise ; sans engagement, résiliation en 2 clics à tout moment.",
        "Pro ajoute : mots-clés personnalisés et bilan concurrentiel.",
        f"Métiers couverts : {', '.join(m['libelle'] for m in metiers.values())}.",
        f"Voir les marchés ouverts et s'abonner : {site}/#tarifs",
        "On ne rédige pas les réponses aux appels d'offres à la place du client (sauf options payantes, ne pas les mentionner).",
        "Paiement par Stripe ; facture envoyée automatiquement.",
    ]
    return "\n".join(lignes)


def _consigne(faits, texte, sujet):
    return ("Tu réponds, au nom de « L'équipe Radar Marchés », à une entreprise qui a répondu à notre e-mail de prospection. "
            "Règles : français, vouvoiement, 3 phrases courtes au maximum, simple et direct, sans formule creuse, pas de liste à puces, pas de gras. Réponds seulement à la question posée. "
            "N'affirme RIEN qui ne soit pas dans les faits ci-dessous (pas de remise, pas de promesse de gain, pas de délai, "
            "pas de nom de personne, pas de numéro de téléphone). Termine par le lien d'abonnement, une seule fois. "
            "Ne signe pas (la signature est ajoutée). "
            "Si le message demande autre chose qu'une information couverte par les faits (négociation, partenariat, plainte, "
            "colère, demande juridique, devis particulier, rendez-vous, appel téléphonique, question technique précise, "
            "ou si tu as le moindre doute), choisis « transmettre ».\n"
            "Réponds UNIQUEMENT avec un objet JSON, sans texte avant ni après, sans bloc de code : {\"action\": \"repondre\" ou \"transmettre\", \"reponse\": \"texte\", "
            "\"motif\": \"en 8 mots, pour le propriétaire\"}.\n\n"
            f"FAITS :\n{faits}\n\nOBJET : {sujet}\nMESSAGE DU PROSPECT :\n{texte}")


def _json(brut):
    m = re.search(r"\{.*\}", brut or "", re.S)
    try:
        d = json.loads(m.group(0)) if m else {}
    except ValueError:
        d = {}
    return d if isinstance(d, dict) else {}


def _html(texte):
    paras = [p.strip() for p in re.split(r"\n\s*\n", texte or "") if p.strip()]
    return Markup("".join(f"<p style='margin:0 0 14px'>{html_lib.escape(p).replace(chr(10), '<br>')}</p>" for p in paras))


def est_refuse(email, state):
    """Vrai si cette adresse (ou son domaine d'entreprise) a demandé à ne plus être contactée."""
    refus = set(state.get("prospects_refus") or [])
    e = (email or "").lower()
    dom = e.split("@")[-1]
    return prospects.h(e) in refus or (dom not in prospects.WEBMAILS and prospects.h(dom) in refus)


def traiter(biz, metiers, state, alert_owner, session=None, ask_fn=None, today=None):
    """Retourne {repondus, transmis, desinscrits, ignores, erreurs}."""
    conf = biz.get("prospection") or {}
    key, adresse = (os.getenv("RESEND_INBOUND_KEY") or "").strip(), (conf.get("reponses_adresse") or "").lower()
    res = {"repondus": 0, "transmis": 0, "desinscrits": 0, "ignores": 0, "erreurs": []}
    if not key or not adresse:
        return res
    today = (today or date.today()).isoformat()
    vus = state.setdefault("reponses_vues", [])
    auteurs = state.setdefault("reponses_auteurs", {})
    refus = state.setdefault("prospects_refus", [])
    journal = state.setdefault("reponses_journal", [])
    try:
        recus = _resend("/emails/receiving?limit=100", key, session).get("data") or []
    except Exception as exc:
        res["erreurs"].append(f"réponses prospects non lues : {str(exc)[:120]}")
        return res
    mois = today[:7]
    usage = state.setdefault("ia_usage", {}).setdefault(mois, {"entree": 0, "sortie": 0, "cout_usd": 0.0})
    budget = (biz.get("ia") or {}).get("budget_mensuel_usd")
    envoyes = 0
    for e in recus:
        if e["id"] in vus or adresse not in " ".join(map(str, e.get("to") or [])).lower():
            continue
        if envoyes >= MAX_PAR_PASSAGE:
            break
        vus.append(e["id"])
        exp, sujet = _adresse(e.get("from")), (e.get("subject") or "").strip()
        if not exp or exp == adresse or not re.match(r"[^@]+@[^@]+\.[a-z]{2,}$", exp):
            continue
        try:
            corps = _resend(f"/emails/receiving/{e['id']}", key, session)
            texte = _sans_citation(corps.get("text") or re.sub(r"<[^>]+>", "\n", corps.get("html") or ""))
            n_sujet, n_texte = norm(sujet), norm(texte)
            sujet_re = sujet if norm(sujet).startswith(("re:", "re :")) else f"Re: {sujet or biz['nom']}"
            if AUTO.search(n_sujet) or AUTO.search(n_texte[:300]) or "noreply" in exp or "no-reply" in exp:
                res["ignores"] += 1
                continue
            hx = prospects.h(exp)
            if STOP.search(n_sujet) or STOP.search(n_texte[:500]):
                dom = exp.split("@")[-1]
                for x in [hx] + ([prospects.h(dom)] if dom not in prospects.WEBMAILS else []):
                    if x not in refus:
                        refus.append(x)
                mailer.send(exp, sujet_re, render("email_reponse_prospect.html", biz=biz, corps=_html(
                    "C'est noté : vous ne recevrez plus aucun message de notre part, et votre adresse a été retirée "
                    "de nos fichiers de prospection.\n\nBonne continuation.")), conf["expediteur"])
                res["desinscrits"] += 1
                journal.append({"date": today, "action": "desinscrit"})
                envoyes += 1
                continue
            d = {}
            ia_ok = biz.get("ia_active") and (budget is None or usage["cout_usd"] < budget)
            if ia_ok and auteurs.get(hx, 0) < MAX_REPONSES and texte:
                d = _json((ask_fn or ai.ask)(_consigne(_faits(biz, metiers), texte, sujet),
                                             model=biz["ia"]["modele"], max_tokens=1500, usage=usage))
            reponse = (d.get("reponse") or "").strip()
            if d.get("action") == "repondre" and 40 <= len(reponse) <= 700:
                mailer.send(exp, sujet_re, render("email_reponse_prospect.html", biz=biz, corps=_html(reponse)),
                            conf["expediteur"], reply_to=adresse)
                auteurs[hx] = auteurs.get(hx, 0) + 1
                res["repondus"] += 1
                journal.append({"date": today, "action": "repondu"})
                envoyes += 1
            else:
                motif = d.get("motif") or ("IA indisponible" if not ia_ok else
                                           "déjà 2 réponses automatiques" if auteurs.get(hx, 0) >= MAX_REPONSES else "à voir")
                alert_owner(biz, f"Un prospect a répondu : {sujet[:60]}", (
                    f"<p><b>À traiter par toi</b> ({html_lib.escape(str(motif))[:120]}). L'agent n'a pas répondu.</p>"
                    f"<p>Prospect : <b>{html_lib.escape(exp)}</b><br>Objet : {html_lib.escape(sujet)}</p>"
                    f"<blockquote style='border-left:3px solid #E1E5EA;margin:0;padding:0 12px'>{_html(texte)}</blockquote>"
                    "<p>Si tu réponds toi-même, ta réponse partira de ton adresse personnelle : "
                    "pour la garder privée, demande plutôt à Claude de répondre pour toi.</p>"))
                res["transmis"] += 1
                journal.append({"date": today, "action": "transmis"})
        except Exception as exc:
            res["erreurs"].append(f"réponse prospect non traitée : {str(exc)[:120]}")
    state["reponses_vues"] = vus[-500:]
    state["reponses_journal"] = journal[-300:]
    return res
