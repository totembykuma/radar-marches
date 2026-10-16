"""Option « Dossier prêt à signer » : l'IA prépare la réponse à un appel d'offres, le client relit, chiffre, signe et dépose.

Parcours, 100 % automatique (workflow dossier.yml, toutes les heures en journée) :
1. le client paie (lien Stripe à 199 €, 149 € pour les abonnés Pro) ;
2. il envoie par e-mail à l'adresse de réception Resend (business.yaml : dossier.adresse) le dossier de consultation
   (pièces jointes PDF, Word ou ZIP) et la présentation de son entreprise (formulaire /dossier/envoyer/) ;
3. on rapproche l'e-mail du paiement (même adresse), on extrait le texte du DCE, Claude rédige le dossier ;
4. le client reçoit un fichier Word modifiable, passages à compléter surlignés.
Rien n'est conservé : les pièces sont traitées en mémoire, l'état public (data/state.json) ne garde que des identifiants Stripe.
Inactif tant que l'IA n'est pas autorisée (clé ANTHROPIC_API_KEY + premier client payant) ou que les liens n'existent pas.
"""
import base64
import io
import json
import logging
import os
import re
import time
import zipfile
from datetime import date, datetime, timedelta, timezone

import requests

from . import mailer
from .criteres import _adresse, _resend
from .render import render
from .textutil import norm

log = logging.getLogger(__name__)
STRIPE = "https://api.stripe.com/v1"
MAX_CARACTERES = 400_000        # ≈ 100 000 mots : au-delà, on garde en priorité règlement, CCTP, CCAP
RELANCE_JOURS, REMBOURSEMENT_JOURS = 2, 7
PRIORITE = ("rc", "reglement", "cctp", "ccap", "cahier", "clauses", "bpu", "dpgf", "dqe", "ae", "acte", "annexe")

PROMPT = """Tu es consultant expert en marchés publics français. Tu prépares, pour une TPE/PME, la réponse à l'appel d'offres
dont le dossier de consultation (DCE) est fourni plus bas. Le chef d'entreprise relira, mettra ses prix, signera et déposera lui-même.

Règles absolues :
- N'invente AUCUN fait sur l'entreprise (chiffres, effectif, références, certifications, matériel, noms). Utilise uniquement
  les informations de l'entreprise fournies. Tout ce qui manque s'écrit exactement « [À COMPLÉTER : ce qu'il faut indiquer] ».
- Ne propose aucun prix.
- Suis le plan et les critères de notation du règlement de consultation : le mémoire technique répond point par point
  à ce que l'acheteur note, avec des engagements concrets et vérifiables, dans un français professionnel et simple.
- Si le DCE est incomplet ou illisible, dis-le dans « vigilance » et fais au mieux.

Réponds uniquement par un objet JSON, sans texte autour :
{{
 "acheteur": "...", "objet": "...", "reference": "...", "lots": "lot(s) concerné(s) ou « lot unique »",
 "date_limite": "AAAA-MM-JJ HH:MM ou vide si introuvable",
 "resume": "5 lignes : ce que l'acheteur veut, où, durée, montant si cité",
 "criteres": [{{"critere": "...", "poids": "ex. 60 %", "conseil": "comment marquer des points"}}],
 "pieces": [{{"piece": "...", "ou_la_trouver": "ex. Kbis sur infogreffe.fr", "obligatoire": true}}],
 "vigilance": ["points du règlement qui peuvent faire écarter l'offre (visite obligatoire, signature électronique, format…)"],
 "memoire": [{{"titre": "1. ...", "texte": "paragraphes séparés par des lignes vides"}}],
 "lettre": "lettre de candidature complète (objet, formule, présentation, engagement, signature à compléter)",
 "dume": [{{"rubrique": "ex. Partie II-A Identification", "reponse": "..."}}],
 "references": [{{"client": "...", "objet": "...", "annee": "...", "montant": "..."}}]
}}

INFORMATIONS DE L'ENTREPRISE (fournies par le client) :
{infos}

DOSSIER DE CONSULTATION :
{dce}"""


# ---------------------------------------------------------------- activation et paiements

def actif(biz):
    from .config import dossier_actif
    return dossier_actif(biz)


def _stripe(method, path, data=None, params=None, session=None):
    key = (os.getenv("STRIPE_SECRET_KEY") or "").strip()
    r = (session or requests).request(method, f"{STRIPE}/{path}", data=data, params=params, auth=(key, ""), timeout=60)
    if r.status_code >= 400:
        raise RuntimeError(f"Stripe {path} : {r.status_code}")
    return r.json()


