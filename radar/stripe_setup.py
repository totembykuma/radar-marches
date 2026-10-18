"""Crée (une seule fois) dans Stripe les offres, leurs liens de paiement avec essai et champs personnalisés,
et le portail client. Fonctionne en mode test (sk_test_…) comme en réel (sk_live_… / rk_live_…).
Les liens et identifiants de prix sont écrits dans config/business.yaml."""
import os
import re
from datetime import datetime, timezone

import requests

from .config import ROOT, business, metiers as load_metiers

API = "https://api.stripe.com/v1"
DESC = "Alertes quotidiennes par e-mail des appels d'offres publics de votre métier et de vos départements."
DESC_CLE = (DESC + " Pour chaque marché qui vous intéresse : rapport « comment gagner » (critères, pièces, prix de repère)"
            " et 2 dossiers de réponse prêts à signer par mois.")


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


def _price_for(product, amount_cents, interval="month"):
    for pr in _call("GET", "prices", params={"product": product["id"], "active": "true", "limit": 100})["data"]:
        if pr["unit_amount"] == amount_cents and (pr.get("recurring") or {}).get("interval") == interval:
            return pr
    return _call("POST", "prices", data={"product": product["id"], "currency": "eur", "unit_amount": amount_cents,
                                         "recurring[interval]": interval, "tax_behavior": "inclusive"})


def _link_fields(offre_key, metiers):
    d = {
        "custom_fields[0][key]": "metier", "custom_fields[0][type]": "dropdown",
        "custom_fields[0][label][type]": "custom", "custom_fields[0][label][custom]": "Votre métier",
        "custom_fields[1][key]": "departements", "custom_fields[1][type]": "text",
        "custom_fields[1][label][type]": "custom",
        "custom_fields[1][label][custom]": "Votre département (ex. 33)" if offre_key == "essentiel" else "Départements (ex. 33, 47) ou « France »",
    }
    for i, (k, m) in enumerate(metiers.items()):
        d[f"custom_fields[0][dropdown][options][{i}][label]"] = m["libelle"][:100]
        d[f"custom_fields[0][dropdown][options][{i}][value]"] = k.replace("-", "")
    if offre_key in ("pro", "cle"):
        d.update({"custom_fields[2][key]": "motscles", "custom_fields[2][type]": "text", "custom_fields[2][optional]": "true",
                  "custom_fields[2][label][type]": "custom", "custom_fields[2][label][custom]": "Mots-clés en plus (facultatif)"})
    return d


def _apres_paiement(biz):
    """Après le paiement, le client arrive sur la page « Merci, voici ce qui se passe » du site."""
    return {"after_completion[type]": "redirect",
            "after_completion[redirect][url]": biz["site_url"].rstrip("/") + "/merci/"}


def _texte_paiement(biz, offre):
    """Texte affiché sur la page de paiement : essai unique et grille de prix exacte."""
    from .paliers import texte_grille
    return (f"Essai gratuit de {biz['essai_gratuit_jours']} jours, une seule fois par client (déjà abonné : 1er mois prélevé "
            f"tout de suite). Ensuite : {texte_grille(offre)}. Sans engagement, résiliable en un clic.")


