"""Récupération des avis du BOAMP (open data DILA, API Opendatasoft, gratuite, sans clé)."""
import json
import logging
import re
from datetime import date, timedelta

import requests

API = "https://boamp-datadila.opendatasoft.com/api/explore/v2.1/catalog/datasets/boamp"
log = logging.getLogger(__name__)

# Le schéma BOAMP a évolué dans le temps : on essaie plusieurs noms de champs.
FIELDS = {
    "id": ["idweb", "id", "boamp_idweb"],
    "objet": ["objet", "boamp_objetmarche", "boamp_objet_complet"],
    "acheteur": ["nomacheteur", "boamp_nomacheteur"],
    "departements": ["code_departement", "boamp_numerodepartement", "code_departement_prestation"],
    "descripteurs": ["descripteur_libelle", "boamp_descripteur"],
    "date_parution": ["dateparution", "boamp_dateparution"],
    "date_limite": ["datelimitereponse", "boamp_datelimitereponse"],
    "url": ["url_avis", "boamp_displayurl"],
    "nature": ["nature_libelle", "nature", "type_avis"],
    "type_marche": ["type_marche", "famille_libelle"],
}


def _first(rec, keys):
    for k in keys:
        v = rec.get(k)
        if v not in (None, "", []):
            return v
    return None


def _as_list(v):
    if v is None:
        return []
    if isinstance(v, list):
        return [str(x) for x in v if x not in (None, "")]
    return [s.strip() for s in str(v).replace(";", ",").split(",") if s.strip()]


def _extract_text(donnees):
    """Le champ 'donnees' est un JSON stocké en texte : on en tire un court descriptif."""
    if not donnees:
        return ""
    try:
        data = json.loads(donnees) if isinstance(donnees, str) else donnees
    except (ValueError, TypeError):
        return ""
    found = []

    def walk(node, key=""):
        if len(" ".join(found)) > 1500:
            return
        if isinstance(node, dict):
            for k, v in node.items():
                walk(v, k)
        elif isinstance(node, list):
            for v in node:
                walk(v, key)
        elif isinstance(node, str) and key.lower() in ("description", "objet_complet", "objet", "intitule", "caracteristiques", "quantite"):
            if len(node) > 20:
                found.append(node.strip())

    walk(data)
    return " ".join(dict.fromkeys(found))[:1500]


def _siret_acheteur(donnees):
    """SIRET de l'acheteur, lu dans le détail de l'avis (formulaires nationaux ou eForms).
    Ne lève jamais d'erreur : un format inattendu donne simplement « pas de SIRET »."""
    try:
        return _siret_brut(donnees)
    except Exception:
        return ""


def _one(v):
    return (v[0] if v else {}) if isinstance(v, list) else (v if isinstance(v, dict) else {})


def _siret_brut(donnees):
    try:
        data = json.loads(donnees) if isinstance(donnees, str) else (donnees or {})
    except ValueError:
        return ""

    def txt(v):
        if isinstance(v, list):
            v = v[0] if v else ""
        if isinstance(v, dict):
            v = v.get("#text") or v.get("$") or ""
        return "".join(c for c in str(v or "") if c.isdigit())

    fn = (data.get("FNSimple") or {}).get("organisme") or {}
    if isinstance(fn, dict) and len(txt(fn.get("codeIdentificationNational"))) == 14:
        return txt(fn.get("codeIdentificationNational"))
    ef = data.get("EFORMS") if isinstance(data.get("EFORMS"), dict) else {}
    root = next((v for v in ef.values() if isinstance(v, dict)), {})
    party = root.get("cac:ContractingParty") or {}
    party = party[0] if isinstance(party, list) and party else party
    ref = ""
    if isinstance(party, dict):
        pid = ((party.get("cac:Party") or {}).get("cac:PartyIdentification") or {})
        pid = pid[0] if isinstance(pid, list) and pid else pid
        ref = (pid.get("cbc:ID") if isinstance(pid, dict) else "") or ""
        ref = ref.get("#text", "") if isinstance(ref, dict) else str(ref)
    try:
        orgs = root["ext:UBLExtensions"]["ext:UBLExtension"]["ext:ExtensionContent"]["efext:EformsExtension"][
            "efac:Organizations"]["efac:Organization"]
    except (KeyError, TypeError):
        return ""
    for org in orgs if isinstance(orgs, list) else [orgs]:
        comp = _one(_one(org).get("efac:Company"))
        oid = _one(comp.get("cac:PartyIdentification")).get("cbc:ID")
        oid = oid.get("#text", "") if isinstance(oid, dict) else str(oid or "")
        siret = txt(_one(comp.get("cac:PartyLegalEntity")).get("cbc:CompanyID"))
        if len(siret) == 14 and (not ref or oid == ref):
            return siret
    return ""