def paiements(biz, session=None):
    """Achats de dossiers payés des 30 derniers jours : {id session: {email, montant, payment_intent, date, avis}}."""
    ids = set((biz.get("dossier") or {}).get("stripe_link_ids") or [])
    if not ids or not (os.getenv("STRIPE_SECRET_KEY") or "").strip():
        return {}
    depuis = int((datetime.now(timezone.utc) - timedelta(days=30)).timestamp())
    out = {}
    for lien in ids:
        data = _stripe("GET", "checkout/sessions", params={"payment_link": lien, "limit": 100, "created[gte]": depuis}, session=session)
        for cs in data.get("data") or []:
            if cs.get("payment_status") != "paid":
                continue
            champs = {f.get("key"): (f.get("text") or {}).get("value") for f in cs.get("custom_fields") or []}
            out[cs["id"]] = {"email": ((cs.get("customer_details") or {}).get("email") or "").lower(),
                             "montant": (cs.get("amount_total") or 0) / 100, "payment_intent": cs.get("payment_intent"),
                             "date": datetime.fromtimestamp(cs.get("created") or 0, timezone.utc).date().isoformat(),
                             "avis": champs.get("avis") or (f"Avis BOAMP n° {cs['client_reference_id']}" if cs.get("client_reference_id") else "")}
    return out


def rembourser(achat, session=None):
    if achat.get("payment_intent"):
        _stripe("POST", "refunds", data={"payment_intent": achat["payment_intent"]}, session=session)


# ---------------------------------------------------------------- lecture des pièces

def extraire_texte(nom, contenu, profondeur=0):
    """Texte d'une pièce du DCE (PDF, Word, ZIP, texte). Liste de (nom, texte)."""
    n = nom.lower()
    try:
        if n.endswith(".zip") and profondeur < 2:
            out = []
            with zipfile.ZipFile(io.BytesIO(contenu)) as z:
                for info in z.infolist():
                    if not info.is_dir() and info.file_size < 60_000_000:
                        out += extraire_texte(info.filename, z.read(info), profondeur + 1)
            return out
        if n.endswith(".pdf"):
            from pypdf import PdfReader
            pages = PdfReader(io.BytesIO(contenu)).pages
            return [(nom, "\n".join((p.extract_text() or "") for p in pages))]
        if n.endswith(".docx"):
            from docx import Document
            d = Document(io.BytesIO(contenu))
            lignes = [p.text for p in d.paragraphs]
            for t in d.tables:
                lignes += [" | ".join(c.text for c in row.cells) for row in t.rows]
            return [(nom, "\n".join(lignes))]
        if n.endswith((".txt", ".csv", ".html", ".htm", ".xml")):
            texte = contenu.decode("utf-8", errors="ignore")
            return [(nom, re.sub(r"<[^>]+>", " ", texte) if n.endswith((".html", ".htm", ".xml")) else texte)]
    except Exception as exc:
        log.warning("Pièce illisible %s : %s", nom, exc)
    return []


def assembler(pieces):
    """Met bout à bout les pièces, les plus utiles d'abord (règlement, CCTP…), dans la limite de MAX_CARACTERES."""
    def rang(p):
        base = norm(p[0].rsplit("/", 1)[-1])
        return next((i for i, k in enumerate(PRIORITE) if re.search(rf"(^|[^a-z]){k}", base)), len(PRIORITE))
    texte = ""
    for nom, t in sorted((p for p in pieces if p[1].strip()), key=rang):
        propre = re.sub(r"[ \t]+", " ", t).strip()
        bloc = f"\n\n===== {nom} =====\n{propre}"
        texte += bloc[: MAX_CARACTERES - len(texte)]
        if len(texte) >= MAX_CARACTERES:
            break
    return texte.strip()


def pieces_jointes(email_id, key, session=None):
    out = []
    for a in (_resend(f"/emails/receiving/{email_id}/attachments?limit=100", key, session).get("data") or []):
        if a.get("download_url"):
            r = (session or requests).get(a["download_url"], timeout=120)
            if r.status_code < 400:
                out += extraire_texte(a.get("filename") or "piece", r.content)
    return out


# ---------------------------------------------------------------- rédaction par l'IA

def generer(dce, infos, modele):
    """Appel Claude (flux, réponses longues). Renvoie le dict du dossier, ou lève une erreur."""
    import anthropic
    client = anthropic.Anthropic(timeout=900, max_retries=3)
    with client.messages.stream(model=modele, max_tokens=64000, output_config={"effort": "high"},
                                messages=[{"role": "user", "content": PROMPT.format(infos=infos or "(aucune)", dce=dce)}]) as flux:
        msg = flux.get_final_message()
    if msg.stop_reason == "refusal":
        raise RuntimeError("l'IA a refusé de traiter ce dossier")
    texte = "".join(b.text for b in msg.content if b.type == "text")
    return json.loads(texte[texte.index("{"): texte.rindex("}") + 1])