def _link_for(key, price, biz, metiers, mode, offre):
    link = None
    for pl in _call("GET", "payment_links", params={"active": "true", "limit": 100})["data"]:
        if (pl.get("metadata") or {}).get("radar_offre") == key and (pl.get("metadata") or {}).get("radar_price") == price["id"]:
            link = pl
    if link:
        # Met à jour les champs (libellés, liste des métiers) si l'offre a changé.
        wanted = {k: v for k, v in _link_fields(key, metiers).items()}
        labels = [((f.get("label") or {}).get("custom")) for f in link.get("custom_fields") or []]
        options = [o.get("value") for f in link.get("custom_fields") or [] for o in (f.get("dropdown") or {}).get("options") or []]
        if labels != [v for k, v in wanted.items() if k.endswith("[label][custom]")] \
                or options != [v for k, v in wanted.items() if k.endswith("][value]")]:
            link = _call("POST", f"payment_links/{link['id']}", data=wanted)
            print(f"[{mode}] {offre['nom']} : champs du lien mis à jour")
        if (link.get("after_completion") or {}).get("type") != "redirect":
            link = _call("POST", f"payment_links/{link['id']}", data=_apres_paiement(biz))
            print(f"[{mode}] {offre['nom']} : redirection vers /merci/ après paiement")
        maj = {}
        if (price.get("recurring") or {}).get("interval") == "month":
            if ((link.get("subscription_data") or {}).get("trial_period_days")) != biz["essai_gratuit_jours"]:
                maj["subscription_data[trial_period_days]"] = biz["essai_gratuit_jours"]
            if (((link.get("custom_text") or {}).get("submit") or {}).get("message")) != _texte_paiement(biz, offre):
                maj["custom_text[submit][message]"] = _texte_paiement(biz, offre)
        if maj:
            link = _call("POST", f"payment_links/{link['id']}", data=maj)
            print(f"[{mode}] {offre['nom']} : essai {biz['essai_gratuit_jours']} jours et grille de prix sur la page de paiement")
    if not link:
        data = {"line_items[0][price]": price["id"], "line_items[0][quantity]": 1,
                "subscription_data[trial_period_days]": biz["essai_gratuit_jours"],
                "allow_promotion_codes": "true", "billing_address_collection": "auto",
                "tax_id_collection[enabled]": "true",
                **_apres_paiement(biz),
                "metadata[radar_offre]": key, "metadata[radar_price]": price["id"], **_link_fields(key, metiers)}
        if (price.get("recurring") or {}).get("interval") == "month":   # grille dégressive : formules mensuelles seulement
            data["custom_text[submit][message]"] = _texte_paiement(biz, offre)
        link = _call("POST", "payment_links", data=data)
    return link


def _dossier(biz):
    """Option « Dossier prêt à signer » : un produit à l'unité, deux prix (normal et abonnés Pro), deux liens."""
    d = biz["dossier"]
    product = _find_product("dossier") or _call("POST", "products", data={
        "name": f"{biz['nom']} · {d['nom']}", "metadata[radar_offre]": "dossier",
        "description": "Réponse à un appel d'offres préparée pour vous : analyse, mémoire technique, lettre de candidature, DUME."})
    prices = {pr["unit_amount"]: pr for pr in _call("GET", "prices", params={"product": product["id"], "active": "true", "limit": 100})["data"]
              if not pr.get("recurring")}
    links = {(pl.get("metadata") or {}).get("radar_price"): pl
             for pl in _call("GET", "payment_links", params={"active": "true", "limit": 100})["data"]
             if (pl.get("metadata") or {}).get("radar_offre") == "dossier"}
    out = {"ids": []}
    for champ, prix in (("lien_paiement", d["prix"]), ("lien_pro", d["prix_pro"]), ("lien_cle", d.get("prix_cle"))):
        if not prix:
            continue
        price = prices.get(int(prix * 100)) or _call("POST", "prices", data={
            "product": product["id"], "currency": "eur", "unit_amount": int(prix * 100), "tax_behavior": "inclusive"})
        link = links.get(price["id"]) or _call("POST", "payment_links", data={
            "line_items[0][price]": price["id"], "line_items[0][quantity]": 1, "allow_promotion_codes": "true",
            "invoice_creation[enabled]": "true", "tax_id_collection[enabled]": "true",
            "after_completion[type]": "redirect",
            "after_completion[redirect][url]": biz["site_url"].rstrip("/") + "/dossier/envoyer/",
            "custom_fields[0][key]": "avis", "custom_fields[0][type]": "text", "custom_fields[0][optional]": "true",
            "custom_fields[0][label][type]": "custom", "custom_fields[0][label][custom]": "Lien ou référence de l'appel d'offres",
            "metadata[radar_offre]": "dossier", "metadata[radar_price]": price["id"]})
        out[champ] = link["url"]
        out["ids"].append(link["id"])
        print(f"{d['nom']} ({prix} €) : {link['url']}")
    return out


