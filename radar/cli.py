"""Point d'entrée. Exemples :
    python -m radar.cli daily      # envoi des alertes + site (tous les matins)
    python -m radar.cli weekly     # bilan du dimanche au propriétaire
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

import requests
from markupsafe import Markup
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from . import avis, criteres, gratuit, ai, dossier, parrainage, boamp, finance, historique, barometre, laureats, mailer, prospects, site, subscribers
from . import anti_abus, compte, comptes, rapport
from .config import ROOT, business, cle_actif, dossier_actif, metiers as load_metiers
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
            html = render("email_interne.html", biz=biz, contenu=Markup(body_html))
            mailer.send(biz["email_proprietaire"].strip(), f"[{biz['nom']}] {subject}", html, biz["email_expediteur"])
        except Exception as exc:  # une alerte ratée ne doit pas faire échouer la tâche
            log.error("Alerte propriétaire non envoyée : %s", safe(exc))
    else:
        log.warning("Alerte non envoyée (pas d'e-mail propriétaire) : %s", subject)


def essais_uniques(biz, run):
    """Un seul essai par carte ou adresse : arrête chez Stripe les essais en double (avant la bienvenue)."""
    try:
        res = anti_abus.appliquer(biz, mailer=mailer)
    except Exception as exc:  # Stripe indisponible : on réessaie au passage suivant, sans bloquer la bienvenue
        run["erreurs"].append(f"contrôle des essais impossible : {safe(exc)}")
        return
    run["essais_refuses"] = run.get("essais_refuses", 0) + res["essais_refuses"]
    run["erreurs"] += res["erreurs"]
    if res["essais_refuses"]:
        alert_owner(biz, f"{res['essais_refuses']} essai(s) en double arrêté(s)",
                    f"<p>{res['essais_refuses']} nouvel(s) abonnement(s) utilisai(en)t une carte ou une adresse ayant déjà eu un essai : "
                    "l'essai a été arrêté, le client paie dès aujourd'hui et a reçu un e-mail d'explication. Rien à faire.</p>")


def comptes_espace(biz, metiers, subs, run, state=None):
    """Comptes avec mot de passe (Supabase) : recopie les abonnements Stripe et applique les critères modifiés
    dans l'espace client. Renvoie le client robot (ou None si les comptes ne sont pas configurés)."""
    try:
        supa = comptes.connexion()
    except Exception as exc:
        run["erreurs"].append(f"comptes clients injoignables : {safe(exc)}")
        return None
    if not supa:
        return None
    try:
        if os.getenv("STRIPE_SECRET_KEY", "").strip():   # sans clé Stripe, on ne touche à aucun droit
            st = comptes.synchroniser(supa, subs, biz, metiers)
            utilisateurs = st.pop("_utilisateurs")
            run["comptes"] = st
            if state is not None:   # clients d'avant les mots de passe : 1 invitation chacun
                n, errs = comptes.inviter(subs, biz, utilisateurs, state)
                run["invitations_compte"], run["erreurs"] = n, run["erreurs"] + errs
        n, errs = comptes.pousser_criteres(supa, subs, biz, metiers, criteres_txt, criteres._stripe_update)
        if n:
            run["criteres_espace"] = n
        run["erreurs"] += errs
    except Exception as exc:
        run["erreurs"].append(f"comptes clients non synchronisés : {safe(exc)}")
    return supa


def lien_dossier(biz, sub):
    """Bouton « Préparer ma réponse » des alertes : prix abonné Pro pour les Pro, sinon prix normal ; rien si l'option est inactive.
    Offre Clé en main : dossiers inclus (formulaire d'envoi des pièces) et bouton « Rapport : comment gagner »."""
    if not biz.get("dossier_actif"):
        return None
    d = biz["dossier"]
    if sub.get("plan") == "cle":
        return {"inclus": True, "url": biz["site_url"].rstrip("/") + "/dossier/envoyer/", "rapport": d.get("rapport_adresse") or "",
                "email": sub.get("email") or ""}
    pro = sub.get("plan") == "pro" and d.get("lien_pro")
    return {"url": d["lien_pro"] if pro else d["lien_paiement"], "prix": d["prix_pro"] if pro else d["prix"],
            "email": sub.get("email") or ""}


def criteres_txt(sub, metiers):
    m = ", ".join(metiers[k]["libelle"] for k in sub["metiers"] if k in metiers) or "—"
    d = ", ".join(sub["departements"]) if sub["departements"] else "toute la France"
    extra = f" · mots-clés : {', '.join(sub['mots_cles'])}" if sub.get("mots_cles") else ""
    return f"{m} · {d}{extra}"


def envoyer_resultats(subs, biz, metiers, matcher, state, records=None):
    """Offre Pro : chaque lundi, « qui a gagné » les marchés de leur métier et départements (7 derniers jours)."""
    pros = [s for s in subs if s["plan"] in subscribers.PLANS_PRO and s["metiers"]]
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


def envoyer_bienvenues(subs, biz, metiers, state, run):
    """Envoie le message de bienvenue aux abonnés pas encore connus (clé = empreinte de l'e-mail)."""
    known = set(state.setdefault("known", []))
    for s in subs:
        key = hashlib.sha256(s["email"].lower().encode()).hexdigest()[:16]
        if key in known:
            continue
        html = render("email_bienvenue.html", biz=biz, criteres=criteres_txt(s, metiers), sub=s,
                      lien=parrainage.lien(biz, s), lien_compte=compte.lien(biz, s, metiers))
        try:
            res = mailer.send(s["email"], f"Bienvenue sur {biz['nom']} : votre veille est active", html,
                              biz["email_expediteur"], reply_to=biz.get("email_proprietaire") or biz["email_contact"])
            if not res.get("simulated") or os.getenv("RADAR_DEMO") == "1":  # en simulation, ne pas marquer comme envoyé
                state["known"].append(key)
                known.add(key)
                state.setdefault("compte_lien_envoye", []).append(key)  # le lien de l'espace est dans la bienvenue
            run["bienvenues"] = run.get("bienvenues", 0) + 1
        except Exception as exc:
            run["erreurs"].append(f"bienvenue non envoyée : {safe(exc)}")


