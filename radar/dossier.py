"""Option « Dossier prêt à signer » : l'IA prépare la réponse à un appel d'offres, le client relit, chiffre, signe et dépose.

Parcours, 100 % automatique (workflow dossier.yml, toutes les heures en journée) :
1. le client paie (lien Stripe à 199 €, 149 € pour les abonnés Pro) ;
2. il envoie par e-mail à l'adresse de réception Resend (business.yaml : dossier.adresse) le dossier de consultation
   (pièces jointes PDF, Word ou ZIP) et la présentation de son entreprise (formulaire /dossier/envoyer/) ;
3. on rapproche l'e-mail du paiement (même adresse), on extrait le texte du DCE, Claude rédige le dossier ;
4. le client reçoit un fichier Word modifiable, passages à compléter surlignés.
Offre « Clé en main » (abonnement) : pas de paiement à l'unité, l'e-mail du client suffit, dans la limite des dossiers inclus du mois.
Rien n'est conservé : les pièces sont traitées en mémoire, l'état public (data/state.json) ne garde que des identifiants Stripe.
Inactif tant que l'IA n'est pas autorisée (clé ANTHROPIC_API_KEY + premier client payant) ou que les liens n'existent pas.
"""
import base64
import hashlib
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
- Si seul l'avis officiel est fourni (« AVIS OFFICIEL SEUL »), appuie-toi dessus et sur les usages de ce type de marché,
  écris « [À VÉRIFIER DANS LE RÈGLEMENT] » à chaque point incertain et rappelle en tête de « vigilance » de relire le règlement.
- Les pièces déjà fournies par l'entreprise (Kbis, attestations…) sont listées : écris « fournie » dans « ou_la_trouver ».
- Le client doit finir vite : phrases courtes, paragraphes de 3 phrases au plus, aucun remplissage. Chaque information
  manquante n'est demandée qu'une fois (un même [À COMPLÉTER] ne se répète pas d'une partie à l'autre).
- « resume » : 3 phrases simples, sans jargon.

Réponds uniquement par un objet JSON, sans texte autour :
{{
 "acheteur": "...", "objet": "...", "reference": "...", "lots": "lot(s) concerné(s) ou « lot unique »",
 "date_limite": "AAAA-MM-JJ HH:MM ou vide si introuvable",
 "resume": "3 phrases : ce que l'acheteur veut, où, durée, montant si cité",
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

MOIS = ("janv.", "févr.", "mars", "avr.", "mai", "juin", "juil.", "août", "sept.", "oct.", "nov.", "déc.")
A_FAIRE = r"(\[À (?:COMPLÉTER|VÉRIFIER)[^\]]*\])"


def _date_fr(date_limite):
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})(?:[ T](\d{1,2}):(\d{2}))?", date_limite or "")
    if not m:
        return ""
    a, mo, j, h, mi = m.groups()
    heure = f" à {int(h)} h{'' if mi in (None, '00') else ' ' + mi}" if h else ""
    return f"{int(j)} {MOIS[int(mo) - 1]} {a}{heure}"


