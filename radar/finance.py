"""Suivi financier : chiffre d'affaires (Stripe), frais, dépenses, cotisations et revenu net estimé."""
import csv
import os
from datetime import date, datetime, timezone

import requests

from .config import ROOT

STRIPE = "https://api.stripe.com/v1"


def _ts(d):
    return int(datetime(d.year, d.month, d.day, tzinfo=timezone.utc).timestamp())


def stripe_revenue(start: date, end: date, session=None):
    """CA encaissé, frais et remboursements sur la période (en euros)."""
    key = (os.getenv("STRIPE_SECRET_KEY") or "").strip()
    res = {"ca_brut": 0.0, "frais_stripe": 0.0, "remboursements": 0.0, "paiements": 0}
    if not key:
        return res
    http = session or requests
    params = {"created[gte]": _ts(start), "created[lt]": _ts(end), "limit": 100}
    while True:
        r = http.get(f"{STRIPE}/balance_transactions", params=params, auth=(key, ""), timeout=60)
        r.raise_for_status()
        body = r.json()
        for t in body["data"]:
            if t["type"] in ("charge", "payment"):
                res["ca_brut"] += t["amount"] / 100
                res["frais_stripe"] += t["fee"] / 100
                res["paiements"] += 1
            elif t["type"] in ("refund", "payment_refund"):
                res["remboursements"] += -t["amount"] / 100
            elif t["type"] == "stripe_fee":
                res["frais_stripe"] += -t["amount"] / 100
        if not body.get("has_more"):
            break
        params["starting_after"] = body["data"][-1]["id"]
    return {k: round(v, 2) if isinstance(v, float) else v for k, v in res.items()}


def depenses(start: date, end: date):
    path = ROOT / "finance" / "depenses.csv"
    total, lignes = 0.0, []
    with open(path, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            try:
                d = date.fromisoformat(row["date"])
            except (ValueError, KeyError):
                continue
            if start <= d < end:
                m = float(row["montant_eur"].replace(",", "."))
                total += m
                lignes.append(row)
    return round(total, 2), lignes


def bilan(ca_brut, remboursements, frais_stripe, autres_depenses, fiscal):
    """Trois niveaux, comme demandé : CA, bénéfice après dépenses, revenu après cotisations et impôt."""
    ca = round(ca_brut - remboursements, 2)
    benefice = round(ca - frais_stripe - autres_depenses, 2)
    taux = fiscal["taux_cotisations"] + fiscal["taux_cfp"] + fiscal["taux_versement_liberatoire"]
    prelevements = round(ca * taux, 2)  # en micro-entreprise, calculés sur le CA, pas sur le bénéfice
    return {"chiffre_affaires": ca, "frais_stripe": frais_stripe, "autres_depenses": autres_depenses,
            "benefice_apres_depenses": benefice, "cotisations_et_impot": prelevements,
            "taux_prelevement": round(taux, 4), "revenu_net": round(benefice - prelevements, 2)}


def mrr(subscribers_stripe_raw):
    """MRR = somme des abonnements payants actifs (hors essai)."""
    total = 0.0
    for s in subscribers_stripe_raw:
        if s["status"] != "active":
            continue
        for it in (s.get("items") or {}).get("data", []):
            p = it.get("price") or {}
            amount = (p.get("unit_amount") or 0) / 100 * (it.get("quantity") or 1)
            interval = (p.get("recurring") or {}).get("interval", "month")
            total += amount / 12 if interval == "year" else amount
    return round(total, 2)


def list_subscriptions(session=None):
    key = (os.getenv("STRIPE_SECRET_KEY") or "").strip()
    if not key:
        return []
    http = session or requests
    out, params = [], {"status": "all", "limit": 100}
    while True:
        r = http.get(f"{STRIPE}/subscriptions", params=params, auth=(key, ""), timeout=60)
        r.raise_for_status()
        body = r.json()
        out += body["data"]
        if not body.get("has_more"):
            return out
        params["starting_after"] = body["data"][-1]["id"]