# ---------------------------------------------------------------- fichier Word

def construire_docx(d, nom_service):
    from docx import Document
    from docx.enum.text import WD_COLOR_INDEX
    from docx.shared import Pt

    doc = Document()
    doc.styles["Normal"].font.name = "Calibri"
    doc.styles["Normal"].font.size = Pt(11)

    def para(texte, style=None):
        p = doc.add_paragraph(style=style)
        for i, morceau in enumerate(re.split(r"(\[À COMPLÉTER[^\]]*\])", str(texte or ""))):
            run = p.add_run(morceau)
            if i % 2:
                run.font.highlight_color = WD_COLOR_INDEX.YELLOW
        return p

    def bloc(texte):
        for ligne in str(texte or "").split("\n\n"):
            if ligne.strip():
                para(ligne.strip())

    def tableau(entetes, lignes):
        t = doc.add_table(rows=1, cols=len(entetes))
        t.style = "Table Grid"
        for c, h in zip(t.rows[0].cells, entetes):
            c.text = h
        for l in lignes:
            for c, v in zip(t.add_row().cells, l):
                c.text = str(v or "")

    doc.add_heading(f"Réponse à l'appel d'offres : {d.get('objet') or ''}", 0)
    para(f"Acheteur : {d.get('acheteur') or ''}\nRéférence : {d.get('reference') or ''}\nLots : {d.get('lots') or ''}\n"
         f"Date limite de remise des offres : {d.get('date_limite') or '[À COMPLÉTER : vérifier dans le règlement]'}")
    para(f"Préparé par {nom_service}. À relire entièrement : complétez les passages surlignés en jaune, ajoutez vos prix, "
         "puis signez et déposez sur le site de l'acheteur. Vous restez seul responsable de l'offre déposée.")

    doc.add_heading("1. Ce que demande l'acheteur", 1)
    bloc(d.get("resume"))
    doc.add_heading("Critères de notation", 2)
    tableau(["Critère", "Poids", "Comment marquer des points"],
            [(c.get("critere"), c.get("poids"), c.get("conseil")) for c in d.get("criteres") or []])
    if d.get("vigilance"):
        doc.add_heading("Points de vigilance", 2)
        for v in d["vigilance"]:
            para(v, "List Bullet")
    doc.add_heading("Pièces à joindre", 2)
    tableau(["Pièce", "Où la trouver", "Obligatoire"],
            [(p.get("piece"), p.get("ou_la_trouver"), "oui" if p.get("obligatoire") else "si demandé") for p in d.get("pieces") or []])

    doc.add_page_break()
    doc.add_heading("2. Mémoire technique", 1)
    for s in d.get("memoire") or []:
        doc.add_heading(s.get("titre") or "", 2)
        bloc(s.get("texte"))

    doc.add_page_break()
    doc.add_heading("3. Lettre de candidature", 1)
    bloc(d.get("lettre"))

    doc.add_heading("4. DUME (à reporter sur le formulaire en ligne indiqué par l'acheteur)", 1)
    tableau(["Rubrique", "Réponse"], [(x.get("rubrique"), x.get("reponse")) for x in d.get("dume") or []])

    doc.add_heading("5. Références", 1)
    tableau(["Client", "Objet", "Année", "Montant"],
            [(r.get("client"), r.get("objet"), r.get("annee"), r.get("montant")) for r in d.get("references") or []])
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def jours_avant(date_limite, maintenant=None):
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", date_limite or "")
    if not m:
        return None
    return (date(*map(int, m.groups())) - (maintenant or date.today())).days


# ---------------------------------------------------------------- traitement

def _envoyer(biz, to, sujet, cas, pj=None, **kw):
    html = render("email_dossier.html", biz=biz, cas=cas, **kw)
    mailer.send(to, f"{biz['nom']} : {sujet}", html, biz["email_expediteur"], reply_to=biz["email_contact"], attachments=pj)


