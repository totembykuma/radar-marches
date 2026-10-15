"""Promo de lancement : crée dans Stripe le coupon (une fois) et le code promo du jour, limité aux N premiers.
Lancé à minuit (heure de Paris) par le workflow promo.yml pendant la période de la promo."""
import os
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from .config import business
from .stripe_setup import _call, _find_product

PARIS = ZoneInfo("Europe/Paris")


def code_du_jour(jour: date):
    return f"PRO{jour.day:02d}"


def run(jour: date | None = None):
    biz = business()
    p = biz.get("promo") or {}
    jour = jour or datetime.now(PARIS).date()
    if not p.get("publie"):
        print("Promo non publiée (promo.publie: false) : rien à faire.")
        return None
    if not (date.fromisoformat(p["debut"]) <= jour <= date.fromisoformat(p["fin"])):
        print(f"{jour} hors de la période de promo : rien à faire.")
        return None
    offre = biz["offres"][p["offre"]]
    product = _find_product(p["offre"])
    remise = int((offre["prix_mensuel"] - p["prix"]) * 100)
    cid = f"radar-promo-{p['offre']}-{p['debut']}"
    try:
        coupon = _call("GET", f"coupons/{cid}")
    except RuntimeError:
        coupon = _call("POST", "coupons", data={
            "id": cid, "name": f"Lancement : {offre['nom']} à {p['prix']} € pendant {p['mois']} mois",
            "amount_off": remise, "currency": "eur", "duration": "repeating", "duration_in_months": p["mois"],
            "applies_to[products][0]": product["id"]})
    code = code_du_jour(jour)
    existants = _call("GET", "promotion_codes", params={"code": code, "limit": 10})["data"]
    if any(c.get("coupon", {}).get("id") == cid or (c.get("promotion") or {}).get("coupon") == cid for c in existants):
        print(f"Code {code} déjà créé.")
        return code
    fin = datetime.combine(jour, time(23, 59, 59), PARIS)
    commun = {"code": code, "max_redemptions": p["places_par_jour"], "expires_at": int(fin.timestamp()),
              "restrictions[first_time_transaction]": "true"}
    try:  # API Stripe récente
        _call("POST", "promotion_codes", data={**commun, "promotion[type]": "coupon", "promotion[coupon]": cid})
    except RuntimeError:  # API plus ancienne
        _call("POST", "promotion_codes", data={**commun, "coupon": cid})
    print(f"Code {code} créé : -{remise / 100:.0f} € pendant {p['mois']} mois, {p['places_par_jour']} places, jusqu'à {fin:%d/%m %H:%M}.")
    return code


if __name__ == "__main__":
    import sys
    run(date.fromisoformat(sys.argv[1]) if len(sys.argv) > 1 else None)
