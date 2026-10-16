"""Offre « Clé en main » : rapport « comment gagner » sur un marché, à la demande du client.

Le client écrit à l'adresse de réception Resend (business.yaml : dossier.rapport_adresse) en donnant la référence
de l'avis BOAMP (bouton des alertes ou espace client). Il peut joindre le dossier de consultation pour plus de précision.
Toutes les heures (workflow dossier.yml), on lit l'avis, l'historique de l'acheteur (DECP) et, si fourni, le DCE ;
Claude rédige le rapport : chances, critères et comment marquer des points, pièces à fournir, pièges, prix de repère,
plan d'action daté. Réservé aux abonnés Clé en main (essai compris), dans la limite de rapports_max par mois.
Rien de personnel n'est publié : l'état (data/dossiers.json) ne garde que des empreintes d'adresses.
"""
import json
import os
import re
from datetime import date

from . import ai, boamp, mailer
from .criteres import _adresse, _resend
from .dossier import _h, assembler, pieces_jointes
from .render import render

MAX_DCE = 150_000   # caractères du DCE transmis (≈ 40 000 mots) : le rapport n'a pas besoin de tout

PROMPT = """Tu es consultant expert en marchés publics français. Une TPE/PME du métier « {metier} » envisage de répondre
à l'appel d'offres ci-dessous. Rédige pour son dirigeant un rapport clair et concret pour maximiser ses chances de gagner.

Règles :
- Appuie-toi uniquement sur l'avis, l'historique de l'acheteur et le dossier de consultation s'il est fourni. N'invente rien :
  si une information manque (critères, pièces…), dis ce qui est habituel pour ce type de marché et précise « à vérifier dans le règlement ».
- Prix : donne un repère chiffré seulement s'il s'appuie sur l'historique (montants des marchés précédents) ou sur un montant cité
  dans l'avis ou le DCE, et explique d'où il vient. Sinon, explique comment fixer son prix (sans inventer de montant).
- Français simple, phrases courtes, aucune formule creuse.

Réponds uniquement par un objet JSON, sans texte autour :
{{
 "en_bref": "3 lignes : ce que veut l'acheteur, où, combien de temps, pour qui ce marché est fait",
 "chances": "bonnes | moyennes | faibles",
 "pourquoi_chances": "2 phrases : taille, concurrence attendue, exigences",
 "criteres": [{{"critere": "...", "poids": "ex. 60 % ou « non précisé »", "comment_gagner_des_points": "conseil concret"}}],
 "prix": {{"repere": "ex. 40 000 à 55 000 € HT sur la durée, ou vide", "source": "d'où vient ce repère", "conseil": "comment se placer : ni trop haut, ni anormalement bas"}},
 "pieces": [{{"piece": "...", "ou_la_trouver": "ex. Kbis sur infogreffe.fr", "obligatoire": true}}],
 "pieges": ["ce qui peut faire écarter l'offre : visite obligatoire, signature électronique, format, délai…"],
 "plan": [{{"quand": "ex. dès aujourd'hui / J-10", "action": "..."}}],
 "a_demander": ["questions utiles à poser à l'acheteur avant la date limite, s'il y en a"]
}}

AVIS :
Objet : {objet}
Acheteur : {acheteur} ({ville})
Date limite : {date_limite}
Texte : {texte}

HISTORIQUE DE CET ACHETEUR DANS CE MÉTIER (marchés attribués, données officielles DECP) :
{histo}

DOSSIER DE CONSULTATION (fourni par le client, peut être vide) :
{dce}"""


def reference(texte):
    """Numéro d'avis BOAMP trouvé dans l'objet ou le corps de l'e-mail (ex. 26-123456, ou dans un lien idweb)."""
    m = re.search(r"(?<![\d-])(\d{2}-\d{3,7})(?![\d-])", texte or "")
    return m.group(1) if m else None


def _histo_txt(fiche):
    if not fiche:
        return "Aucun marché comparable publié."
    lignes = [f"{fiche['n']} marché(s) sur {fiche['mois'] // 12} ans. Montant médian : {fiche.get('montant_median') or 'non publié'} € HT."
              f" Offres reçues en médiane : {fiche.get('offres_median') or 'non publié'}."]
    for d in fiche.get("derniers") or []:
        lignes.append(f"- {d['date']} : {d['objet']} · {d.get('montant') or '?'} € · {d.get('offres') or '?'} offre(s) · gagnant : {d.get('gagnant') or 'non publié'}")
    return "\n".join(lignes)


