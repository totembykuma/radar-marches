"""Crée (une seule fois) dans Stripe les 2 offres, leurs liens de paiement avec essai et champs personnalisés,
et le portail client. Fonctionne en mode test (sk_test_…) comme en réel (sk_live_… / rk_live_…).
Les liens et identifiants de prix sont écrits dans config/business.yaml."""
import os
import re

import requests

from .config import ROOT, business, metiers as load_metiers

API = "https://api.stripe.com/v1"


def _call(method, path, data=None, params=None):
    key = os.environ["STRIPE_SECRET_KEY"].strip()
    r = requests.request(method, f"{API}/{path}", data=data, params=params, auth=(key, ""), timeout=60)
    if r.status_code >= 400:
        raise RuntimeError(f"Stripe {path} : {r.status_code} {r.text[:500]}")
    return r.json()


def _find_product(offre_key):
    for p in _call("GET", "products", params={"limit": 100, "active": "true"})["data"]:
        if (p.get("metadata") or {}).get("radar_offre") == offre_key:
            return p
    return None


def _price_for(product, amount_cents):
    for pr in _call("GET", "prices", params={"product": product["id"], "active": "true", "limit": 100})["data"]:
        if pr["unit_amount"] == amount_cents and (pr.get("recurring") or {}).get("interval") == "month":
            return pr
    return _call("POST", "prices", data={"product": product["id"], "currency": "eur", "unit_amount": amount_cents,
                                         "recurring[interval]": "month", "tax_behavior": "inclusive"})


def _link_fields(offre_key, metiers):
    d = {
        "custom_fields[0][key]": "metier", "custom_fields[0][type]": "dropdown",
        "custom_fields[0][label][type]": "custom", "custom_fields[0][label][custom]": "Votre métier",
        "custom_fields[1][key]": "departements", "custom_fields[1][type]": "text",
        "custom_fields[1][label][type]": "custom",
        "custom_fields[1][label][custom]": "Départements (ex. 33, 47)" if offre_key == "essentiel" else "Départements ou « France »",
    }
    for i, (k, m) in enumerate(metiers.items()):
        d[f"custom_fields[0][dropdown][options][{i}][label]"] = m["libelle"][:100]
        d[f"custom_fields[0][dropdown][options][{i}][value]"] = k.replace("-", "")
    if offre_key == "pro":
        d.update({"custom_fields[2][key]": "motscles", "custom_fields[2][type]": "text", "custom_fields[2][optional]": "true",
                  "custom_fields[2][label][type]": "custom", "custom_fields[2][label][custom]": "Mots-clés en plus (facultatif)"})
    return d


def run():
    biz, metiers = business(), load_metiers()
    mode = "test" if os.environ["STRIPE_SECRET_KEY"].strip().split("_")[1] == "test" else "réel"
    results = {}
    for key, offre in biz["offres"].items():
        product = _find_product(key) or _call("POST", "products", data={
            "name": f"{biz['nom']} {offre['nom']}", "metadata[radar_offre]": key,
            "description": "Alertes quotidiennes d'appels d'offres publics, triées et résumées par IA."})
        price = _price_for(product, int(offre["prix_mensuel"] * 100))
        link = None
        for pl in _call("GET", "payment_links", params={"active": "true", "limit": 100})["data"]:
            if (pl.get("metadata") or {}).get("radar_offre") == key and (pl.get("metadata") or {}).get("radar_price") == price["id"]:
                link = pl
        if not link:
            data = {"line_items[0][price]": price["id"], "line_items[0][quantity]": 1,
                    "subscription_data[trial_period_days]": biz["essai_gratuit_jours"],
                    "allow_promotion_codes": "true", "billing_address_collection": "auto",
                    "tax_id_collection[enabled]": "true",
                    "after_completion[type]": "hosted_confirmation",
                    "after_completion[hosted_confirmation][custom_message]":
                        f"Merci ! Votre veille {biz['nom']} est active : vous recevrez un e-mail de bienvenue puis vos premières alertes dès le prochain matin ouvré.",
                    "metadata[radar_offre]": key, "metadata[radar_price]": price["id"], **_link_fields(key, metiers)}
            link = _call("POST", "payment_links", data=data)
        results[key] = {"lien": link["url"], "price": price["id"]}
        print(f"[{mode}] {offre['nom']} : {link['url']} (prix {price['id']})")

    portal = None
    try:
        confs = _call("GET", "billing_portal/configurations", params={"limit": 10})["data"]
        conf = next((c for c in confs if (c.get("metadata") or {}).get("radar") == "1"), None)
        if not conf:
            conf = _call("POST", "billing_portal/configurations", data={
                "business_profile[headline]": f"{biz['nom']} : gérer mon abonnement",
                "features[subscription_cancel][enabled]": "true", "features[subscription_cancel][mode]": "at_period_end",
                "features[payment_method_update][enabled]": "true", "features[invoice_history][enabled]": "true",
                "login_page[enabled]": "true", "metadata[radar]": "1"})
        portal = (conf.get("login_page") or {}).get("url")
        print(f"[{mode}] Portail client : {portal}")
    except RuntimeError as exc:
        print("Portail client non créé (à activer dans Stripe) :", exc)

    # Écriture dans config/business.yaml (édition textuelle pour garder les commentaires)
    path = ROOT / "config" / "business.yaml"
    text = path.read_text(encoding="utf-8")
    for key, r in results.items():
        block = re.search(rf"\n  {key}:\n(?:    .*\n)+", text).group(0)
        new = re.sub(r'lien_paiement: ".*?"', f'lien_paiement: "{r["lien"]}"', block)
        new = re.sub(r"stripe_price_ids: \[.*?\]", f'stripe_price_ids: ["{r["price"]}"]', new)
        text = text.replace(block, new)
    if portal:
        text = re.sub(r'portail_client: ".*?"', f'portail_client: "{portal}"', text) if "portail_client:" in text \
            else text.replace("essai_gratuit_jours:", f'portail_client: "{portal}"\nessai_gratuit_jours:', 1)
    text = re.sub(r'stripe_mode: ".*?"\n', "", text)
    text = text.replace("essai_gratuit_jours:", f'stripe_mode: "{mode}"\nessai_gratuit_jours:', 1)
    path.write_text(text, encoding="utf-8")
    return results


if __name__ == "__main__":
    run()
