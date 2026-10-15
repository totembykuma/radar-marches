"""Point d'entrée. Exemples :
    python -m radar.cli daily      # envoi des alertes + site (tous les matins)
    python -m radar.cli weekly     # rapport hebdo au propriétaire (lundi)
    python -m radar.cli check-api  # diagnostic des champs BOAMP
    python -m radar.cli demo       # test complet hors ligne avec des données d'exemple
"""
import argparse
import hashlib
import json
import time
import logging
import os
import sys
import traceback
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from . import ai, boamp, finance, historique, mailer, prospects, site, subscribers
from .config import ROOT, business, metiers as load_metiers
from .matching import Matcher
from .render import date_fr, date_limite_fr, render

log = logging.getLogger("radar")
DATA = ROOT / "data"
OUT = Path(os.getenv("RADAR_OUT", ROOT / "out"))


def _load_json(path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return default


def _save_json(path, obj):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")


SECRETS = ("RESEND_API_KEY", "STRIPE_SECRET_KEY", "ANTHROPIC_API_KEY", "OWNER_EMAIL")


def safe(msg):
    """Retire toute clé secrète d'un message d'erreur avant de l'écrire dans un journal public."""
    msg = str(msg)
    for name in SECRETS:
        val = (os.getenv(name) or "").strip()
        if len(val) > 4:
            msg = msg.replace(val, "***")
    return msg[:500]


def alert_owner(biz, subject, body_html):
    if biz["email_proprietaire"]:
        try:
            mailer.send(biz["email_proprietaire"].strip(), f"[{biz['nom']}] {subject}", body_html, biz["email_expediteur"])
        except Exception as exc:  # une alerte ratée ne doit pas faire échouer la tâche
            log.error("Alerte propriétaire non envoyée : %s", safe(exc))
    else:
        log.warning("Alerte non envoyée (pas d'e-mail propriétaire) : %s", subject)


def criteres_txt(sub, metiers):
    m = ", ".join(metiers[k]["libelle"] for k in sub["metiers"] if k in metiers) or "—"
    d = ", ".join(sub["departements"]) if sub["departements"] else "toute la France"
    extra = f" · mots-clés : {', '.join(sub['mots_cles'])}" if sub.get("mots_cles") else ""
    return f"{m} · {d}{extra}"


def envoyer_resultats(subs, biz, metiers, matcher, state, records=None):
    """Offre Pro : chaque lundi, « qui a gagné » les marchés de leur métier et départements (7 derniers jours)."""
    pros = [s for s in subs if s["plan"] == "pro" and s["metiers"]]
    if not pros:
        return 0
    semaine = date.today().strftime("%G-W%V")
    faits = state.setdefault("resultats", {})
    if records is None:
        records = prospects.fetch_results(days=7)
    resultats = []
    for rec in records:
        a = boamp.normalize(rec)
        a["metiers"] = matcher.metiers_of(a)
        if a["metiers"]:
            a["titulaires"], a["montant"] = prospects.titulaires(rec), prospects.montant(rec)
            resultats.append(a)
    envoyes = 0
    for s in pros:
        key = hashlib.sha256(s["email"].lower().encode()).hexdigest()[:16]
        if faits.get(key) == semaine:
            continue
        deps = set(s["departements"])
        items = [r for r in resultats if set(r["metiers"]) & set(s["metiers"]) and (not deps or deps & set(r["departements"]))]
        if not items:
            continue
        items.sort(key=lambda r: -(r["montant"] or 0))
        html = render("email_resultats.html", biz=biz, items=items[:40], criteres=criteres_txt(s, metiers))
        res = mailer.send(s["email"], f"Bilan du lundi : {len(items)} marché{'s' if len(items) > 1 else ''} attribué{'s' if len(items) > 1 else ''} dans votre métier",
                          html, biz["email_expediteur"], reply_to=biz.get("email_proprietaire") or biz["email_contact"])
        if not res.get("simulated") or os.getenv("RADAR_DEMO") == "1":
            faits[key] = semaine
        envoyes += 1
    return envoyes


def run_daily(avis_new=None, avis_open=None, subs=None, resultats_records=None, histo=None):
    biz, metiers = business(), load_metiers()
    matcher = Matcher(metiers)
    state = _load_json(DATA / "state.json", {"sent": {}})
    run = {"date": datetime.now(timezone.utc).isoformat(timespec="minutes"), "erreurs": []}

    # 1. Nouveaux avis
    if avis_new is None:
        since = date.today() - timedelta(days=4)  # couvre le week-end ; les doublons sont filtrés
        avis_new = boamp.fetch_since(since)
    for a in avis_new:
        a["metiers"] = matcher.metiers_of(a)
    run["avis_recuperes"] = len(avis_new)
    run["avis_metiers"] = sum(1 for a in avis_new if a["metiers"])

    # 2. Abonnés
    if subs is None:
        subs = subscribers.load_all(biz, metiers)
    run["abonnes"] = len(subs)

    # 2b. Message de bienvenue aux nouveaux abonnés
    known = set(state.setdefault("known", []))
    for s in subs:
        key = hashlib.sha256(s["email"].lower().encode()).hexdigest()[:16]
        if key in known:
            continue
        html = render("email_bienvenue.html", biz=biz, criteres=criteres_txt(s, metiers), sub=s)
        try:
            res = mailer.send(s["email"], f"Bienvenue sur {biz['nom']} : votre veille est active", html,
                              biz["email_expediteur"], reply_to=biz.get("email_proprietaire") or biz["email_contact"])
            if not res.get("simulated") or os.getenv("RADAR_DEMO") == "1":  # en simulation, ne pas marquer comme envoyé
                state["known"].append(key)
            run["bienvenues"] = run.get("bienvenues", 0) + 1
        except Exception as exc:
            run["erreurs"].append(f"bienvenue non envoyée : {safe(exc)}")

    # 3. Sélection par abonné (sans renvoyer ce qui a déjà été envoyé)
    plans, to_enrich = {}, {}
    for s in subs:
        if not s["metiers"] and not s.get("mots_cles"):
            run["erreurs"].append(f"abonné sans métier reconnu ({s['plan']})")
            continue
        key = hashlib.sha256(s["email"].lower().encode()).hexdigest()[:16]
        already = set(state["sent"].get(key, []))
        sel = [a for a in matcher.for_subscriber(avis_new, s) if a["id"] not in already]
        if sel:
            plans[key] = (s, sel)
            for a in sel:
                to_enrich[a["id"]] = a

    # 4. Résumés IA (une seule fois par avis, même s'il intéresse plusieurs abonnés)
    ai.enrich(list(to_enrich.values()), model=biz["ia"]["modele"], batch=biz["ia"]["max_avis_par_appel"])

    # 4b. Fiche « avant de répondre » : ce que l'acheteur a déjà attribué dans le métier de l'abonné
    histo = histo or historique.Historique(metiers)
    n_fiches = 0
    for key, (s, sel) in plans.items():
        for a in sel:
            for m in s["metiers"]:
                if m in (a.get("metiers") or []) and a.get("acheteur_siret"):
                    n_fiches += histo.fiche(a["acheteur_siret"], m) is not None
    histo.sauver()
    run["fiches_acheteur"] = n_fiches

    # 5. Envoi
    sent = 0
    for key, (s, sel) in plans.items():
        sel.sort(key=lambda a: (-(a.get("score") or 0), not a.get("pme"), a["date_limite"] or "9999"))
        for a in sel:
            a["date_limite_fr"] = date_limite_fr(a["date_limite"])
            m = next((m for m in s["metiers"] if m in (a.get("metiers") or [])), None)
            a["fiche"] = histo.fiche(a.get("acheteur_siret"), m) if m and a.get("acheteur_siret") else None
            a["fiche_complete"] = s.get("plan") == "pro"
            a["metier_libelle"] = metiers[m]["libelle"].lower() if m else ""
        html = render("email_digest.html", biz=biz, avis=sel, date_fr=date_fr(), criteres=criteres_txt(s, metiers))
        subject = f"{len(sel)} appel{'s' if len(sel) > 1 else ''} d'offres : {sel[0]['objet'][:60]}"
        try:
            res = mailer.send(s["email"], subject, html, biz["email_expediteur"], reply_to=biz.get("email_proprietaire") or biz["email_contact"])
            if not res.get("simulated") or os.getenv("RADAR_DEMO") == "1":
                state["sent"][key] = (state["sent"].get(key, []) + [a["id"] for a in sel])[-500:]
            sent += 1
        except Exception as exc:
            run["erreurs"].append(f"envoi échoué : {safe(exc)}")
    run["emails_envoyes"] = sent

    # 5b. Offre Pro : bilan « qui a gagné » le lundi
    if date.today().weekday() == 0 or os.getenv("RADAR_FORCE_RESULTATS") == "1":
        try:
            run["bilans_resultats"] = envoyer_resultats(subs, biz, metiers, matcher, state, records=resultats_records)
        except Exception as exc:
            run["erreurs"].append(f"bilan des résultats non envoyé : {safe(exc)}")
    run["abonnes_sans_nouveaute"] = len(subs) - len(plans)

    # 6. Site
    try:
        if avis_open is None:
            avis_open = boamp.fetch_open(days=30)
        res = site.build(avis_open, biz, metiers, matcher, OUT / "site",
                         stats={"total": len(avis_new), "metiers_hits": run["avis_metiers"]})
        run["pages_site"] = res["pages"]
    except Exception as exc:
        run["erreurs"].append(f"site non généré : {safe(exc)}")

    # 7. Contrôles
    weekday = date.today().weekday() < 5
    if weekday and run["avis_recuperes"] == 0:
        run["erreurs"].append("aucun avis BOAMP récupéré un jour ouvré (API en panne ou format changé ?)")
    _save_json(DATA / "state.json", state)
    _save_json(DATA / "runs" / f"{date.today().isoformat()}.json", run)
    if run["erreurs"]:
        alert_owner(biz, "Problème détecté lors de l'envoi quotidien",
                    "<p>L'agent a détecté :</p><ul>" + "".join(f"<li>{e}</li>" for e in run["erreurs"]) +
                    "</ul><p>Aucune action n'est requise si cela ne se reproduit pas demain.</p>")
    log.info("Run quotidien : %s", json.dumps(run, ensure_ascii=False))
    return run


def run_weekly():
    biz, metiers = business(), load_metiers()
    today = date.today()
    month_start = today.replace(day=1)
    rev = finance.stripe_revenue(month_start, today + timedelta(days=1))
    dep, _ = finance.depenses(month_start, today + timedelta(days=1))
    bilan = finance.bilan(rev["ca_brut"], rev["remboursements"], rev["frais_stripe"], dep, biz["fiscalite"])
    subs_raw = finance.list_subscriptions()
    actifs = [s for s in subs_raw if s["status"] == "active"]
    essais = [s for s in subs_raw if s["status"] == "trialing"]
    impayes = [s for s in subs_raw if s["status"] in ("past_due", "unpaid")]
    week_ago = int((datetime.now(timezone.utc) - timedelta(days=7)).timestamp())
    nouveaux = [s for s in subs_raw if s.get("created", 0) >= week_ago]
    resilies = [s for s in subs_raw if (s.get("canceled_at") or 0) >= week_ago]
    runs = [_load_json(DATA / "runs" / f"{(today - timedelta(days=i)).isoformat()}.json", None) for i in range(7)]
    runs = [r for r in runs if r]
    kpi = {
        "mrr": finance.mrr(subs_raw), "abonnes_payants": len(actifs), "en_essai": len(essais), "impayes": len(impayes),
        "nouveaux_7j": len(nouveaux), "resilies_7j": len(resilies),
        "runs_7j": len(runs), "runs_avec_erreur": sum(1 for r in runs if r.get("erreurs")),
        "emails_7j": sum(r.get("emails_envoyes", 0) for r in runs),
        "avis_moyens_jour": round(sum(r.get("avis_recuperes", 0) for r in runs) / max(1, len(runs))),
        "objectif_mensuel_ca": [800, 1300],
    }
    pj = _load_json(DATA / "prospection.json", {}).get("journal", [])
    debut = (today - timedelta(days=7)).isoformat()
    kpi["prospects_contactes_7j"] = sum(j.get("envoyes", 0) for j in pj if j.get("date", "") >= debut)
    kpi["prospects_contactes_total"] = sum(j.get("envoyes", 0) for j in pj)
    kpi["prospects_clics_7j"] = _clics([i for j in pj if j.get("date", "") >= debut for i in j.get("ids", [])])
    decisions = []
    if impayes:
        decisions.append(f"{len(impayes)} abonnement(s) impayé(s) : Stripe relance automatiquement, rien à faire sauf si cela dure.")
    if kpi["runs_7j"] < 5:
        decisions.append("Moins de 5 envois quotidiens cette semaine : vérifier l'onglet Actions de GitHub.")
    if kpi["resilies_7j"] > kpi["nouveaux_7j"] and kpi["resilies_7j"]:
        decisions.append("Plus de résiliations que de nouveaux clients : lire les réponses clients pour comprendre pourquoi.")
    analyse = ai.ask(
        "Tu es le directeur des opérations d'un petit service d'alertes d'appels d'offres par abonnement "
        f"({biz['offres']['essentiel']['prix_mensuel']}€ et {biz['offres']['pro']['prix_mensuel']}€/mois). "
        "Voici les indicateurs de la semaine (JSON). En 5 puces maximum, en français simple : "
        "ce qui va bien, ce qui inquiète, et UNE action concrète prioritaire qui ne demande pas plus de 15 minutes au propriétaire. "
        "Ne promets aucun revenu.\n" + json.dumps({"kpi": kpi, "finance_mois": bilan}, ensure_ascii=False),
        model=biz["ia"]["modele"])
    rows = "".join(f"<tr><td>{k}</td><td style='text-align:right'><b>{v}</b></td></tr>" for k, v in {**kpi, **bilan}.items())
    html = (f"<h2>{biz['nom']} · rapport du {date_fr(today)}</h2>"
            f"<p>Mois en cours : CA {bilan['chiffre_affaires']} € · bénéfice {bilan['benefice_apres_depenses']} € · "
            f"net estimé après cotisations et impôt {bilan['revenu_net']} €.</p>"
            + ("<h3>Décisions / points d'attention</h3><ul>" + "".join(f"<li>{d}</li>" for d in decisions) + "</ul>" if decisions else "<p>Aucune décision requise cette semaine.</p>")
            + (f"<h3>Analyse de l'agent</h3><div style='white-space:pre-wrap'>{analyse}</div>" if analyse else "")
            + f"<h3>Détail</h3><table cellpadding='4'>{rows}</table>")
    _save_json(DATA / "rapports" / f"{today.isoformat()}.json", {"kpi": kpi, "finance": bilan, "decisions": decisions})
    alert_owner(biz, f"Rapport hebdo · MRR {kpi['mrr']} €", html)
    return {"kpi": kpi, "finance": bilan, "decisions": decisions, "analyse": analyse}


def run_abonnes():
    """Vérification : liste les abonnés lus dans Stripe (e-mails masqués, pour des journaux publics)."""
    biz, metiers = business(), load_metiers()
    subs = subscribers.load_all(biz, metiers)

    def mask(e):
        u, _, d = e.partition("@")
        return f"{u[:2]}***@{d}"
    return {"mode_stripe": biz.get("stripe_mode"), "nombre": len(subs),
            "abonnes": [{"email": mask(s["email"]), "plan": s["plan"], "statut": s["statut"], "metiers": s["metiers"],
                         "departements": s["departements"], "mots_cles": s["mots_cles"]} for s in subs]}


def _mask(e):
    u, _, d = e.partition("@")
    return f"{u[:2]}***@{d}"


def run_prospection(apercu=False):
    """Écrit une seule fois aux entreprises qui viennent de gagner un marché de leur métier.
    Inactif par défaut : sans « prospection.actif » ni expéditeur dédié, compte seulement les prospects
    (et, avec apercu=True, envoie au propriétaire l'e-mail tel que le premier prospect le recevrait)."""
    biz, metiers = business(), load_metiers()
    conf = biz.get("prospection") or {}
    matcher = Matcher(metiers)
    state_path = DATA / "prospection.json"
    state = _load_json(state_path, {"contactes": [], "journal": []})
    deja = set(state["contactes"])
    try:
        clients = [s["email"] for s in subscribers.load_all(biz, metiers)]
    except Exception as exc:
        log.warning("Clients Stripe non lus (%s) : prospection annulée par prudence", safe(exc))
        return {"erreur": "clients non lus"}
    cands = prospects.candidates(prospects.fetch_results(days=7), matcher, deja, clients)
    avis_open = [a for a in boamp.fetch_open(days=30) if not matcher.is_award_notice(a)]
    par_metier = {}
    for a in avis_open:
        a["date_limite_fr"] = date_limite_fr(a["date_limite"])
        for m in matcher.metiers_of(a):
            par_metier.setdefault(m, []).append(a)

    def compose(p):
        lst = par_metier.get(p["metier"], [])
        local = [a for a in lst if p["dep"] and p["dep"] in a["departements"]]
        base = biz["site_url"].rstrip("/") + f"/appels-offres/{p['metier']}/"
        if len(local) >= 2:
            n, zone, ex, lien = len(local), f"dans le département {p['dep']}", local[0], base + f"{p['dep']}.html"
        else:
            n, zone, ex, lien = len(lst), "en France", (lst[0] if lst else None), base
        lien += f"?utm_source=prospection&utm_medium=email&utm_campaign={p['metier']}"
        m = metiers[p["metier"]]
        subject = f"{n} marchés publics « {m['libelle'].lower()} » ouverts {zone.replace('dans le département', 'dans le')}"
        html = render("email_prospect.html", p=p, n=n, zone=zone, ex=ex, lien=lien, metier=m, biz=biz)
        return subject, html, n

    resume = {"prospects_trouves": len(cands), "par_metier": {}}
    for c in cands:
        resume["par_metier"][c["metier"]] = resume["par_metier"].get(c["metier"], 0) + 1
    cands = [c for c in cands if compose(c)[2] >= 2]
    resume["avec_au_moins_2_marches"] = len(cands)
    actif = bool(conf.get("actif")) and bool((conf.get("expediteur") or "").strip())
    resume["mode"] = "envoi" if actif else "inactif"

    if not actif:
        if apercu and cands and biz["email_proprietaire"]:
            c = cands[0]
            subject, html, _ = compose(c)
            bandeau = (f"<p style='background:#fff4d6;padding:10px;border-radius:8px;font-family:Arial'>"
                       f"<b>Aperçu, rien n'a été envoyé au prospect.</b> Destinataire prévu : {_mask(c['email'])} "
                       f"({c['nom'] or 'entreprise'}). Prospects trouvés cette semaine : {len(cands)}.<br>"
                       f"Objet : <b>{subject}</b></p>")
            mailer.send(biz["email_proprietaire"].strip(), f"[Aperçu prospection] {subject}", bandeau + html,
                        biz["email_expediteur"])
            resume["apercu_envoye"] = True
        return resume

    try:  # compter les clics sur les liens (réglage du domaine d'envoi, sans compte en plus)
        mailer.activer_suivi_clics(conf["expediteur"].split("@")[-1].strip(" >"))
    except Exception as exc:
        log.warning("Suivi des clics non activé : %s", safe(exc))
    envoyes, ids = 0, []
    for c in cands[: int(conf.get("par_jour") or 20)]:
        subject, html, _ = compose(c)
        reply = biz.get("email_proprietaire") or biz["email_contact"]
        try:
            res = mailer.send(c["email"], subject, html, conf["expediteur"], reply_to=reply,
                              headers={"List-Unsubscribe": f"<mailto:{reply}?subject=STOP>"})
        except Exception as exc:
            log.error("Envoi prospect refusé : %s", safe(exc))
            continue
        if res.get("simulated"):
            continue
        state["contactes"].append(prospects.h(c["email"]))
        if c.get("dom_key"):
            state["contactes"].append(prospects.h(c["dom_key"]))
        envoyes += 1
        if res.get("id"):
            ids.append(res["id"])
    state["journal"].append({"date": date.today().isoformat(), "envoyes": envoyes, "ids": ids})
    state["journal"] = state["journal"][-120:]
    _save_json(state_path, state)
    resume["envoyes"] = envoyes
    return resume


def _clics(ids):
    """Nombre d'e-mails de prospection dont le lien a été cliqué (None si Resend ne répond pas)."""
    n = 0
    for i in ids:
        try:
            n += mailer.dernier_evenement(i) == "clicked"
        except Exception as exc:
            log.warning("Clics non lus : %s", safe(exc))
            return None
        time.sleep(0.6)  # limite Resend : 2 requêtes/seconde
    return n


def run_audit():
    """Qualité du tri : pour chaque métier, d'où vient la correspondance (titre, descripteurs BOAMP ou texte)."""
    metiers = load_metiers()
    matcher = Matcher(metiers)
    avis = [a for a in boamp.fetch_open(days=30) if not matcher.is_award_notice(a)]
    out = {}
    for k in metiers:
        res = {"titre": 0, "descripteurs": [], "texte": []}
        for a in avis:
            if k not in matcher.metiers_of(a):
                continue
            if k in matcher.metiers_of({**a, "descripteurs": [], "texte": ""}):
                res["titre"] += 1
            elif k in matcher.metiers_of({**a, "texte": ""}):
                res["descripteurs"].append(f"{a['objet'][:90]} || {', '.join(a['descripteurs'])[:120]}")
            else:
                res["texte"].append(a["objet"][:110])
        out[k] = {"titre": res["titre"], "descripteurs": len(res["descripteurs"]), "texte": len(res["texte"]),
                  "ex_descripteurs": res["descripteurs"][:15], "ex_texte": res["texte"][:25]}
    return out


def run_demo():
    """Test bout-en-bout sans réseau : avis d'exemple + 2 abonnés fictifs, e-mails écrits dans out/outbox."""
    os.environ["RADAR_DRY_RUN"] = "1"
    os.environ["RADAR_DEMO"] = "1"
    sample = _load_json(ROOT / "tests" / "fixtures" / "boamp_sample.json", [])
    avis = [boamp.normalize(r) for r in sample]
    subs = [
        {"email": "demo-nettoyage@example.com", "plan": "essentiel", "metiers": ["nettoyage"], "departements": ["33", "47"], "mots_cles": []},
        {"email": "demo-pro@example.com", "plan": "pro", "metiers": ["espaces-verts"], "departements": [], "mots_cles": ["aire de jeux"]},
    ]
    global DATA
    DATA = OUT / "demo-data"
    return run_daily(avis_new=avis, avis_open=[dict(a) for a in avis], subs=subs)


def run_essai():
    """Essai à blanc sur les vraies données : aucun e-mail, aucune clé requise. Montre ce que le tri donne."""
    biz, metiers = business(), load_metiers()
    matcher = Matcher(metiers)
    avis = boamp.fetch_since(date.today() - timedelta(days=3))
    appels = [a for a in avis if not matcher.is_award_notice(a)]
    report = {"avis_3_jours": len(avis), "dont_appels_d_offres": len(appels), "par_metier": {}}
    for k in metiers:
        hits = [a for a in appels if k in matcher.metiers_of(a)]
        report["par_metier"][k] = {"nombre": len(hits), "exemples": [a["objet"][:110] for a in hits[:4]]}
    avis_open = boamp.fetch_open(days=30)
    res = site.build(avis_open, biz, metiers, matcher, OUT / "site",
                     stats={"total": len(avis), "metiers_hits": sum(v["nombre"] for v in report["par_metier"].values())})
    report["avis_ouverts_30_jours"] = len(avis_open)
    report["pages_site"] = res["pages"]
    return report


def main(argv=None):
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    p = argparse.ArgumentParser(prog="radar")
    p.add_argument("cmd", choices=["daily", "weekly", "check-api", "demo", "essai", "abonnes", "prospection", "audit"])
    p.add_argument("--apercu", action="store_true", help="prospection : envoie un aperçu au propriétaire")
    args = p.parse_args(argv)
    try:
        if args.cmd == "daily":
            run_daily()
        elif args.cmd == "weekly":
            print(json.dumps(run_weekly(), ensure_ascii=False, indent=1))
        elif args.cmd == "check-api":
            fields = boamp.schema_sample()
            print("Champs BOAMP :", fields)
            missing = [k for k, cands in boamp.FIELDS.items() if not set(cands) & set(fields)]
            print("Champs logiques non trouvés :", missing or "aucun")
            sys.exit(1 if {"id", "objet", "date_parution"} & set(missing) else 0)
        elif args.cmd == "abonnes":
            print(json.dumps(run_abonnes(), ensure_ascii=False, indent=1))
        elif args.cmd == "essai":
            print(json.dumps(run_essai(), ensure_ascii=False, indent=1))
        elif args.cmd == "prospection":
            print(json.dumps(run_prospection(apercu=args.apercu), ensure_ascii=False, indent=1))
        elif args.cmd == "audit":
            print(json.dumps(run_audit(), ensure_ascii=False, indent=1))
        elif args.cmd == "demo":
            print(json.dumps(run_demo(), ensure_ascii=False, indent=1))
    except Exception:
        traceback.print_exc()
        try:
            alert_owner(business(), f"Échec de la tâche '{args.cmd}'", f"<pre>{safe(traceback.format_exc()[-3000:])}</pre>")
        finally:
            sys.exit(1)


if __name__ == "__main__":
    main()
