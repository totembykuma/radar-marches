"""Contrôle en lecture seule du site en ligne et des liens de paiement Stripe.

Rien n'est payé, créé ni modifié : pages du site (GET), pages de paiement Stripe
(GET, sans saisir de carte) et lecture des liens de paiement par l'API Stripe.
Imprime un résumé court : OK / PROBLÈME par point vérifié.
"""
import os
import random
import re
import sys
import xml.etree.ElementTree as ET

import requests
import yaml

SITE = "https://www.radar-marches.fr"
PAGES = ["/", "/appels-offres/", "/entreprises/", "/barometre/", "/videos/", "/compte/", "/merci/",
         "/dossier/", "/dossier/envoyer/", "/criteres/", "/parrainage/", "/avis/", "/guides/", "/cgv.html",
         "/mentions-legales.html", "/confidentialite.html", "/sitemap.xml", "/robots.txt",
         "/static/style.css", "/static/compte.js", "/static/og-image.png", "/static/videos/creer-un-dossier.mp4",
         "/linkedin/flux.xml"]
problemes = []


def pb(msg):
    problemes.append(msg)
    print("PROBLÈME", msg)


def get(url, **kw):
    try:
        return requests.get(url, timeout=30, headers={"User-Agent": "RadarMarches-verif/1.0"}, **kw)
    except requests.RequestException as e:
        return e


def site():
    for p in PAGES:
        r = get(SITE + p)
        if not hasattr(r, "status_code") or r.status_code != 200:
            pb(f"page {p} : {getattr(r, 'status_code', r)}")
    for depart in ("http://www.radar-marches.fr/", "https://radar-marches.fr/", "http://radar-marches.fr/"):
        r = get(depart)
        fin = getattr(r, "url", "")
        if not hasattr(r, "status_code") or r.status_code != 200 or not fin.startswith(SITE):
            pb(f"adresse {depart} n'arrive pas sur {SITE} ({getattr(r, 'status_code', r)}, {fin})")
    r = get(SITE + "/sitemap.xml")
    urls = [e.text for e in ET.fromstring(r.content).iter() if e.tag.endswith("loc")] if getattr(r, "status_code", 0) == 200 else []
    random.seed(1)
    echantillon = random.sample(urls, min(60, len(urls)))
    ko = [u for u in echantillon if getattr(get(u), "status_code", 0) != 200]
    print(f"plan du site : {len(urls)} adresses, {len(echantillon)} testées, {len(ko)} en erreur")
    for u in ko[:10]:
        pb(f"adresse du plan du site en erreur : {u}")


def stripe():
    cle = os.environ.get("STRIPE_SECRET_KEY")
    biz = yaml.safe_load(open("config/business.yaml", encoding="utf-8"))
    texte = open("config/business.yaml", encoding="utf-8").read()
    liens = sorted(set(re.findall(r"https://buy\.stripe\.com/[A-Za-z0-9]+", texte)))
    for u in liens:
        r = get(u)
        if getattr(r, "status_code", 0) != 200:
            pb(f"page de paiement {u} : {getattr(r, 'status_code', r)}")
    print(f"pages de paiement Stripe ouvertes : {len(liens)}")
    if not cle:
        pb("clé Stripe absente : liens non lus dans Stripe")
        return
    s = requests.Session()
    s.auth = (cle, "")
    tous, apres = {}, None
    while True:
        d = s.get("https://api.stripe.com/v1/payment_links", params={"limit": 100, **({"starting_after": apres} if apres else {})}, timeout=30).json()
        for l in d.get("data", []):
            tous[l["url"]] = l
        if not d.get("has_more"):
            break
        apres = d["data"][-1]["id"]
    for u in liens:
        l = tous.get(u)
        if not l:
            pb(f"lien {u} inconnu dans Stripe (mauvais compte ou mode ?)")
            continue
        if not l.get("active"):
            pb(f"lien {u} désactivé dans Stripe")
        items = s.get(f"https://api.stripe.com/v1/payment_links/{l['id']}/line_items", timeout=30).json().get("data", [])
        prix = items[0]["price"] if items else {}
        rec = (prix.get("recurring") or {}).get("interval", "unique")
        essai = (l.get("subscription_data") or {}).get("trial_period_days")
        retour = ((l.get("after_completion") or {}).get("redirect") or {}).get("url", "")
        print(f"lien {u[-6:]} : {prix.get('unit_amount', 0) / 100:.2f} € / {rec}, essai {essai}, "
              f"actif {l.get('active')}, retour {retour.replace(SITE, '') or 'page Stripe'}, livemode {l.get('livemode')}")
        if not l.get("livemode") and biz.get("stripe_mode") == "réel":
            pb(f"lien {u} en mode test alors que le site est en mode réel")
        if retour and not retour.startswith(SITE):
            pb(f"lien {u} renvoie vers {retour}")


if __name__ == "__main__":
    site()
    stripe()
    print(f"RÉSUMÉ : {len(problemes)} problème(s)")
    sys.exit(0)