def traiter(biz, state, alerte=lambda sujet, html: None, session=None, generer_fn=None):
    """Rapproche paiements et e-mails reçus, prépare et envoie les dossiers. Retourne (nb livrés, erreurs)."""
    d = biz.get("dossier") or {}
    key, adresse = (os.getenv("RESEND_INBOUND_KEY") or "").strip(), (d.get("adresse") or "").lower()
    if not actif(biz) or not key:
        return 0, []
    generer_fn = generer_fn or (lambda dce, infos: generer(dce, infos, d.get("modele") or "claude-opus-5-5"))
    suivi = state.setdefault("dossiers", {})          # id session Stripe → {etat, date, livre}
    vus = state.setdefault("dossiers_emails_vus", [])
    achats = paiements(biz, session)
    livres, erreurs = 0, []

    for cs, a in achats.items():                      # nouveaux achats : accusé de réception avec la marche à suivre
        if cs not in suivi:
            suivi[cs] = {"etat": "attente", "date": a["date"]}
            try:
                _envoyer(biz, a["email"], "envoyez-nous le dossier de consultation", "recu")
            except Exception as exc:
                erreurs.append(f"dossier : accusé non envoyé ({str(exc)[:80]})")
            alerte("Vente : un dossier prêt à signer", f"<p>Un client vient d'acheter un dossier ({a['montant']:.0f} €). "
                   "Tout est automatique, rien à faire.</p>")

    for e in _resend("/emails/receiving?limit=100", key, session).get("data") or []:
        if e["id"] in vus or adresse not in " ".join(map(str, e.get("to") or [])).lower():
            continue
        vus.append(e["id"])
        exp = _adresse(e.get("from"))
        cs = next((c for c, a in sorted(achats.items(), key=lambda x: x[1]["date"])
                   if a["email"] == exp and suivi.get(c, {}).get("etat") == "attente"), None)
        try:
            if not cs:
                _envoyer(biz, exp, "aucun dossier en attente", "sans_paiement")
                continue
            corps = _resend(f"/emails/receiving/{e['id']}", key, session)
            infos = (corps.get("text") or re.sub(r"<[^>]+>", "\n", corps.get("html") or ""))[:20_000]
            dce = assembler(pieces_jointes(e["id"], key, session))
            if len(dce) < 2000:
                _envoyer(biz, exp, "il manque le dossier de consultation", "pieces_manquantes")
                continue
            suivi[cs]["etat"] = "redaction"
            dossier = generer_fn(dce, (infos + "\n" + achats[cs]["avis"]).strip())
            reste = jours_avant(dossier.get("date_limite"))
            if reste is not None and reste < int(d.get("delai_min_jours") or 5):
                rembourser(achats[cs], session)
                suivi[cs].update(etat="rembourse")
                _envoyer(biz, exp, "délai trop court, vous êtes remboursé", "delai", reste=max(reste, 0))
                continue
            fichier = construire_docx(dossier, biz["nom"])
            nom = "dossier-" + re.sub(r"[^a-z0-9]+", "-", norm(dossier.get("objet") or "reponse"))[:50].strip("-") + ".docx"
            _envoyer(biz, exp, "votre dossier est prêt", "livre", objet=dossier.get("objet"), date_limite=dossier.get("date_limite"),
                     pj=[{"filename": nom, "content": base64.b64encode(fichier).decode()}])
            suivi[cs].update(etat="livre", livre=date.today().isoformat())
            livres += 1
        except Exception as exc:
            if cs and suivi[cs]["etat"] == "redaction":
                suivi[cs]["essais"] = suivi[cs].get("essais", 0) + 1
                if suivi[cs]["essais"] < 2:          # nouvel essai au prochain passage, sans rien demander au client
                    suivi[cs]["etat"] = "attente"
                    vus.remove(e["id"])
                else:                                # 2 échecs : on rembourse et on prévient, pour ne pas payer l'IA en boucle
                    suivi[cs]["etat"] = "rembourse"
                    try:
                        rembourser(achats[cs], session)
                        _envoyer(biz, exp, "votre dossier n'a pas pu être préparé", "echec")
                    except Exception:
                        pass
                    alerte("Dossier non préparé (client remboursé)", f"<p>Erreur : {str(exc)[:300]}</p>")
            erreurs.append(f"dossier non préparé : {str(exc)[:120]}")

    aujourdhui = date.today()
    for cs, s in suivi.items():                       # relance à 2 jours, remboursement à 7 jours sans pièces
        if s["etat"] != "attente" or cs not in achats:
            continue
        age = (aujourdhui - date.fromisoformat(s["date"])).days
        try:
            if age >= REMBOURSEMENT_JOURS:
                rembourser(achats[cs], session)
                s["etat"] = "rembourse"
                _envoyer(biz, achats[cs]["email"], "remboursement de votre dossier", "rembourse_sans_pieces")
            elif age >= RELANCE_JOURS and not s.get("relance"):
                s["relance"] = True
                _envoyer(biz, achats[cs]["email"], "nous attendons votre dossier de consultation", "relance")
        except Exception as exc:
            erreurs.append(f"dossier : relance/remboursement impossible ({str(exc)[:80]})")

    state["dossiers_emails_vus"] = vus[-500:]
    return livres, erreurs