def run_bienvenue():
    """Passage rapide (toutes les 10 min) : bienvenue aux nouveaux abonnés sans attendre le lendemain matin."""
    biz, metiers = business(), load_metiers()
    state = _load_json(DATA / "state.json", {"sent": {}})
    biz["ia_active"] = bool(os.getenv("ANTHROPIC_API_KEY", "").strip()) and bool(state.get("premier_client_payant"))
    biz["dossier_actif"], biz["cle_actif"] = dossier_actif(biz), cle_actif(biz)
    run = {"erreurs": []}
    essais_uniques(biz, run)
    subs = subscribers.load_all(biz, metiers)
    envoyer_bienvenues(subs, biz, metiers, state, run)
    n, errs = compte.traiter(subs, biz, metiers, state)
    run["erreurs"] += errs
    comptes_espace(biz, metiers, subs, run, state)
    if run.get("bienvenues") or n or state.get("compte_vus") or run.get("invitations_compte"):
        _save_json(DATA / "state.json", state)
    return {"bienvenues": run.get("bienvenues", 0), "liens_espace_client": n, "comptes": run.get("comptes"),
            "criteres_espace": run.get("criteres_espace", 0), "essais_refuses": run.get("essais_refuses", 0), "erreurs": run["erreurs"]}


def _historique_collecte(n=10):
    """Nombre d'avis récupérés lors des derniers envois quotidiens (hors aujourd'hui)."""
    vals = []
    for f in sorted((DATA / "runs").glob("*.json"), reverse=True):
        if f.stem == date.today().isoformat() or len(vals) >= n:
            continue
        r = _load_json(f, {})
        if r.get("avis_recuperes") and not r.get("collecte_anormale"):
            vals.append(r["avis_recuperes"])
    return vals


def collecte_anormale(avis_new, historique, seuil=0.35):
    """Motif si la collecte du jour semble incomplète ou mal lue, sinon None."""
    if avis_new:
        vides = sum(1 for a in avis_new if not a.get("id") or not a.get("objet"))
        if vides > len(avis_new) / 2:
            return f"{vides} avis sur {len(avis_new)} sans numéro ou sans objet (format BOAMP changé ?)"
    if len(historique) >= 3:
        med = sorted(historique)[len(historique) // 2]
        if med >= 200 and len(avis_new) < seuil * med:
            return f"seulement {len(avis_new)} avis récupérés contre {med} d'habitude (collecte incomplète ?)"
    return None


def resumer_ia(avis_list, biz, metiers, state, run):
    mois = date.today().strftime("%Y-%m")
    usage = state.setdefault("ia_usage", {}).setdefault(mois, {"entree": 0, "sortie": 0, "cout_usd": 0.0})
    budget = biz["ia"].get("budget_mensuel_usd")
    reste = None if budget is None else max(0.0, budget - usage["cout_usd"])
    cache = _load_json(DATA / "ia_cache.json", {})
    for a in avis_list:
        a["metiers_libelles"] = [metiers[m]["libelle"] for m in a.get("metiers") or [] if m in metiers]
    st = ai.enrich(avis_list, model=biz["ia"]["modele"], batch=biz["ia"]["max_avis_par_appel"], cache=cache, budget_usd=reste) or {}
    for k in ("entree", "sortie"):
        usage[k] += st.get(k, 0)
    usage["cout_usd"] = round(usage["cout_usd"] + st.get("cout_usd", 0), 4)
    limite = (date.today() - timedelta(days=45)).isoformat()
    for a in avis_list:
        if a["id"] in cache:
            cache[a["id"]].setdefault("jour", date.today().isoformat())
    _save_json(DATA / "ia_cache.json", {k: v for k, v in cache.items() if v.get("jour", "") >= limite})
    run["ia"] = {**st, "hors_metier": sum(1 for a in avis_list if a.get("hors_metier")), "avis": len(avis_list)}
    if st.get("lots_echoues") and not st.get("lots_ok"):
        run["erreurs"].append("résumés IA indisponibles aujourd'hui : alertes envoyées sans résumé")
    if (st.get("budget_atteint") or reste == 0.0) and usage.get("alerte_budget") != mois:
        usage["alerte_budget"] = mois
        alert_owner(biz, "Budget IA du mois atteint", f"<p>Les résumés IA ont coûté {usage['cout_usd']} $ ce mois-ci "
                    f"(plafond {budget} $). Les alertes continuent sans résumé jusqu'au 1er du mois prochain. Rien à faire.</p>")


def run_veille():
    """Contrôle de 10 h : l'envoi quotidien a-t-il tourné ce matin ? Sinon prévenir et demander une relance."""
    biz = business()
    jour = date.today()
    if jour.weekday() >= 5 or (DATA / "runs" / f"{jour.isoformat()}.json").exists():
        return {"ok": True}
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "relancer").write_text("daily")
    alert_owner(biz, "Envoi quotidien absent ce matin : relancé automatiquement",
                "<p>L'envoi de 6 h 20 n'a pas tourné (retard ou panne de GitHub). Il vient d'être relancé tout seul. "
                "Rien à faire ; si ce message revient plusieurs jours de suite, dites-le à Claude.</p>")
    return {"ok": False, "relance": True}