def construire_docx(d, nom_service, maintenant=None):
    """Word du dossier, pensé pour être fini vite : page 1 = l'essentiel et la liste de ce qu'il reste à faire,
    puis une partie par page, phrases aérées, passages à compléter surlignés en vert citron."""
    from docx import Document
    from docx.enum.table import WD_TABLE_ALIGNMENT
    from docx.enum.text import WD_BREAK
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Cm, Pt, RGBColor

    NUIT, IVOIRE, CITRON, GRIS, TEXTE2, ROUGE, VERT = "101C2C", "F5F3ED", "B8F36B", "F1F3F5", "5E6A7C", "C83C48", "21845B"
    doc = Document()
    for sec in doc.sections:
        sec.left_margin = sec.right_margin = Cm(2.2)
        sec.top_margin, sec.bottom_margin = Cm(1.8), Cm(1.8)
    st = doc.styles["Normal"]
    st.font.name, st.font.size = "Calibri", Pt(11)
    st.font.color.rgb = RGBColor.from_string(NUIT)
    st.paragraph_format.space_after, st.paragraph_format.line_spacing = Pt(8), 1.25

    def fond(el, couleur):                 # el = cellule ou run
        pr = el._tc.get_or_add_tcPr() if hasattr(el, "_tc") else el._r.get_or_add_rPr()
        shd = OxmlElement("w:shd")
        shd.set(qn("w:val"), "clear"), shd.set(qn("w:color"), "auto"), shd.set(qn("w:fill"), couleur)
        pr.append(shd)

    def bordure(p, couleur, cote="left", taille=24):
        pPr = p._p.get_or_add_pPr()
        bd = pPr.find(qn("w:pBdr"))
        if bd is None:
            bd = OxmlElement("w:pBdr")
            pPr.append(bd)
        b = OxmlElement(f"w:{cote}")
        b.set(qn("w:val"), "single"), b.set(qn("w:sz"), str(taille)), b.set(qn("w:space"), "8"), b.set(qn("w:color"), couleur)
        bd.append(b)

    def sans_bordures(t):
        tblPr = t._tbl.tblPr
        bd = OxmlElement("w:tblBorders")
        for c in ("top", "left", "bottom", "right", "insideH", "insideV"):
            e = OxmlElement(f"w:{c}")
            e.set(qn("w:val"), "single"), e.set(qn("w:sz"), "18"), e.set(qn("w:color"), "FFFFFF")
            bd.append(e)
        tblPr.append(bd)
        mar = OxmlElement("w:tblCellMar")
        for c, v in (("top", 120), ("bottom", 120), ("left", 160), ("right", 160)):
            e = OxmlElement(f"w:{c}")
            e.set(qn("w:w"), str(v)), e.set(qn("w:type"), "dxa")
            mar.append(e)
        tblPr.append(mar)

    def largeurs(t, cms):
        t.autofit = False
        for col, cm in zip(t.columns, cms):
            col.width = Cm(cm)
            for cell in col.cells:
                cell.width = Cm(cm)

    def run(p, texte, taille=None, gras=False, couleur=None):
        r = p.add_run(texte)
        r.bold = gras
        if taille:
            r.font.size = Pt(taille)
        if couleur:
            r.font.color.rgb = RGBColor.from_string(couleur)
        return r

    def para(texte, p=None, **kw):
        """Paragraphe dont les [À COMPLÉTER …] et [À VÉRIFIER …] ressortent en vert citron."""
        p = p or doc.add_paragraph()
        for i, morceau in enumerate(re.split(A_FAIRE, str(texte or ""))):
            if not morceau:
                continue
            r = run(p, morceau, gras=bool(i % 2) or kw.get("gras", False), taille=kw.get("taille"), couleur=kw.get("couleur"))
            if i % 2:
                fond(r, CITRON)
        return p

    def bloc(texte):
        for ligne in re.split(r"\n\s*\n", str(texte or "")):
            if ligne.strip():
                para(ligne.strip())

    def titre(num, texte, sous=""):
        p = doc.add_paragraph()
        p.paragraph_format.space_before, p.paragraph_format.space_after = Pt(4), Pt(2)
        p.paragraph_format.keep_with_next = True
        bordure(p, CITRON, taille=48)
        run(p, f"{num}  " if num else "", 20, True, TEXTE2)
        run(p, texte, 20, True)
        if sous:
            q = doc.add_paragraph()
            q.paragraph_format.space_after = Pt(14)
            run(q, sous, 10.5, couleur=TEXTE2)

    def page():
        doc.add_paragraph().add_run().add_break(WD_BREAK.PAGE)

    def tuiles(cases, couleur=IVOIRE):
        t = doc.add_table(rows=1, cols=len(cases))
        t.alignment = WD_TABLE_ALIGNMENT.CENTER
        sans_bordures(t)
        for c, (petit, grand, sous) in zip(t.rows[0].cells, cases):
            fond(c, couleur)
            p = c.paragraphs[0]
            p.paragraph_format.space_after = Pt(0)
            run(p, petit.upper(), 8, True, TEXTE2)
            q = c.add_paragraph()
            q.paragraph_format.space_after = Pt(0)
            run(q, grand or "—", 13, True)
            if sous:
                run(c.add_paragraph(), sous, 9, couleur=TEXTE2)
        doc.add_paragraph().paragraph_format.space_after = Pt(2)

    def liste(lignes, case="☐"):
        t = doc.add_table(rows=0, cols=2)
        sans_bordures(t)
        for coche, texte, aide in lignes:
            cl = t.add_row().cells
            fond(cl[0], CITRON if coche == "✔" else GRIS)
            run(cl[0].paragraphs[0], coche or case, 13, True, VERT if coche == "✔" else NUIT)
            fond(cl[1], GRIS)
            p = cl[1].paragraphs[0]
            p.paragraph_format.space_after = Pt(0)
            para(texte, p, gras=True)
            if aide:
                q = cl[1].add_paragraph()
                q.paragraph_format.space_after = Pt(0)
                run(q, aide, 9.5, couleur=TEXTE2)
        largeurs(t, (1.2, 15.4))
        doc.add_paragraph().paragraph_format.space_after = Pt(2)

    def encadre(lignes, couleur, signe):
        for l in lignes:
            p = para(f"{signe}  {l}")
            bordure(p, couleur, taille=24)
            p.paragraph_format.left_indent = Cm(0.3)

    tout = json.dumps(d, ensure_ascii=False)
    nb_trous = len(re.findall(A_FAIRE, tout))
    pieces = d.get("pieces") or []
    fournies = [p for p in pieces if re.search(r"\bfourni(?:e|es|s)?\b", f"{p.get('ou_la_trouver')}".lower())]
    a_joindre = [p for p in pieces if p not in fournies and p.get("obligatoire")]
    minutes = max(15, 5 * round((10 + 1.5 * nb_trous + 2 * len(a_joindre)) / 5))
    jours = jours_avant(d.get("date_limite"), maintenant)
    quand = _date_fr(d.get("date_limite"))

    # ---- page 1 : l'essentiel
    t = doc.add_table(rows=1, cols=1)
    sans_bordures(t)
    c = t.rows[0].cells[0]
    fond(c, NUIT)
    run(c.paragraphs[0], f"{nom_service.upper()}  ·  DOSSIER PRÊT À SIGNER", 8.5, True, CITRON)
    p = c.add_paragraph()
    p.paragraph_format.space_after, p.paragraph_format.line_spacing = Pt(4), 1.05
    run(p, d.get("objet") or "Votre réponse à l'appel d'offres", 22, True, "FFFFFF")
    run(c.add_paragraph(), d.get("acheteur") or "", 11, couleur=IVOIRE)
    doc.add_paragraph().paragraph_format.space_after = Pt(0)

    tuiles([("Date limite", quand or "À vérifier", f"dans {jours} jours" if jours is not None and jours >= 0 else ""),
            ("Votre temps", f"≈ {minutes} min", "pour finir ce dossier"),
            ("À compléter", str(nb_trous), "passages en vert")])

    titre("", "Il vous reste 4 choses à faire", "Le reste est déjà rédigé. Cochez au fur et à mesure.")
    liste([("", f"Compléter les {nb_trous} passages surlignés en vert" if nb_trous else "Relire le dossier",
            "Tapez directement dessus, puis enlevez le surlignage."),
           ("", "Ajouter vos prix", "Dans le bordereau de prix de l'acheteur. Nous n'en proposons jamais."),
           ("", f"Joindre {len(a_joindre)} pièce(s) obligatoire(s)" if a_joindre else "Vérifier vos pièces",
            "La liste est page 2." + (f" {len(fournies)} déjà fournie(s)." if fournies else "")),
           ("", "Signer et déposer" + (f" avant le {quand}" if quand else ""),
            "Sur le site indiqué par l'acheteur. Vous restez seul responsable de l'offre.")])

    titre("", "En bref")
    bloc(d.get("resume"))
    if d.get("reference") or d.get("lots"):
        run(doc.add_paragraph(), f"Référence {d.get('reference') or '—'}  ·  {d.get('lots') or 'lot unique'}", 9.5, couleur=TEXTE2)

    # ---- page 2 : gagner des points, pièges, pièces
    page()
    titre("1", "Comment marquer des points", "Ce que l'acheteur note, et comment le mémoire y répond.")
    crit = d.get("criteres") or []
    if crit:
        t = doc.add_table(rows=0, cols=2)
        sans_bordures(t)
        for x in crit:
            cl = t.add_row().cells
            fond(cl[0], NUIT)
            run(cl[0].paragraphs[0], x.get("poids") or "—", 15, True, CITRON)
            fond(cl[1], IVOIRE)
            run(cl[1].paragraphs[0], x.get("critere") or "", 11, True)
            if x.get("conseil"):
                para(x["conseil"], cl[1].add_paragraph(), taille=10)
        largeurs(t, (2.6, 14))
        doc.add_paragraph().paragraph_format.space_after = Pt(2)
    if d.get("vigilance"):
        titre("", "Attention", "Ce qui peut faire écarter une offre.")
        encadre(d["vigilance"], ROUGE, "!")
    titre("", "Vos pièces")
    liste([("✔" if p in fournies else "", p.get("piece") or "",
            ("Déjà fournie" if p in fournies else (p.get("ou_la_trouver") or "")) + ("" if p.get("obligatoire") else " · si demandé"))
           for p in pieces])

    # ---- une partie par page
    page()
    titre("2", "Mémoire technique", "Votre présentation, point par point, dans l'ordre de la notation.")
    for s in d.get("memoire") or []:
        p = para(s.get("titre") or "", gras=True, taille=13)
        p.paragraph_format.space_before, p.paragraph_format.keep_with_next = Pt(12), True
        bloc(s.get("texte"))

    page()
    titre("3", "Lettre de candidature", "Prête à imprimer sur votre papier à en-tête.")
    bloc(d.get("lettre"))

    if d.get("dume"):
        page()
        titre("4", "DUME", "À recopier sur le formulaire en ligne indiqué par l'acheteur.")
        t = doc.add_table(rows=0, cols=2)
        sans_bordures(t)
        for i, x in enumerate(d["dume"]):
            cl = t.add_row().cells
            for cell in cl:
                fond(cell, GRIS if i % 2 else IVOIRE)
            run(cl[0].paragraphs[0], x.get("rubrique") or "", 9.5, True, TEXTE2)
            para(x.get("reponse"), cl[1].paragraphs[0])
        largeurs(t, (5.5, 11.1))

    if d.get("references"):
        titre("5", "Vos références")
        t = doc.add_table(rows=1, cols=4)
        sans_bordures(t)
        for cell, h in zip(t.rows[0].cells, ("Client", "Objet", "Année", "Montant")):
            fond(cell, NUIT)
            run(cell.paragraphs[0], h, 9, True, CITRON)
        for i, r in enumerate(d["references"]):
            for cell, v in zip(t.add_row().cells, (r.get("client"), r.get("objet"), r.get("annee"), r.get("montant"))):
                fond(cell, GRIS if i % 2 else IVOIRE)
                para(v, cell.paragraphs[0], taille=10)

    pied = doc.sections[0].footer.paragraphs[0]
    run(pied, f"{nom_service} · dossier préparé pour vous, à relire, compléter et signer", 8, couleur=TEXTE2)
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