def generer(avis, fiche, metier, dce, modele, ask_fn=None):
    prompt = PROMPT.format(metier=metier or "non précisé", objet=avis["objet"], acheteur=avis["acheteur"], ville=avis.get("ville") or "",
                           date_limite=avis.get("date_limite") or "non précisée", texte=(avis.get("texte") or "")[:20_000],
                           histo=_histo_txt(fiche), dce=(dce or "")[:MAX_DCE])
    brut = (ask_fn or ai.ask)(prompt, model=modele, max_tokens=6000, timeout=400)
    m = re.search(r"\{.*\}", brut or "", re.S)
    if not m:
        raise RuntimeError("réponse de l'IA illisible")
    return json.loads(m.group(0))


def _envoyer(biz, to, sujet, cas, **kw):
    html = render("email_rapport.html", biz=biz, cas=cas, **kw)
    mailer.send(to, f"{biz['nom']} : {sujet}", html, biz["email_expediteur"], reply_to=biz["email_contact"])


def traiter(biz, state, abonnes, metiers, histo=None, session=None, ask_fn=None, avis_fn=None):
    """Lit les demandes de rapport et envoie les rapports. Retourne (nb envoyés, erreurs)."""
    d = biz.get("dossier") or {}
    key, adresse = (os.getenv("RESEND_INBOUND_KEY") or "").strip(), (d.get("rapport_adresse") or "").lower()
    if not biz.get("cle_actif") or not key or not adresse:
        return 0, []
    avis_fn = avis_fn or (lambda ref: boamp.fetch_one(ref, session))
    o = (biz.get("offres") or {}).get("cle") or {}
    vus = state.setdefault("rapports_vus", [])
    compte = state.setdefault("rapports", {})          # empreinte e-mail → {mois: nombre}
    essais = state.setdefault("rapports_essais", {})
    mois, envoyes, erreurs = date.today().isoformat()[:7], 0, []
    for e in _resend("/emails/receiving?limit=100", key, session).get("data") or []:
        if e["id"] in vus or adresse not in " ".join(map(str, e.get("to") or [])).lower():
            continue
        vus.append(e["id"])
        exp = _adresse(e.get("from"))
        sub = next((s for s in abonnes if s.get("plan") == "cle" and (s.get("email") or "").lower() == exp), None)
        try:
            if not sub:
                _envoyer(biz, exp, "rapport réservé à l'offre Clé en main", "refuse")
                continue
            h = _h(exp)
            if compte.get(h, {}).get(mois, 0) >= int(o.get("rapports_max") or 20):
                _envoyer(biz, exp, "rapports du mois utilisés", "quota", n=o.get("rapports_max") or 20)
                continue
            corps = _resend(f"/emails/receiving/{e['id']}", key, session)
            ref = reference(" ".join([e.get("subject") or "", corps.get("text") or "", corps.get("html") or ""]))
            avis = avis_fn(ref) if ref else None
            if not avis:
                _envoyer(biz, exp, "avis introuvable", "introuvable", ref=ref)
                continue
            metier = next((m for m in sub.get("metiers") or [] if m in (avis.get("metiers") or [])), None) \
                or next(iter(sub.get("metiers") or []), None)
            fiche = histo.fiche(avis.get("acheteur_siret"), metier) if histo and metier and avis.get("acheteur_siret") else None
            dce = assembler(pieces_jointes(e["id"], key, session))
            r = generer(avis, fiche, (metiers.get(metier) or {}).get("libelle", ""), dce, d.get("modele") or "claude-opus-5-5", ask_fn)
            _envoyer(biz, exp, f"comment gagner « {avis['objet'][:60]} »", "rapport", r=r, avis=avis, fiche=fiche,
                     avec_dce=bool(dce), lien_dossier=f"{biz['site_url'].rstrip('/')}/dossier/envoyer/?avis={avis['id']}")
            compte.setdefault(h, {})[mois] = compte.get(h, {}).get(mois, 0) + 1
            envoyes += 1
        except Exception as exc:
            n = essais[e["id"]] = essais.get(e["id"], 0) + 1
            if n < 2:                                  # nouvel essai au prochain passage
                vus.remove(e["id"])
            else:
                try:
                    _envoyer(biz, exp, "rapport non préparé", "echec")
                except Exception:
                    pass
            erreurs.append(f"rapport non préparé : {str(exc)[:120]}")
    state["rapports_vus"] = vus[-500:]
    state["rapports_essais"] = {k: v for k, v in essais.items() if k in vus}
    for h in compte:                                   # on ne garde que le mois en cours
        compte[h] = {mois: compte[h].get(mois, 0)}
    return envoyes, erreurs