def run_daily(avis_new=None, avis_open=None, subs=None, resultats_records=None, histo=None):
    biz, metiers = business(), load_metiers()
    matcher = Matcher(metiers)
    state = _load_json(DATA / "state.json", {"sent": {}})
    run = {"date": datetime.now(timezone.utc).isoformat(timespec="minutes"), "erreurs": []}

    # 1. Nouveaux avis (3 tentatives ; si la collecte échoue ou paraît anormale, les alertes attendent le lendemain)
    collecte_ko = None
    if avis_new is None:
        since = date.today() - timedelta(days=4)  # couvre le week-end ; les doublons sont filtrés
        try:
            avis_new = boamp.fetch_since(since)
        except Exception as exc:
            avis_new, collecte_ko = [], f"BOAMP injoignable après 3 tentatives : {safe(exc)}"
        jour = date.today().isoformat()  # jamais d'avis déjà clos (rectificatifs publiés après la date limite)
        avis_new = [a for a in avis_new if not a["date_limite"] or a["date_limite"][:10] >= jour]
        collecte_ko = collecte_ko or collecte_anormale(avis_new, _historique_collecte())
    for a in avis_new:
        a["metiers"] = matcher.metiers_of(a)
    run["avis_recuperes"] = len(avis_new)
    run["avis_metiers"] = sum(1 for a in avis_new if a["metiers"])

    # 2. Abonnés
    if subs is None:
        essais_uniques(biz, run)
        subs = subscribers.load_all(biz, metiers)
    run["abonnes"] = sum(1 for s in subs if s.get("source") != "interne")

    # 2a. Changements de critères demandés via le formulaire du site (lecture automatique)
    try:
        n, errs = criteres.traiter(subs, biz, metiers, state, criteres_txt)
        if n:
            run["criteres_modifies"] = n
        run["erreurs"] += errs
    except Exception as exc:
        run["erreurs"].append(f"lecture des demandes de critères impossible : {safe(exc)}")

    # 2a. bis Critères modifiés dans l'espace client (mot de passe) : copiés dans Stripe avant la sélection du jour
    supa = comptes_espace(biz, metiers, subs, run, state)

    # 2a bis. Parrainage : 1 mois offert au filleul (dès l'inscription) et au parrain (quand le filleul devient payant)
    try:
        n, errs = parrainage.traiter(subs, biz, state)
        if n:
            run["mois_offerts_parrainage"] = n
        run["erreurs"] += errs
    except Exception as exc:
        run["erreurs"].append(f"parrainage non traité : {safe(exc)}")

    # 2a ter. Premier client payant : les résumés IA deviennent possibles (payés par ce client), on prévient le propriétaire.
    if not state.get("premier_client_payant") and any(s.get("source") == "stripe" and s.get("statut") == "active" for s in subs):
        state["premier_client_payant"] = date.today().isoformat()
        run["premier_client_payant"] = True
        if not os.getenv("ANTHROPIC_API_KEY", "").strip():
            alert_owner(biz, "Premier client payant : vous pouvez activer les résumés IA", render("email_premier_client.html", biz=biz))
    biz["ia_active"] = bool(os.getenv("ANTHROPIC_API_KEY", "").strip()) and bool(state.get("premier_client_payant"))
    biz["dossier_actif"], biz["cle_actif"] = dossier_actif(biz), cle_actif(biz)

    # 2b. Message de bienvenue aux nouveaux abonnés (aussi envoyé toutes les 10 min par bienvenue.yml)
    envoyer_bienvenues(subs, biz, metiers, state, run)
    n, errs = compte.traiter(subs, biz, metiers, state)
    run["liens_espace_client"], run["erreurs"] = n, run["erreurs"] + errs

    # 2c. Rappel 3 jours avant la fin de l'essai (une seule fois par essai)
    rappels = state.setdefault("rappel_essai", [])
    now_ts = datetime.now(timezone.utc).timestamp()
    for s in subs:
        fin = s.get("fin_essai")
        if not fin or not 0 < fin - now_ts <= 3.5 * 86400:
            continue
        fin_fr = datetime.fromtimestamp(fin, timezone.utc).strftime("%d/%m/%Y")
        key = hashlib.sha256(f"{s['email'].lower()}|{fin_fr}".encode()).hexdigest()[:16]
        if key in rappels:
            continue
        offre = biz["offres"][s["plan"]]
        html = render("email_fin_essai.html", biz=biz, offre=offre, fin_fr=fin_fr, criteres=criteres_txt(s, metiers),
                      lien_compte=compte.lien(biz, s, metiers))
        try:
            res = mailer.send(s["email"], f"Votre essai {biz['nom']} se termine le {fin_fr}", html,
                              biz["email_expediteur"], reply_to=biz.get("email_proprietaire") or biz["email_contact"])
            if not res.get("simulated") or os.getenv("RADAR_DEMO") == "1":
                rappels.append(key)
            run["rappels_essai"] = run.get("rappels_essai", 0) + 1
        except Exception as exc:
            run["erreurs"].append(f"rappel de fin d'essai non envoyé : {safe(exc)}")
    state["rappel_essai"] = rappels[-500:]

    # 2d. Avis clients : demande à 1 mois d'abonnement, publication des avis reçus via le formulaire du site
    try:
        n, errs = avis.demander(subs, biz, state, parrainage.lien)
        if n:
            run["demandes_avis"] = n
        run["erreurs"] += errs
        n, errs = avis.recevoir(subs, biz, metiers, state, lambda sujet, html: alert_owner(biz, sujet, html))
        if n:
            run["avis_publies"] = n
        run["erreurs"] += errs
    except Exception as exc:
        run["erreurs"].append(f"avis clients non traités : {safe(exc)}")

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

    # 4. Résumés IA (une seule fois par avis : résultats gardés dans data/ia_cache.json, coût suivi par mois, budget plafonné)
    if biz["ia_active"] and to_enrich and not collecte_ko:
        resumer_ia(list(to_enrich.values()), biz, metiers, state, run)

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

    # 5. Envoi (suspendu si la collecte a échoué : rien n'est marqué envoyé, la fenêtre de 4 jours rattrape le lendemain)
    sent = 0
    if collecte_ko:
        run["collecte_anormale"] = collecte_ko
        run["erreurs"].append(f"alertes clients reportées à demain : {collecte_ko}")
        plans_envoi = {}
    else:
        plans_envoi = plans
    for key, (s, sel) in plans_envoi.items():
        sel.sort(key=lambda a: (-(a.get("score") or 0), not a.get("pme"), a["date_limite"] or "9999"))
        for a in sel:
            a["date_limite_fr"] = date_limite_fr(a["date_limite"])
            m = next((m for m in s["metiers"] if m in (a.get("metiers") or [])), None)
            a["fiche"] = histo.fiche(a.get("acheteur_siret"), m) if m and a.get("acheteur_siret") else None
            a["fiche_complete"] = s.get("plan") in subscribers.PLANS_PRO
            a["metier_libelle"] = metiers[m]["libelle"].lower() if m else ""
        html = render("email_digest.html", biz=biz, avis=sel, date_fr=date_fr(), criteres=criteres_txt(s, metiers),
                      lien_parrainage=parrainage.lien(biz, s), lien_dossier=lien_dossier(biz, s),
                      lien_compte=compte.lien(biz, s, metiers))
        subject = f"{len(sel)} appel{'s' if len(sel) > 1 else ''} d'offres : {sel[0]['objet'][:60]}"
        try:
            res = mailer.send(s["email"], subject, html, biz["email_expediteur"], reply_to=biz.get("email_proprietaire") or biz["email_contact"])
            if not res.get("simulated") or os.getenv("RADAR_DEMO") == "1":
                state["sent"][key] = (state["sent"].get(key, []) + [a["id"] for a in sel])[-500:]
            sent += 1
            if supa:   # historique privé « Mes alertes » de l'espace client
                try:
                    comptes.enregistrer_alertes(supa, s["email"], sel)
                except Exception as exc:
                    run["erreurs"].append(f"historique des alertes non enregistré : {safe(exc)}")
        except Exception as exc:
            run["erreurs"].append(f"envoi échoué : {safe(exc)}")
    run["emails_envoyes"] = sent

    # 5a. Espace client : rappel 3 jours avant la date limite des favoris, durées de conservation
    if supa:
        try:
            n, errs = comptes.rappels_favoris(supa, biz, subs)
            run["rappels_favoris"], run["erreurs"] = n, run["erreurs"] + errs
            comptes.menage(supa)
        except Exception as exc:
            run["erreurs"].append(f"rappels de favoris non envoyés : {safe(exc)}")

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
        res = site.build(avis_open, biz, metiers, matcher, OUT / "site", histo=None if os.getenv("RADAR_DEMO") else histo,
                         laureats=None if os.getenv("RADAR_DEMO") else charger_laureats(metiers, run, budget=1500, secondes=300),
                         stats={"total": len(avis_new), "metiers_hits": run["avis_metiers"]})
        run["pages_site"] = res["pages"]
        if date.today().weekday() == 0 and os.getenv("GITHUB_ACTIONS") == "true" and not os.getenv("RADAR_DEMO"):
            try:   # chaque lundi : signaler les pages à Bing et aux autres moteurs IndexNow
                code, n = site.indexnow(biz, OUT / "site")
                run["indexnow"] = f"{n} pages, réponse {code}"
            except Exception as exc:
                log.warning("IndexNow : %s", safe(exc))
    except Exception as exc:
        run["erreurs"].append(f"site non généré : {safe(exc)}")

    # 6b. Alerte gratuite limitée : inscriptions / désinscriptions reçues, puis 3 marchés par semaine (dans la limite Resend du jour)
    if avis_open is not None:
        try:
            quota = RESEND_JOUR - MARGE_JOUR - sent - run.get("bienvenues", 0) - run.get("rappels_essai", 0)
            g, errs = gratuit.traiter(biz, metiers, matcher, state, subs, avis_open, quota)
            run["alerte_gratuite"] = g
            run["emails_gratuits"] = g["envoyes"]
            run["erreurs"] += errs
        except Exception as exc:
            run["erreurs"].append(f"alerte gratuite non traitée : {safe(exc)}")

    # 7. Contrôles
    weekday = date.today().weekday() < 5
    if weekday and run["avis_recuperes"] == 0 and not collecte_ko:
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
    now_ts = int(datetime.now(timezone.utc).timestamp())
    # Passés payants : fin d'essai cette semaine et abonnement actif (ou payé directement sans essai).
    ventes = [s for s in subs_raw if s["status"] == "active" and week_ago <= (s.get("trial_end") or s.get("created") or 0) <= now_ts]
    runs = [_load_json(DATA / "runs" / f"{(today - timedelta(days=i)).isoformat()}.json", None) for i in range(7)]
    runs = [r for r in runs if r]
    kpi = {
        "mrr": finance.mrr(subs_raw), "abonnes_payants": len(actifs), "en_essai": len(essais), "impayes": len(impayes),
        "nouveaux_7j": len(nouveaux), "resilies_7j": len(resilies),
        "runs_7j": len(runs), "runs_avec_erreur": sum(1 for r in runs if r.get("erreurs")),
        "emails_7j": sum(r.get("emails_envoyes", 0) for r in runs),
        "avis_moyens_jour": round(sum(r.get("avis_recuperes", 0) for r in runs) / max(1, len(runs))),
        "objectif_mensuel_ca": [800, 1300],
        "essais_commences_7j": sum(1 for s in nouveaux if s.get("trial_end")),
        "ventes_7j": len(ventes),
        "dossiers_livres_7j": sum(1 for x in _load_json(DATA / "dossiers.json", {}).get("dossiers", {}).values()
                                  if (x.get("livre") or "") >= (today - timedelta(days=7)).isoformat()),
    }
    kpi["visites_7j"] = visites(biz, today - timedelta(days=7), today)
    ia_mois = _load_json(DATA / "state.json", {}).get("ia_usage", {}).get(today.strftime("%Y-%m"), {})
    kpi["ia_cout_mois_usd"] = ia_mois.get("cout_usd", 0)
    kpi["ia_avis_7j"] = sum((r.get("ia") or {}).get("avis", 0) for r in runs)
    kpi["ia_hors_metier_7j"] = sum((r.get("ia") or {}).get("hors_metier", 0) for r in runs)
    kpi["collectes_reportees_7j"] = sum(1 for r in runs if r.get("collecte_anormale"))
    pj = _load_json(DATA / "prospection.json", {}).get("journal", [])
    debut = (today - timedelta(days=7)).isoformat()
    kpi["prospects_contactes_7j"] = sum(j.get("envoyes", 0) for j in pj if j.get("date", "") >= debut)
    kpi["prospects_contactes_total"] = sum(j.get("envoyes", 0) for j in pj)
    kpi["prospects_relances_7j"] = sum(j.get("relances", 0) for j in pj if j.get("date", "") >= debut)
    kpi["alerte_gratuite_inscrits"] = len(gratuit.charger()["abonnes"])
    kpi["prospects_clics_7j"] = _clics([i for j in pj if j.get("date", "") >= debut for i in j.get("ids", [])])
    decisions = []
    if kpi["visites_7j"] is None and biz.get("statistiques_goatcounter"):
        decisions.append("Visites illisibles : sur goatcounter.com, Settings, cocher « Allow adding visitor counts on your website » puis Save.")
    if impayes:
        decisions.append(f"{len(impayes)} abonnement(s) impayé(s) : Stripe relance automatiquement, rien à faire sauf si cela dure.")
    if kpi["runs_7j"] < 5:
        decisions.append("Moins de 5 envois quotidiens cette semaine : vérifier l'onglet Actions de GitHub.")
    if kpi["collectes_reportees_7j"]:
        decisions.append(f"Alertes reportées {kpi['collectes_reportees_7j']} jour(s) (BOAMP en panne ou incomplet) : rattrapées le lendemain, rien à faire sauf si cela dure.")
    if kpi["resilies_7j"] > kpi["nouveaux_7j"] and kpi["resilies_7j"]:
        decisions.append("Plus de résiliations que de nouveaux clients : lire les réponses clients pour comprendre pourquoi.")
    analyse = biz["ia_active"] and ai.ask(
        "Tu es le directeur des opérations d'un petit service d'alertes d'appels d'offres par abonnement "
        f"({biz['offres']['essentiel']['prix_mensuel']}€ et {biz['offres']['pro']['prix_mensuel']}€/mois). "
        "Voici les indicateurs de la semaine (JSON). En 5 puces maximum, en français simple : "
        "ce qui va bien, ce qui inquiète, et UNE action concrète prioritaire qui ne demande pas plus de 15 minutes au propriétaire. "
        "Ne promets aucun revenu.\n" + json.dumps({"kpi": kpi, "finance_mois": bilan}, ensure_ascii=False),
        model=biz["ia"]["modele"])
    rows = "".join(f"<tr><td>{k}</td><td style='text-align:right'><b>{v}</b></td></tr>" for k, v in {**kpi, **bilan}.items())
    def ligne(lib, v, aide=""):
        v = "non disponible" if v is None else v
        return f"<tr><td>{lib}</td><td style='text-align:right;font-size:18px'><b>{v}</b></td><td style='color:#667'>{aide}</td></tr>"
    resume = ("<table cellpadding='6' style='border-collapse:collapse'>"
              + ligne("Visites du site", kpi["visites_7j"], "personnes venues sur radar-marches.fr")
              + ligne("Entreprises prospectées", kpi["prospects_contactes_7j"], "1 seul e-mail par entreprise")
              + ligne("Clics dans ces e-mails", kpi["prospects_clics_7j"], "")
              + ligne("Relances (prospects qui avaient cliqué)", kpi["prospects_relances_7j"], "1 seule relance, 5 jours après")
              + (ligne("Inscrits à l'alerte gratuite", kpi["alerte_gratuite_inscrits"], "3 marchés par semaine") if biz.get("alerte_gratuite") else "")
              + ligne("Essais gratuits commencés", kpi["essais_commences_7j"], "")
              + ligne("Ventes (passés payants)", kpi["ventes_7j"], "")
              + ligne("Résiliations", kpi["resilies_7j"], "")
              + (ligne("Dossiers prêts à signer livrés", kpi["dossiers_livres_7j"], f"{biz['dossier']['prix']} € l'unité") if biz.get("dossier_actif") else "")
              + ligne("Clients payants", kpi["abonnes_payants"], f"revenu mensuel récurrent {kpi['mrr']} €")
              + (ligne("Avis résumés par l'IA", kpi["ia_avis_7j"], f"dont {kpi['ia_hors_metier_7j']} jugés hors métier (observation, rien n'est filtré) · "
                       f"coût du mois {kpi['ia_cout_mois_usd']} $") if kpi["ia_avis_7j"] or kpi["ia_cout_mois_usd"] else "")
              + "</table>")
    html = (f"<h2>{biz['nom']} · bilan de la semaine ({date_fr(today)})</h2>" + resume
            + f"<p>Mois en cours : CA {bilan['chiffre_affaires']} € · bénéfice {bilan['benefice_apres_depenses']} € · "
            f"net estimé après cotisations et impôt {bilan['revenu_net']} €.</p>"
            + ("<h3>Décisions / points d'attention</h3><ul>" + "".join(f"<li>{d}</li>" for d in decisions) + "</ul>" if decisions else "<p>Aucune décision requise cette semaine.</p>")
            + (f"<h3>Analyse de l'agent</h3><div style='white-space:pre-wrap'>{analyse}</div>" if analyse else "")
            + f"<h3>Détail</h3><table cellpadding='4'>{rows}</table>")
    _save_json(DATA / "rapports" / f"{today.isoformat()}.json", {"kpi": kpi, "finance": bilan, "decisions": decisions})
    alert_owner(biz, f"Bilan du dimanche · {kpi['visites_7j'] if kpi['visites_7j'] is not None else '?'} visites, "
                     f"{kpi['essais_commences_7j']} essai(s), {kpi['ventes_7j']} vente(s)", html)
    return {"kpi": kpi, "finance": bilan, "decisions": decisions, "analyse": analyse}


