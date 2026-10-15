"""Fiche « avant de répondre » : ce que l'acheteur a déjà attribué dans votre métier.
Source : données essentielles de la commande publique (DECP, data.economie.gouv.fr, open data gratuit),
reliées à l'avis par le SIRET de l'acheteur. Noms des entreprises : annuaire officiel recherche-entreprises."""
import json
import logging
import statistics
from datetime import date, timedelta

import requests

from .config import ROOT

log = logging.getLogger(__name__)
DECP = "https://data.economie.gouv.fr/api/explore/v2.1/catalog/datasets/decp-2022-marches-valides/records"
ANNUAIRE = "https://recherche-entreprises.api.gouv.fr/search"
NOMS = ROOT / "data" / "entreprises.json"   # cache SIRET → nom (données publiques)


def _int(v):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


class Historique:
    def __init__(self, metiers, session=None, mois=24):
        self.metiers, self.http, self.mois = metiers, session or requests, mois
        self._cache = {}
        try:
            self.noms = json.loads(NOMS.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.noms = {}

    def nom(self, siret):
        if not siret:
            return ""
        if siret not in self.noms:
            try:
                r = self.http.get(ANNUAIRE, params={"q": siret, "per_page": 1}, timeout=20)
                res = (r.json().get("results") or [{}])[0] if r.ok else {}
                self.noms[siret] = (res.get("nom_raison_sociale") or res.get("nom_complet") or "").strip()
            except Exception as exc:
                log.warning("Annuaire indisponible : %s", exc)
                return ""
        return self.noms[siret]

    def fiche(self, siret, metier):
        """Résumé des marchés attribués par cet acheteur dans ce métier (None si rien de fiable)."""
        cpvs = (self.metiers.get(metier) or {}).get("cpv") or []
        if not siret or not cpvs:
            return None
        key = (siret, metier)
        if key in self._cache:
            return self._cache[key]
        depuis = (date.today() - timedelta(days=30 * self.mois)).isoformat()
        cpv_where = " or ".join(f'startswith(codecpv, "{c}")' for c in cpvs)
        try:
            r = self.http.get(DECP, timeout=60, params={
                "where": f'acheteur_id = "{siret}" and datenotification >= date\'{depuis}\' and ({cpv_where})',
                "select": "id, objet, montant, offresrecues, titulaire_id_1, datenotification",
                "order_by": "datenotification desc", "limit": 100})
            r.raise_for_status()
            rows = r.json().get("results") or []
        except Exception as exc:
            log.warning("Données essentielles indisponibles : %s", exc)
            return None
        vus, lignes = set(), []
        for x in rows:
            k = (x.get("id"), x.get("titulaire_id_1"), x.get("montant"))
            if k not in vus:
                vus.add(k)
                lignes.append(x)
        if not lignes:
            self._cache[key] = None
            return None
        montants = [m for m in (_int(x.get("montant")) for x in lignes) if m and m > 0]
        offres = [o for o in (_int(x.get("offresrecues")) for x in lignes) if o and o > 0]
        derniers = [{"date": (x.get("datenotification") or "")[:10], "objet": (x.get("objet") or "").strip()[:90],
                     "montant": _int(x.get("montant")), "offres": _int(x.get("offresrecues")),
                     "gagnant": self.nom(str(x.get("titulaire_id_1") or ""))} for x in lignes[:3]]
        out = {"n": len(lignes), "mois": self.mois,
               "montant_median": int(statistics.median(montants)) if montants else None,
               "offres_median": int(round(statistics.median(offres))) if offres else None,
               "derniers": derniers}
        self._cache[key] = out
        return out

    def sauver(self):
        try:
            NOMS.parent.mkdir(parents=True, exist_ok=True)
            NOMS.write_text(json.dumps(dict(sorted(self.noms.items())[-20000:]), ensure_ascii=False, indent=0), encoding="utf-8")
        except OSError as exc:
            log.warning("Cache des noms non enregistré : %s", exc)
