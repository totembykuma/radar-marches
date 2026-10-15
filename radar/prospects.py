"""Prospection : trouve les entreprises qui viennent de gagner un marché public (avis « Résultat de marché »
du BOAMP) et leur écrit UNE seule fois, avec les marchés ouverts de leur métier près de chez elles.

Garde-fous :
- chaque adresse (et chaque nom de domaine d'entreprise) n'est contactée qu'une fois, jamais relancée ;
- plafond quotidien (config business.yaml → prospection.par_jour) ;
- les clients Stripe et les adresses publiques (mairies, administrations) sont exclus ;
- seules des empreintes (sha256) des adresses contactées sont conservées, dans data/prospection.json :
  le dépôt est public, aucune adresse n'y est jamais écrite.
"""
import hashlib
import json
import logging
import re
from datetime import date, timedelta

import requests

from .boamp import API, normalize

log = logging.getLogger(__name__)
EMAIL = re.compile(r"^[\w.+'-]+@([\w-]+\.)+[a-z]{2,}$", re.I)
WEBMAILS = {"gmail.com", "hotmail.com", "hotmail.fr", "outlook.com", "outlook.fr", "yahoo.fr", "yahoo.com",
            "orange.fr", "wanadoo.fr", "free.fr", "sfr.fr", "laposte.net", "live.fr", "icloud.com", "neuf.fr",
            "bbox.fr", "aol.com", "gmx.fr", "msn.com", "me.com", "numericable.fr"}
PUBLIC = re.compile(r"(\.gouv\.fr|\.gov|mairie|ville-|cc-|agglo|departement|region|chu-|ch-|\.cnrs\.fr|"
                    r"ac-[a-z-]+\.fr|univ-|\.education\.fr|sdis|hopital|aphp)", re.I)


def h(s):
    return hashlib.sha256(s.strip().lower().encode()).hexdigest()[:24]


def _txt(v):
    if isinstance(v, dict):
        return _txt(v.get("#text") or v.get("$") or "")
    if isinstance(v, list):
        return _txt(v[0]) if v else ""
    return str(v or "").strip()


def _list(v):
    return v if isinstance(v, list) else ([] if v is None else [v])


def _dig(node, *keys):
    for k in keys:
        if isinstance(node, list):
            node = node[0] if node else None
        if not isinstance(node, dict):
            return None
        node = node.get(k)
    return node


def _walk_find(node, key):
    """Toutes les valeurs d'une clé, à n'importe quelle profondeur."""
    out = []
    if isinstance(node, dict):
        for k, v in node.items():
            if k == key:
                out.extend(_list(v))
            out.extend(_walk_find(v, key))
    elif isinstance(node, list):
        for v in node:
            out.extend(_walk_find(v, key))
    return out


def _eforms_winners(data):
    """Avis européens (eForms) : organisations désignées comme soumissionnaires retenus."""
    tenderer_ids = {_txt(_dig(t, "cbc:ID")) for t in _walk_find(data, "efac:Tenderer")}
    out = []
    for org in _walk_find(data, "efac:Organization"):
        comp = _dig(org, "efac:Company") or {}
        oid = _txt(_dig(comp, "cac:PartyIdentification", "cbc:ID"))
        if not oid or oid not in tenderer_ids:
            continue
        out.append({"nom": _txt(_dig(comp, "cac:PartyName", "cbc:Name")),
                    "email": _txt(_dig(comp, "cac:Contact", "cbc:ElectronicMail")),
                    "cp": _txt(_dig(comp, "cac:PostalAddress", "cbc:PostalZone"))})
    return out


def _mapa_winners(data):
    """Avis nationaux (MAPA, FNSimple…) : blocs « titulaire »."""
    out = []
    for t in _walk_find(data, "titulaire"):
        if not isinstance(t, dict):
            continue
        coord = t.get("coord") if isinstance(t.get("coord"), dict) else {}
        out.append({"nom": _txt(t.get("denomination") or coord.get("nom")),
                    "email": _txt(coord.get("mel")), "cp": _txt(coord.get("cp"))})
    return out


def winners(rec):
    try:
        data = json.loads(rec.get("donnees") or "{}") if isinstance(rec.get("donnees"), str) else (rec.get("donnees") or {})
    except ValueError:
        return []
    found = _eforms_winners(data) + _mapa_winners(data)
    clean, seen = [], set()
    for w in found:
        e = w["email"].lower().strip().strip(".;,")
        if not EMAIL.match(e) or PUBLIC.search(e) or e in seen:
            continue
        seen.add(e)
        cp = re.sub(r"\D", "", w["cp"])
        dep = (cp[:3] if cp.startswith("97") else cp[:2]) if len(cp) == 5 else ""
        clean.append({**w, "email": e, "dep": dep})
    return clean


def fetch_results(days=7, session=None):
    since = (date.today() - timedelta(days=days)).isoformat()
    http = session or requests
    r = http.get(f"{API}/exports/json", params={
        "where": f"dateparution >= date'{since}' and nature_libelle = \"Résultat de marché\"",
        "order_by": "dateparution desc"}, timeout=180)
    r.raise_for_status()
    return r.json()


def candidates(records, matcher, exclude_hashes, client_emails=()):
    """Une entrée par entreprise gagnante, avec le métier déduit du marché remporté."""
    clients = {e.lower() for e in client_emails}
    client_doms = {"dom:" + e.split("@")[1] for e in clients if "@" in e and e.split("@")[1] not in WEBMAILS}
    out, seen_dom = [], set()
    for rec in records:
        avis = normalize(rec)
        metiers = matcher.metiers_of(avis)
        if not metiers:
            continue
        for w in winners(rec):
            dom = w["email"].split("@")[1]
            dom_key = None if dom in WEBMAILS else "dom:" + dom
            if w["email"] in clients or h(w["email"]) in exclude_hashes or (dom_key and h(dom_key) in exclude_hashes):
                continue
            if dom_key and (dom_key in seen_dom or dom_key in client_doms):
                continue
            if dom_key:
                seen_dom.add(dom_key)
            deps = [w["dep"]] if w["dep"] else avis["departements"][:1]
            out.append({**w, "metier": metiers[0], "dep": deps[0] if deps else "",
                        "marche_gagne": avis["objet"], "acheteur": avis["acheteur"], "dom_key": dom_key})
    return out