def visites(biz, debut, fin):
    """Visiteurs uniques GoatCounter sur la période (compteur public du compte), None si illisible."""
    compte = (biz.get("statistiques_goatcounter") or "").strip()
    if not compte:
        return None
    try:
        r = requests.get(f"https://{compte}.goatcounter.com/counter/TOTAL.json",
                         params={"start": debut.isoformat(), "end": fin.isoformat()}, timeout=30)
        r.raise_for_status()
        return int("".join(c for c in str(r.json().get("count", "")) if c.isdigit()) or 0)
    except Exception as exc:
        log.warning("Visites GoatCounter non lues : %s", safe(exc))
        return None


def run_abonnes():
    """Vérification : liste les abonnés lus dans Stripe (e-mails masqués, pour des journaux publics)."""
    biz, metiers = business(), load_metiers()
    subs = subscribers.load_all(biz, metiers)

    def mask(e):
        u, _, d = e.partition("@")
        return f"{u[:2]}***@{d}"
    return {"mode_stripe": biz.get("stripe_mode"), "nombre": len(subs),
            "controle_essais": anti_abus.appliquer(biz, simuler=True),   # lecture seule : rien n'est modifié
            "abonnes": [{"email": mask(s["email"]), "plan": s["plan"], "statut": s["statut"], "source": s.get("source"), "metiers": s["metiers"],
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

    def zone_de(metier, dep, source="prospection"):
        lst = par_metier.get(metier, [])
        local = [a for a in lst if dep and dep in a["departements"]]
        base = biz["site_url"].rstrip("/") + f"/appels-offres/{metier}/"
        if len(local) >= 2:
            n, zone, ex, lien = len(local), f"dans le département {dep}", local[0], base + f"{dep}.html"
        else:
            n, zone, ex, lien = len(lst), "en France", (lst[0] if lst else None), base
        return n, zone, ex, lien + f"?utm_source={source}&utm_medium=email&utm_campaign={metier}"

    def compose(p):
        n, zone, ex, lien = zone_de(p["metier"], p["dep"])
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
    envoyes, ids, suivi = 0, [], {}
    quota = quota_prospection(int(conf.get("par_jour") or 20), state["journal"])
    resume["quota_du_jour"] = quota
    # Relance unique, 5 jours après, seulement pour ceux qui ont cliqué sans s'inscrire (avant les nouveaux prospects).
    nb_relances = relancer(state["journal"], biz, metiers, conf, zone_de, clients, quota)
    resume["relances"] = nb_relances
    quota -= nb_relances
    for c in cands[:max(0, quota)]:
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
            suivi[res["id"]] = [c["metier"], c["dep"]]
    state["journal"].append({"date": date.today().isoformat(), "envoyes": envoyes, "ids": ids, "suivi": suivi,
                             "relances": nb_relances})
    state["journal"] = state["journal"][-120:]
    _save_json(state_path, state)
    resume["envoyes"] = envoyes
    return resume


# Offre gratuite Resend : 100 e-mails/jour et 3 000/mois, tous envois confondus.
# Les alertes clients passent toujours en premier : la prospection prend ce qui reste, moins une marge.
RESEND_JOUR, RESEND_MOIS, MARGE_JOUR, MARGE_MOIS = 100, 3000, 20, 300


def _envois(run):
    return sum(int(run.get(k) or 0) for k in ("emails_envoyes", "emails_gratuits", "bienvenues", "rappels_essai"))


def quota_prospection(par_jour, journal, today=None):
    today = today or date.today()
    run = _load_json(DATA / "runs" / f"{today.isoformat()}.json", {}) or {}
    reste_jour = RESEND_JOUR - MARGE_JOUR - _envois(run)
    mois = today.strftime("%Y-%m")
    clients_mois = sum(_envois(_load_json(f, {}) or {}) for f in (DATA / "runs").glob(f"{mois}-*.json"))
    prospects_mois = sum(j.get("envoyes", 0) + j.get("relances", 0) for j in journal if str(j.get("date", "")).startswith(mois))
    # Envois clients encore à venir ce mois-ci (estimés sur la moyenne des jours déjà passés).
    jours = max(1, len(list((DATA / "runs").glob(f"{mois}-*.json"))))
    a_venir = clients_mois / jours * max(0, 22 - jours)
    reste_mois = RESEND_MOIS - MARGE_MOIS - clients_mois - prospects_mois - a_venir
    return max(0, min(par_jour, reste_jour, int(reste_mois)))


RELANCE_APRES, RELANCE_JUSQUA = 5, 12   # jours après le premier e-mail


def relancer(journal, biz, metiers, conf, zone_de, clients, quota, today=None):
    """Une seule relance par prospect ayant cliqué sans s'inscrire (ni abonné, ni inscrit à l'alerte gratuite).
    Seuls les e-mails dont le pied de page annonçait ce rappel (champ « suivi » du journal) sont concernés."""
    today = today or date.today()
    clients = {gratuit.h(e) for e in clients} | gratuit.emails_connus()
    reply = biz.get("email_proprietaire") or biz["email_contact"]
    n = 0
    for j in journal:
        if not j.get("suivi") or j.get("relance_faite"):
            continue
        age = (today - date.fromisoformat(j["date"])).days
        if age < RELANCE_APRES:
            continue
        verifies = set(j.get("verifies") or [])
        for i, (metier, dep) in j["suivi"].items():
            if i in verifies or age > RELANCE_JUSQUA:
                continue
            if n >= quota:
                break
            try:
                e = mailer.lire_email(i)
                time.sleep(0.6)  # limite Resend : 2 requêtes/seconde
            except Exception as exc:
                log.warning("Relance : e-mail non lu (%s)", safe(exc))
                break
            verifies.add(i)
            to = ((e.get("to") or [""])[0] or "").lower()
            if e.get("last_event") != "clicked" or not to or gratuit.h(to) in clients or metier not in metiers:
                continue
            nb, zone, ex, lien = zone_de(metier, dep, "relance")
            if nb < 2:
                continue
            m = metiers[metier]
            site = biz["site_url"].rstrip("/")
            html = render("email_relance.html", biz=biz, metier=m, n=nb, zone=zone, ex=ex, lien=lien,
                          lien_essai=f"{site}/?utm_source=relance&utm_medium=email&utm_campaign={metier}#tarifs",
                          lien_gratuit=f"{site}/alerte-gratuite/?utm_source=relance" if biz.get("alerte_gratuite") else None)
            try:
                mailer.send(to, f"{nb} marchés « {m['libelle'].lower()} » ouverts {zone.replace('dans le département', 'dans le')}",
                            html, conf["expediteur"], reply_to=reply, headers={"List-Unsubscribe": f"<mailto:{reply}?subject=STOP>"})
                n += 1
            except Exception as exc:
                log.error("Relance refusée : %s", safe(exc))
        j["verifies"] = sorted(verifies)
        if age > RELANCE_JUSQUA or verifies >= set(j["suivi"]):
            j["relance_faite"] = True
            j.pop("verifies", None)
    return n


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
        {"email": "demo-pro@example.com", "plan": "pro", "metiers": ["espaces-verts"], "departements": [], "mots_cles": ["aire de jeux"],
         "fin_essai": int(datetime.now(timezone.utc).timestamp()) + 2 * 86400},
    ]
    global DATA
    DATA = OUT / "demo-data"
    return run_daily(avis_new=avis, avis_open=[dict(a) for a in avis], subs=subs)


def charger_laureats(metiers, run, budget=0, secondes=None):
    """Entreprises lauréates (pages « qui a gagné quoi ») ; None si les données publiques sont indisponibles."""
    try:
        data = laureats.charger(metiers, budget=budget, secondes=secondes)
        run["entreprises_lauréates"] = len(data["fiches"])
        return data
    except Exception as exc:
        run.setdefault("erreurs", []).append(f"pages entreprises non générées : {safe(exc)}")
        return None


def run_entreprises(budget):
    """Complète les noms des entreprises lauréates (annuaire officiel), sans e-mail ni site."""
    metiers = load_metiers()
    data = laureats.charger(metiers, budget=budget, secondes=35 * 60)
    return {"marchés": data["nb_marches"], "entreprises_avec_page": len(data["fiches"]), "repères": data["reperes"]}


def run_barometre(mois=None):
    """Calcule et enregistre le baromètre du mois écoulé (data/barometre/AAAA-MM.json)."""
    metiers = load_metiers()
    matcher = Matcher(metiers)
    mois = mois or barometre.mois_precedent()
    try:
        reperes = laureats.charger(metiers)["reperes"]
    except Exception as exc:
        log.warning("Repères indisponibles : %s", safe(exc))
        reperes = {}
    b = barometre.calculer(mois, metiers, matcher, reperes=reperes)
    barometre.enregistrer(b)
    return {"mois": mois, "appels": b["appels"], "mois_precedent": b["precedent"]["appels"]}


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
    res = site.build(avis_open, biz, metiers, matcher, OUT / "site", histo=historique.Historique(metiers),
                     laureats=charger_laureats(metiers, report),
                     stats={"total": len(avis), "metiers_hits": sum(v["nombre"] for v in report["par_metier"].values())})
    report["avis_ouverts_30_jours"] = len(avis_open)
    report["pages_site"] = res["pages"]
    return report


def run_apercu_emails():
    """Envoie au propriétaire un exemplaire des principaux e-mails (données fictives) pour vérifier leur présentation."""
    biz = business()
    to = biz["email_proprietaire"].strip()
    if not to:
        return {"envoyes": 0, "raison": "pas d'e-mail propriétaire"}
    a = {"objet": "Exemple : nettoyage des locaux de la mairie", "acheteur": "Commune fictive", "departements": ["33"],
         "url": biz["site_url"], "date_limite": "", "date_limite_fr": "12 novembre 2026"}
    crit = "Nettoyage · Gironde (33)"
    mails = {
        "Bienvenue": render("email_bienvenue.html", biz=biz, criteres=crit, sub={}, lien=biz["site_url"] + "/?ami=EXEMPLE"),
        "Alerte du matin": render("email_digest.html", biz=biz, avis=[a], date_fr=date_fr(), criteres=crit),
        "Prospection": render("email_prospect.html", p={"marche_gagne": "Exemple de marché"}, n=7, zone="en Gironde",
                              ex=a, lien=biz["site_url"], metier={"libelle": "Nettoyage"}, biz=biz),
    }
    for nom, html in mails.items():
        mailer.send(to, f"[Aperçu {biz['nom']}] {nom} (données fictives)", html, biz["email_expediteur"])
    return {"envoyes": len(mails)}


def run_dossiers():
    """Option « Dossier prêt à signer » : traite paiements et pièces reçues (workflow dossier.yml, toutes les heures)."""
    biz = business()
    if not biz["dossier_actif"]:
        return {"actif": False}
    path = DATA / "dossiers.json"
    state = _load_json(path, {})
    metiers = load_metiers()
    cles = [s for s in subscribers.load_all(biz, metiers) if s.get("plan") == "cle"] if biz["cle_actif"] else []
    livres, erreurs = dossier.traiter(biz, state, alerte=lambda sujet, html: alert_owner(biz, sujet, html), abonnes=cles)
    histo = historique.Historique(metiers)
    rapports, errs = rapport.traiter(biz, state, cles, metiers, histo=histo) if cles else (0, [])
    erreurs += errs
    _save_json(path, state)
    espace, errs = demandes_espace(biz, metiers, cles, histo)
    erreurs += errs
    if erreurs:
        log.warning("Dossiers : %s", [safe(e) for e in erreurs])
    return {"actif": True, "livres": livres, "rapports": rapports, "demandes_espace": espace, "erreurs": len(erreurs)}


def demandes_espace(biz, metiers, cles, histo, supa=None, generer_rapport=None, generer_dossier=None, avis_fn=None):
    """Rapports et dossiers inclus demandés depuis l'espace client (offre Clé en main) : résultat déposé dans le
    stockage privé du client + e-mail. Les demandes par e-mail (rapport.traiter / dossier.traiter) restent possibles."""
    if not biz.get("cle_actif"):
        return 0, []
    supa = supa or comptes.connexion()
    if not supa:
        return 0, []
    d = biz.get("dossier") or {}
    modele = d.get("modele") or "claude-opus-5-5"
    generer_rapport = generer_rapport or (lambda avis, fiche, metier: rapport.generer(avis, fiche, metier, "", modele))
    generer_dossier = generer_dossier or (lambda dce, infos: dossier.generer(dce, infos, modele))
    avis_fn = avis_fn or boamp.fetch_one
    par_email = {s["email"].lower(): s for s in cles}

    def metier_de(c, avis):
        ms = (par_email.get((c.get("email") or "").lower()) or {}).get("metiers") or []
        return next((m for m in ms if m in (avis.get("metiers") or [])), None) or next(iter(ms), None)

    def faire_rapport(dem, c):
        avis = avis_fn(dem["avis_ref"])
        if not avis:
            return None, f"Avis {dem['avis_ref']} introuvable au BOAMP : vérifiez la référence. Non décompté."
        m = metier_de(c, avis)
        fiche = histo.fiche(avis.get("acheteur_siret"), m) if histo and m and avis.get("acheteur_siret") else None
        r = generer_rapport(avis, fiche, (metiers.get(m) or {}).get("libelle", ""))
        kw = dict(r=r, avis=avis, fiche=fiche, avec_dce=False,
                  lien_dossier=f"{biz['site_url'].rstrip('/')}/compte/#demandes")
        chemin = f"{dem['user_id']}/{dem['id']}/resultat/{comptes.nom_fichier('rapport ' + avis['objet'], '.html')}"
        supa.deposer(chemin, render("email_rapport.html", biz=biz, cas="rapport", **kw).encode(), "text/html")
        rapport._envoyer(biz, c["email"], f"comment gagner « {avis['objet'][:60]} »", "rapport", **kw)
        return chemin, None

    def faire_dossier(dem, c, pieces):
        textes = []
        for p in pieces:
            if p.lower().endswith(comptes.DCE_TYPES):
                textes += dossier.extraire_texte(p.rsplit("/", 1)[-1], supa.lire(p))
        dce = dossier.assembler(textes)
        if len(dce) < 2000:
            return None, "Pièces illisibles ou incomplètes : ajoutez le règlement et le cahier des charges (PDF ou Word). Non décompté."
        doc = generer_dossier(dce, (dem.get("infos") or "") + f"\nAvis BOAMP : {dem['avis_ref']}")
        reste = dossier.jours_avant(doc.get("date_limite"))
        if reste is not None and reste < int(d.get("delai_min_jours") or 5):
            return None, f"Date limite trop proche ({max(reste, 0)} jour(s)) pour préparer un dossier sérieux. Non décompté."
        fichier = dossier.construire_docx(doc, biz["nom"])
        nom = comptes.nom_fichier("dossier " + (doc.get("objet") or "reponse"), ".docx")
        chemin = f"{dem['user_id']}/{dem['id']}/resultat/{nom}"
        supa.deposer(chemin, fichier, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
        import base64
        dossier._envoyer(biz, c["email"], "votre dossier est prêt", "livre", objet=doc.get("objet"), date_limite=doc.get("date_limite"),
                         inclus=True, pj=[{"filename": nom, "content": base64.b64encode(fichier).decode()}])
        return chemin, None

    return comptes.traiter_demandes(supa, biz, metiers, faire_rapport, faire_dossier)


def run_comptes(installer=False):
    """Comptes clients : mise en service (installer=True, workflow comptes-setup.yml) puis synchronisation Stripe."""
    biz, metiers = business(), load_metiers()
    out = {}
    if installer:
        from . import comptes_setup
        out = comptes_setup.installer(biz)
        os.environ.setdefault("SUPABASE_PROJECT_REF", out["projet"])
    run = {"erreurs": []}
    subs = subscribers.load_all(biz, metiers)
    supa = comptes_espace(biz, metiers, subs, run)
    out.update(actif=bool(supa), comptes=run.get("comptes"), criteres_espace=run.get("criteres_espace", 0), erreurs=run["erreurs"])
    return out


def main(argv=None):
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    p = argparse.ArgumentParser(prog="radar")
    p.add_argument("cmd", choices=["daily", "weekly", "check-api", "demo", "essai", "abonnes", "prospection", "audit", "apercu-emails", "dossiers", "entreprises", "barometre", "bienvenue", "veille", "comptes", "comptes-setup"])
    p.add_argument("--mois", help="baromètre : mois AAAA-MM (par défaut le mois écoulé)")
    p.add_argument("--budget", type=int, default=6000, help="entreprises : nombre maximum d'appels à l'annuaire")
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
        elif args.cmd == "veille":
            print(json.dumps(run_veille(), ensure_ascii=False))
        elif args.cmd in ("comptes", "comptes-setup"):
            print(json.dumps(run_comptes(installer=args.cmd == "comptes-setup"), ensure_ascii=False, indent=1))
        elif args.cmd == "bienvenue":
            print(json.dumps(run_bienvenue(), ensure_ascii=False))
        elif args.cmd == "abonnes":
            print(json.dumps(run_abonnes(), ensure_ascii=False, indent=1))
        elif args.cmd == "essai":
            print(json.dumps(run_essai(), ensure_ascii=False, indent=1))
        elif args.cmd == "prospection":
            print(json.dumps(run_prospection(apercu=args.apercu), ensure_ascii=False, indent=1))
        elif args.cmd == "audit":
            print(json.dumps(run_audit(), ensure_ascii=False, indent=1))
        elif args.cmd == "apercu-emails":
            print(json.dumps(run_apercu_emails(), ensure_ascii=False))
        elif args.cmd == "dossiers":
            print(json.dumps(run_dossiers(), ensure_ascii=False))
        elif args.cmd == "entreprises":
            print(json.dumps(run_entreprises(args.budget), ensure_ascii=False, indent=1))
        elif args.cmd == "barometre":
            print(json.dumps(run_barometre(args.mois), ensure_ascii=False, indent=1))
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