def _ville_acheteur(donnees, departements=()):
    """Ville de l'acheteur (ou du lieu d'exécution), lue dans le détail de l'avis : « Lyon », ou "".
    Gardée seulement si son code postal tombe dans un département de l'avis (pages cohérentes)."""
    try:
        return _ville_brute(donnees, departements)
    except Exception:
        return ""


def _nom_ville(v):
    v = re.sub(r"\bcedex\b.*$|\d+", "", str(v or ""), flags=re.I).strip(" -,")
    if not v or len(v) > 40:
        return ""
    return "-".join(w[:1].upper() + w[1:].lower() if w.lower() not in ("sur", "en", "le", "la", "les", "de", "du", "des", "et", "lès", "sous") or i == 0
                    else w.lower() for i, w in enumerate(re.split(r"[- ]+", v)))


def _ville_brute(donnees, departements):
    data = json.loads(donnees) if isinstance(donnees, str) else (donnees or {})
    deps = {str(d) for d in departements}

    def txt(v):
        if isinstance(v, list):
            v = v[0] if v else ""
        return (v.get("#text") or "") if isinstance(v, dict) else str(v or "")

    cands = []
    for fmt in ("FNSimple", "MAPA"):
        org = (data.get(fmt) or {}).get("organisme") or {}
        if isinstance(org, dict):
            adr = org.get("adr") if isinstance(org.get("adr"), dict) else org
            cands.append((adr.get("ville"), adr.get("cp")))
    ef = data.get("EFORMS") if isinstance(data.get("EFORMS"), dict) else {}
    root = next((v for v in ef.values() if isinstance(v, dict)), {})
    if root:
        party = _one(root.get("cac:ContractingParty"))
        ref = txt(_one(_one(party.get("cac:Party")).get("cac:PartyIdentification")).get("cbc:ID"))
        try:
            orgs = root["ext:UBLExtensions"]["ext:UBLExtension"]["ext:ExtensionContent"]["efext:EformsExtension"][
                "efac:Organizations"]["efac:Organization"]
        except (KeyError, TypeError):
            orgs = []
        for org in orgs if isinstance(orgs, list) else [orgs]:
            comp = _one(_one(org).get("efac:Company"))
            if ref and txt(_one(comp.get("cac:PartyIdentification")).get("cbc:ID")) != ref:
                continue
            adr = _one(comp.get("cac:PostalAddress"))
            cands.append((txt(adr.get("cbc:CityName")), txt(adr.get("cbc:PostalZone"))))
            break
    for ville, cp in cands:
        cp = "".join(c for c in txt(cp) if c.isdigit())
        nom = _nom_ville(txt(ville))
        if not nom or len(cp) != 5:
            continue
        dep = cp[:3] if cp[:2] in ("97", "98") else ("2A" if cp[:3] in ("200", "201") else "2B" if cp[:2] == "20" else cp[:2])
        if not deps or dep in deps or cp[:2] in deps:
            return nom
    return ""


MONTANT_CLES = re.compile(r"estimatedoverallcontractamount|valeur_?estim|montant_?estim|valeurtotale|estimationvaleur", re.I)


