"""Les abonnés viennent directement de Stripe (aucune base de données à gérer).

Le client choisit son métier, ses départements et ses mots-clés dans les champs
personnalisés du lien de paiement Stripe. On relit ces champs à chaque envoi.
Un fichier data/abonnes_manuels.yaml permet d'ajouter des comptes test ou offerts.
"""
import logging
import os

import requests
import yaml

from .config import ROOT
from .matching import parse_departements
from .textutil import norm, slugify

log = logging.getLogger(__name__)
STRIPE = "https://api.stripe.com/v1"
ACTIVE = {"active", "trialing", "past_due"}


def _stripe_list(path, params, key, session):
    http = session or requests
    params = dict(params, limit=100)
    while True:
        r = http.get(f"{STRIPE}/{path}", params=params, auth=(key, ""), timeout=60)
        r.raise_for_status()
        body = r.json()
        yield from body["data"]
        if not body.get("has_more"):
            break
        params["starting_after"] = body["data"][-1]["id"]


def _custom_fields(session_obj):
    out = {}
    for f in session_obj.get("custom_fields") or []:
        k = f.get("key")
        v = (f.get("dropdown") or {}).get("value") or (f.get("text") or {}).get("value") \
            or (f.get("numeric") or {}).get("value")
        out[k] = v
    return out


def resolve_metier(value, metiers):
    """Accepte la clé ('espaces-verts') ou le libellé saisi dans Stripe."""
    if not value:
        return None
    s = slugify(value)
    compact = s.replace("-", "")  # Stripe n'accepte que des valeurs alphanumériques dans les listes
    for k, m in metiers.items():
        if s == k or compact == k.replace("-", "") or slugify(m["libelle"]) == s or norm(m["libelle"]).startswith(norm(value)):
            return k
    return None


def plan_for_price(price_id, biz):
    for key, offre in biz["offres"].items():
        if price_id in (offre.get("stripe_price_ids") or []):
            return key
    return "essentiel"


def from_stripe(biz, metiers, session=None):
    key = (os.getenv("STRIPE_SECRET_KEY") or "").strip()
    if not key:
        return []
    subs = {s["id"]: s for s in _stripe_list("subscriptions", {"status": "all"}, key, session)}
    out = []
    for cs in _stripe_list("checkout/sessions", {"status": "complete"}, key, session):
        sub = subs.get(cs.get("subscription"))
        if not sub or sub["status"] not in ACTIVE:
            continue
        cf = _custom_fields(cs)
        # Les métadonnées de l'abonnement Stripe (modifiables par le support) priment sur le formulaire initial.
        cf.update({k: v for k, v in (sub.get("metadata") or {}).items() if k in ("metier", "departements", "motscles") and v})
        items = (sub.get("items") or {}).get("data") or [{}]
        plan = plan_for_price((items[0].get("price") or {}).get("id"), biz)
        offre = biz["offres"][plan]
        metier = resolve_metier(cf.get("metier"), metiers)
        deps = parse_departements(cf.get("departements"))[: offre["departements_max"]]
        mots = [m.strip() for m in (cf.get("motscles") or cf.get("mots_cles") or "").split(",") if m.strip()]
        email = (cs.get("customer_details") or {}).get("email")
        if not email:
            continue
        out.append({"email": email, "plan": plan, "statut": sub["status"],
                    "metiers": [metier] if metier else [], "departements": deps,
                    "mots_cles": mots if plan == "pro" else [], "source": "stripe",
                    "stripe_subscription": sub["id"]})
    return out


def from_file():
    path = ROOT / "data" / "abonnes_manuels.yaml"
    if not path.exists():
        return []
    with open(path, encoding="utf-8") as f:
        rows = yaml.safe_load(f) or []
    for r in rows:
        r.setdefault("plan", "pro")
        r.setdefault("statut", "active")
        r.setdefault("mots_cles", [])
        r["departements"] = parse_departements(",".join(map(str, r.get("departements") or [])))
        r["source"] = "fichier"
    return rows


def load_all(biz, metiers, session=None):
    subs = from_file()
    try:
        subs += from_stripe(biz, metiers, session=session)
    except Exception as exc:
        log.error("Lecture Stripe impossible : %s", exc)
        raise
    seen, out = set(), []
    for s in subs:
        k = s["email"].lower()
        if k not in seen:
            seen.add(k)
            out.append(s)
    return out
