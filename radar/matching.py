"""Associe les avis aux métiers et aux critères des abonnés."""
import re

from .textutil import norm


def _compile(words):
    return [re.compile(r"\b" + re.escape(norm(w))) for w in words if w]


class Matcher:
    def __init__(self, metiers):
        self.metiers = metiers
        self._inc = {k: _compile(v.get("mots", [])) for k, v in metiers.items()}
        self._exc = {k: _compile(v.get("exclure", [])) for k, v in metiers.items()}

    def haystack(self, avis, texte=True):
        extra = avis.get("texte", "")[:400] if texte else ""
        return norm(f"{avis['objet']} {' '.join(avis['descripteurs'])} {extra}")

    def metiers_of(self, avis):
        full, court = self.haystack(avis), self.haystack(avis, texte=False)
        out = []
        for key in self.metiers:
            text = full if self.metiers[key].get("texte", False) else court
            if any(p.search(text) for p in self._exc[key]):
                continue
            if any(p.search(text) for p in self._inc[key]):
                out.append(key)
        return out

    def is_award_notice(self, avis):
        n = norm(avis.get("nature", ""))
        return "attribution" in n or "resultat" in n

    def for_subscriber(self, avis_list, sub):
        """Avis utiles pour un abonné : bon métier (ou mot-clé perso), bon département, avis d'appel (pas d'attribution)."""
        deps = set(sub.get("departements") or [])
        extra = _compile(sub.get("mots_cles") or [])
        out = []
        for a in avis_list:
            if self.is_award_notice(a):
                continue
            if deps and not deps.intersection(a["departements"]):
                continue
            hit = set(sub["metiers"]).intersection(a.get("metiers") or self.metiers_of(a))
            if not hit and extra:
                hit = any(p.search(self.haystack(a)) for p in extra)
            if hit:
                out.append(a)
        return out


def parse_departements(raw):
    """'33, 47 et 2A' → ['33', '47', '2A'] ; 'France' / vide → [] (= toute la France)."""
    if not raw:
        return []
    if "france" in norm(raw) or "tous" in norm(raw):
        return []
    tokens = re.findall(r"\b(\d{2,3}|2[ab])\b", norm(raw))
    return list(dict.fromkeys(t.upper() for t in tokens))  # ordre du client conservé