def _montant_estime(donnees):
    """Montant estimé HT du marché, tel que l'acheteur l'a publié (eForms ou formulaires nationaux), ou None.
    Jamais inventé : sans montant publié, rien n'est affiché."""
    try:
        data = json.loads(donnees) if isinstance(donnees, str) else (donnees or {})
    except ValueError:
        return None
    vals = []

    def num(v):
        if isinstance(v, dict):
            v = v.get("#text") or v.get("$") or v.get("valeur") or ""
        v = re.sub(r"[\s\u00a0\u202f€]", "", str(v or "")).replace(",", ".")
        try:
            return float(v)
        except ValueError:
            return None

    def walk(node, depth=0):
        if depth > 30:
            return
        if isinstance(node, dict):
            for k, v in node.items():
                if MONTANT_CLES.search(str(k)) and not isinstance(v, (list,)) and (n := num(v)):
                    vals.append(n)
                else:
                    walk(v, depth + 1)
        elif isinstance(node, list):
            for v in node:
                walk(v, depth + 1)

    try:
        walk(data)
    except Exception:
        return None
    vals = [v for v in vals if 1_000 <= v <= 500_000_000]
    return int(max(vals)) if vals else None


def _pme(rec):
    """Badge « Accessible PME » : petits marchés (MAPA < 90 k€, national < seuils européens)
    ou lot que l'acheteur déclare adapté aux PME. Renvoie la raison, ou "" si non."""
    famille = str(rec.get("famille") or "").upper()
    if famille == "MAPA":
        return "Petit marché (moins de 90 000 €)"
    if famille == "FNS":
        return "Marché national (sous les seuils européens)"
    d = rec.get("donnees")
    txt = d if isinstance(d, str) else json.dumps(d or {})
    if re.search(r'"cbc:SMESuitableIndicator"\s*:\s*"?true', txt, re.I):
        return "Lot déclaré adapté aux PME par l'acheteur"
    return ""


def normalize(rec):
    avis_id = str(_first(rec, FIELDS["id"]) or "")
    url = _first(rec, FIELDS["url"]) or (f"https://www.boamp.fr/pages/avis/?q=idweb:%22{avis_id}%22" if avis_id else "")
    out = {
        "id": avis_id,
        "objet": (_first(rec, FIELDS["objet"]) or "").strip(),
        "acheteur": (_first(rec, FIELDS["acheteur"]) or "").strip(),
        "departements": [d.zfill(2) if d.isdigit() and len(d) == 1 else d for d in _as_list(_first(rec, FIELDS["departements"]))],
        "descripteurs": _as_list(_first(rec, FIELDS["descripteurs"])),
        "date_parution": str(_first(rec, FIELDS["date_parution"]) or "")[:10],
        "date_limite": str(_first(rec, FIELDS["date_limite"]) or "")[:16],
        "url": url,
        "nature": str(_first(rec, FIELDS["nature"]) or ""),
        "type_marche": " ".join(_as_list(_first(rec, FIELDS["type_marche"]))),
        "texte": _extract_text(rec.get("donnees")),
        "acheteur_siret": _siret_acheteur(rec.get("donnees")),
        "pme": _pme(rec),
        "montant_estime": _montant_estime(rec.get("donnees")),
    }
    out["ville"] = _ville_acheteur(rec.get("donnees"), out["departements"])
    return out


def fetch_since(since: date, until: date | None = None, session=None, limit=None):
    """Tous les avis publiés entre since et until (inclus), via l'endpoint d'export."""
    until = until or date.today()
    where = f"dateparution >= date'{since.isoformat()}' AND dateparution <= date'{until.isoformat()}'"
    params = {"where": where, "order_by": "dateparution desc"}
    if limit:
        params["limit"] = limit
    http = session or requests
    r = http.get(f"{API}/exports/json", params=params, timeout=120)
    r.raise_for_status()
    rows = r.json()
    log.info("BOAMP: %d avis récupérés (%s → %s)", len(rows), since, until)
    return [normalize(x) for x in rows]


def fetch_open(days=30, session=None):
    """Avis publiés ces N derniers jours dont la date limite n'est pas dépassée (pour le site)."""
    today = date.today().isoformat()
    avis = fetch_since(date.today() - timedelta(days=days), session=session)
    return [a for a in avis if not a["date_limite"] or a["date_limite"][:10] >= today]


def schema_sample(session=None):
    """Diagnostic : renvoie les noms de champs réels d'un avis récent."""
    http = session or requests
    r = http.get(f"{API}/records", params={"limit": 1, "order_by": "dateparution desc"}, timeout=60)
    r.raise_for_status()
    results = r.json().get("results", [])
    return sorted(results[0].keys()) if results else []