def run():
    biz, metiers = business(), load_metiers()
    mode = "test" if os.environ["STRIPE_SECRET_KEY"].strip().split("_")[1] == "test" else "réel"
    results = {}
    for key, offre in biz["offres"].items():
        desc = DESC_CLE if key == "cle" else DESC
        product = _find_product(key)
        if not product:
            product = _call("POST", "products", data={
                "name": f"{biz['nom']} {offre['nom']}", "metadata[radar_offre]": key, "description": desc})
        elif product.get("description") != desc:
            product = _call("POST", f"products/{product['id']}", data={"description": desc})
        results[key] = {}
        # Prix mensuel de départ puis un prix par palier d'ancienneté (radar/paliers.py) ; l'annuel reste à prix fixe.
        from .paliers import centimes, grille
        prix_mois = [_price_for(product, centimes(prix), "month") for _, prix in grille(offre)]
        results[key]["paliers"] = results[key]["prices"] = [pr["id"] for pr in prix_mois]
        link = _link_for(key, prix_mois[0], biz, metiers, mode, offre)
        results[key]["lien_paiement"] = link["url"]
        print(f"[{mode}] {offre['nom']} : {link['url']} (prix {', '.join(results[key]['paliers'])})")
        if offre.get("prix_annuel"):
            price = _price_for(product, centimes(offre["prix_annuel"]), "year")
            link = _link_for(key, price, biz, metiers, mode, offre)
            results[key]["lien_annuel"] = link["url"]
            results[key]["prices"] = results[key]["paliers"] + [price["id"]]

    dossier = _dossier(biz) if biz.get("dossier") else None

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
        new = block
        for champ in ("lien_paiement", "lien_annuel"):
            if r.get(champ):
                new = re.sub(rf'{champ}: ".*?"', f'{champ}: "{r[champ]}"', new)
        anciens = re.findall(r'"(price_[^"]+)"', (re.search(r"stripe_price_ids: \[.*?\]", new) or [""])[0])
        ids = ", ".join(f'"{i}"' for i in dict.fromkeys(anciens + r["prices"]))  # garder les anciens : abonnés existants
        new = re.sub(r"stripe_price_ids: \[.*?\]", f"stripe_price_ids: [{ids}]", new)
        paliers = ", ".join(f'"{i}"' for i in r["paliers"])
        new = re.sub(r"stripe_paliers: \[.*?\]", f"stripe_paliers: [{paliers}]", new) if "stripe_paliers:" in new \
            else new.replace("    stripe_price_ids:", f"    stripe_paliers: [{paliers}]\n    stripe_price_ids:", 1)
        text = text.replace(block, new)
    if dossier:
        block = re.search(r"\ndossier:\n(?:  .*\n)+", text).group(0)
        new = re.sub(r'lien_paiement: ".*?"', f'lien_paiement: "{dossier["lien_paiement"]}"', block)
        new = re.sub(r'lien_pro: ".*?"', f'lien_pro: "{dossier["lien_pro"]}"', new)
        if dossier.get("lien_cle"):
            new = re.sub(r'lien_cle: ".*?"', f'lien_cle: "{dossier["lien_cle"]}"', new)
        new = re.sub(r"stripe_link_ids: \[.*?\]", "stripe_link_ids: [" + ", ".join(f'"{i}"' for i in dossier["ids"]) + "]", new)
        text = text.replace(block, new)
    if portal:
        text = re.sub(r'portail_client: ".*?"', f'portail_client: "{portal}"', text) if "portail_client:" in text \
            else text.replace("essai_gratuit_jours:", f'portail_client: "{portal}"\nessai_gratuit_jours:', 1)
    if "nouvelle_grille_depuis:" not in text:  # date de mise en service des paliers et de l'essai unique
        quand = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        text = text.replace("essai_gratuit_jours:", f'nouvelle_grille_depuis: "{quand}"\nessai_gratuit_jours:', 1)
    text = re.sub(r'stripe_mode: ".*?"\n', "", text)
    text = text.replace("essai_gratuit_jours:", f'stripe_mode: "{mode}"\nessai_gratuit_jours:', 1)
    path.write_text(text, encoding="utf-8")
    return results


if __name__ == "__main__":
    run()
