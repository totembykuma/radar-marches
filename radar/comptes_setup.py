"""Mise en service des comptes clients, en un seul passage (workflow comptes-setup.yml, lancé à la main).

Seul prérequis : le secret GitHub SUPABASE_ACCESS_TOKEN (jeton du compte Supabase du propriétaire).
Ce script, rejouable sans risque :
1. trouve le projet Supabase « radar-marches » (le réveille s'il est en pause) et lit ses clés ;
2. crée ou met à jour les tables, les règles d'accès (RLS) et les fonctions (supabase/schema.sql) ;
3. crée le stockage privé « documents » (20 Mo par fichier, PDF / Word / ZIP) ;
4. règle l'authentification : vérification de l'e-mail obligatoire, mot de passe de 10 caractères minimum, liens
   valables 1 h, envoi des e-mails par Resend (SMTP) depuis notre adresse, textes en français, adresses de retour du site ;
5. donne au webhook Stripe ses secrets et crée le point d'arrivée chez Stripe (si la clé Stripe le permet) ;
6. écrit config/comptes.json (adresse du projet + clé publique, faites pour être publiques) : le site affiche alors
   la connexion par mot de passe au prochain passage de site.yml.
Le déploiement du code du webhook se fait juste après, dans le workflow (outil officiel supabase).
Aucune clé n'est imprimée dans les journaux.
"""
import json
import os
import time

import requests

from .comptes import BUCKET, CONFIG, DCE_MAX_OCTETS, Gestion, Supa, _masquer
from .config import ROOT
from .render import render

TYPES_MIME = ["application/pdf", "application/zip", "application/x-zip-compressed", "application/msword",
              "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
              "application/vnd.oasis.opendocument.text", "text/plain",
              "text/html"]   # text/html : rapports « comment gagner » déposés par le robot
EVENEMENTS_STRIPE = ["checkout.session.completed", "customer.subscription.created", "customer.subscription.updated",
                     "customer.subscription.deleted", "customer.subscription.trial_will_end", "invoice.paid",
                     "invoice.payment_failed", "charge.refunded"]
FONCTION = "stripe-webhook"


def config_auth(biz, smtp_pass):
    """Réglages Supabase Auth (API de gestion, PATCH /config/auth)."""
    site = biz["site_url"].rstrip("/")
    expediteur = biz["email_expediteur"].split("<")[-1].strip(" >")
    def modele(cas):
        return render("email_auth.html", biz=biz, cas=cas)
    return {
        "site_url": site,
        "uri_allow_list": f"{site}/compte/**",
        "disable_signup": False,
        "external_email_enabled": True,
        "mailer_autoconfirm": False,                       # adresse vérifiée obligatoire
        "mailer_secure_email_change_enabled": True,        # changement d'adresse confirmé sur l'ancienne ET la nouvelle
        "mailer_otp_exp": 3600,                            # liens valables 1 h, à usage unique
        "password_min_length": 10,
        "refresh_token_rotation_enabled": True,
        "security_refresh_token_reuse_interval": 10,
        "smtp_host": "smtp.resend.com", "smtp_port": "465", "smtp_user": "resend", "smtp_pass": smtp_pass,
        "smtp_admin_email": expediteur, "smtp_sender_name": biz["nom"], "smtp_max_frequency": 60,
        "rate_limit_email_sent": 30,                       # e-mails d'authentification par heure (inscriptions, oublis)
        "mailer_subjects_confirmation": f"{biz['nom']} : confirmez votre adresse e-mail",
        "mailer_templates_confirmation_content": modele("inscription"),
        "mailer_subjects_recovery": f"{biz['nom']} : choisir un nouveau mot de passe",
        "mailer_templates_recovery_content": modele("oubli"),
        "mailer_subjects_email_change": f"{biz['nom']} : confirmez votre nouvelle adresse e-mail",
        "mailer_templates_email_change_content": modele("changement"),
        "mailer_subjects_magic_link": f"{biz['nom']} : votre lien de connexion",
        "mailer_templates_magic_link_content": modele("lien"),
    }


def _bucket(supa):
    corps = {"id": BUCKET, "name": BUCKET, "public": False, "file_size_limit": DCE_MAX_OCTETS, "allowed_mime_types": TYPES_MIME}
    r = supa._req("POST", "/storage/v1/bucket", json_=corps, attendu=409)
    if r.status_code >= 400:                                # existe déjà (409, ou 400 selon la version) : mise à jour
        supa._req("PUT", f"/storage/v1/bucket/{BUCKET}", json_={k: v for k, v in corps.items() if k != "name"})