def _h(email):
    return hashlib.sha256(email.lower().encode()).hexdigest()[:16]


def inclus_utilises(suivi, email, mois=None):
    """Dossiers inclus (offre Clé en main) déjà livrés ce mois-ci à ce client."""
    mois, h = mois or date.today().isoformat()[:7], _h(email)
    return sum(1 for s in suivi.values() if s.get("cle") == h and (s.get("livre") or "").startswith(mois))


def traiter(biz, state, alerte=lambda sujet, html: None, session=None, generer_fn=None, abonnes=()):
    """Rapproche paiements et e-mails reçus, prépare et envoie les dossiers. Retourne (nb livrés, erreurs).
    abonnes : clients de l'offre Clé en main ; leurs dossiers inclus se demandent par e-mail, sans paiement."""
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
        cle = next((s for s in abonnes if s.get("plan") == "cle" and (s.get("email") or "").lower() == exp), None)
        if not cs and cle:                            # offre Clé en main : dossier inclus, sans paiement
            cs = "cle-" + e["id"]
            if cs not in suivi:
                o = (biz.get("offres") or {}).get("cle") or {}
                try:
                    if cle.get("statut") != "active":
                        _envoyer(biz, exp, "vos dossiers inclus démarrent au 1er paiement", "cle_essai", inclus=True)
                        continue
                    if inclus_utilises(suivi, exp) >= int(o.get("dossiers_inclus") or 2):
                        _envoyer(biz, exp, "vos dossiers inclus du mois sont utilisés", "cle_quota", inclus=True,
                                 n=o.get("dossiers_inclus") or 2)
                        continue
                except Exception as exc:
                    erreurs.append(f"dossier inclus : réponse non envoyée ({str(exc)[:80]})")
                    continue
                suivi[cs] = {"etat": "attente", "date": date.today().isoformat(), "cle": _h(exp)}
            achats[cs] = {"email": exp, "montant": 0, "payment_intent": None, "date": suivi[cs]["date"], "avis": "", "inclus": True}
        inclus = bool(cs and achats[cs].get("inclus"))
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
                _envoyer(biz, exp, "délai trop court, dossier non décompté" if inclus else "délai trop court, vous êtes remboursé",
                         "delai", reste=max(reste, 0), inclus=inclus)
                continue
            fichier = construire_docx(dossier, biz["nom"])
            nom = "dossier-" + re.sub(r"[^a-z0-9]+", "-", norm(dossier.get("objet") or "reponse"))[:50].strip("-") + ".docx"
            _envoyer(biz, exp, "votre dossier est prêt", "livre", objet=dossier.get("objet"), date_limite=dossier.get("date_limite"), inclus=inclus,
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
                        _envoyer(biz, exp, "votre dossier n'a pas pu être préparé", "echec", inclus=inclus)
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