def _webhook_stripe(g, ref, url_fonction, session=None):
    """Point d'arrivée Stripe → fonction ; renvoie un message d'état (jamais d'exception : la synchro suffit sans lui)."""
    key = (os.getenv("STRIPE_SECRET_KEY") or "").strip()
    if not key:
        return "webhook Stripe : clé Stripe absente, ignoré"
    http = session or requests
    secrets = {s.get("name") for s in g.req("GET", f"/projects/{ref}/secrets")}
    try:
        r = http.get("https://api.stripe.com/v1/webhook_endpoints", params={"limit": 100}, auth=(key, ""), timeout=60)
        r.raise_for_status()
        existant = next((w for w in r.json()["data"] if w.get("url") == url_fonction), None)
        if existant and "STRIPE_WEBHOOK_SECRET" in secrets:
            return "webhook Stripe : déjà en place"
        if existant:                                        # secret perdu : on recrée pour en obtenir un nouveau
            http.delete(f"https://api.stripe.com/v1/webhook_endpoints/{existant['id']}", auth=(key, ""), timeout=60)
        data = [("url", url_fonction), ("description", "Radar Marchés : droits des comptes clients")] + \
               [("enabled_events[]", e) for e in EVENEMENTS_STRIPE]
        r = http.post("https://api.stripe.com/v1/webhook_endpoints", data=data, auth=(key, ""), timeout=60)
        r.raise_for_status()
        secret = r.json()["secret"]
        _masquer(secret)
        g.req("POST", f"/projects/{ref}/secrets", json=[{"name": "STRIPE_WEBHOOK_SECRET", "value": secret}])
        return "webhook Stripe : créé"
    except Exception as exc:
        code = getattr(getattr(exc, "response", None), "status_code", "")
        return f"webhook Stripe non créé ({code or str(exc)[:80]}) : les droits se mettent à jour toutes les 10 min"


def installer(biz, session=None, attendre=time.sleep):
    jeton = (os.getenv("SUPABASE_ACCESS_TOKEN") or "").strip()
    if not jeton:
        raise SystemExit("Secret SUPABASE_ACCESS_TOKEN absent : voir docs/architecture-comptes-clients.md, étape 1.")
    g = Gestion(jeton, session)
    p = g.projet()
    ref, etapes = p["id"], []
    if p.get("status") not in (None, "ACTIVE_HEALTHY"):
        if p.get("status") == "INACTIVE":
            g.req("POST", f"/projects/{ref}/restore")
            etapes.append("projet réveillé (il était en pause)")
        for _ in range(40):                                 # jusqu'à ~10 min
            if g.req("GET", f"/projects/{ref}").get("status") == "ACTIVE_HEALTHY":
                break
            attendre(15)
    publique, secrete = g.cles(ref)
    if not (publique and secrete):
        raise RuntimeError("clés du projet introuvables")
    _masquer(secrete)
    url = f"https://{ref}.supabase.co"
    supa = Supa(url, secrete, session)

    g.req("POST", f"/projects/{ref}/database/query", json={"query": (ROOT / "supabase" / "schema.sql").read_text(encoding="utf-8")})
    etapes.append("base : tables, règles d'accès et fonctions à jour")
    _bucket(supa)
    etapes.append("stockage privé « documents » prêt")
    resend = (os.getenv("RESEND_API_KEY") or "").strip()
    if not resend:
        raise RuntimeError("secret RESEND_API_KEY absent : les e-mails de confirmation ne pourraient pas partir")
    g.req("PATCH", f"/projects/{ref}/config/auth", json=config_auth(biz, resend))
    etapes.append("authentification : e-mail vérifié, mot de passe ≥ 10 caractères, e-mails en français via Resend")
    sec = [{"name": "RADAR_SERVICE_KEY", "value": secrete}]
    if os.getenv("STRIPE_SECRET_KEY"):
        sec.append({"name": "STRIPE_SECRET_KEY", "value": os.environ["STRIPE_SECRET_KEY"].strip()})
    g.req("POST", f"/projects/{ref}/secrets", json=sec)
    etapes.append(_webhook_stripe(g, ref, f"{url}/functions/v1/{FONCTION}", session))

    CONFIG.write_text(json.dumps({"url": url, "cle": publique}, indent=1) + "\n", encoding="utf-8")
    etapes.append("config/comptes.json écrit : la connexion par mot de passe apparaîtra sur le site")
    if os.getenv("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as f:
            f.write(f"ref={ref}\n")
    return {"projet": ref, "etapes": etapes}
