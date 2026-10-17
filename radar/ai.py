"""Résumés et notes de pertinence générés par Claude (API Anthropic). Facultatif : sans clé, on garde l'objet brut."""
import json
import logging
import os
import time

import requests

log = logging.getLogger(__name__)
API_URL = "https://api.anthropic.com/v1/messages"

PROMPT = """Tu aides des TPE/PME françaises à repérer les marchés publics qui valent la peine d'y répondre.
Pour chaque avis ci-dessous, rédige :
- "resume" : une phrase claire (max 30 mots) qui dit concrètement ce que l'acheteur veut, où, et toute info utile (durée, lots, montant si cité).
- "score" : de 1 à 5, accessibilité pour une petite entreprise du métier (5 = petit marché local simple ; 1 = gros marché national complexe ou hors métier).
- "a_verifier" : au plus 3 conditions qui peuvent éliminer une petite entreprise, seulement si le texte les cite (qualification ou certification exigée, chiffre d'affaires minimum, visite obligatoire, assurance particulière…), 8 mots max chacune. Rien de cité = liste vide : ne suppose jamais.
- "hors_metier" : true si l'avis ne relève clairement pas du métier indiqué dans "metiers", sinon false.
Réponds uniquement par un tableau JSON : [{{"id": "...", "resume": "...", "score": 3, "a_verifier": [], "hors_metier": false}}, ...]

Avis :
{avis}"""

PRIX = {"claude-haiku-5-5": (0.10, 0.50)}   # dollars par million de jetons (entrée, sortie)
PAUSE = 5                                   # secondes avant la 2e tentative d'un lot (0 dans les tests)
CHAMPS = ("resume", "score", "a_verifier", "hors_metier")


def cout_usd(model, entree, sortie):
    pe, ps = PRIX.get(model, (1.0, 5.0))    # modèle inconnu : estimation prudente
    return (entree * pe + sortie * ps) / 1e6


def _appliquer(a, d):
    if d.get("resume"):
        a["resume"] = str(d["resume"])[:300]
    try:
        a["score"] = max(1, min(5, int(d.get("score", 0))))
    except (TypeError, ValueError):
        pass
    av = d.get("a_verifier")
    a["a_verifier"] = [str(x)[:80] for x in av if str(x).strip()][:3] if isinstance(av, list) else []
    a["hors_metier"] = d.get("hors_metier") is True


def enrich(avis_list, model="claude-haiku-5-5", batch=25, session=None, cache=None, budget_usd=None):
    """Ajoute résumé, note, points à vérifier et avis « hors métier » (observation seulement, rien n'est filtré).
    cache : {id: résultat} déjà payé, réutilisé sans rappeler l'IA. budget_usd : ne plus appeler l'IA au-delà.
    Renvoie les compteurs du passage (jetons, coût, lots réussis / échoués, réutilisés)."""
    stats = {"entree": 0, "sortie": 0, "cout_usd": 0.0, "lots_ok": 0, "lots_echoues": 0, "depuis_cache": 0, "budget_atteint": False}
    key = (os.getenv("ANTHROPIC_API_KEY") or "").strip()
    cache = {} if cache is None else cache
    a_faire = []
    for a in avis_list:
        if a["id"] in cache:
            _appliquer(a, cache[a["id"]])
            stats["depuis_cache"] += 1
        else:
            a_faire.append(a)
    if not key or not a_faire:
        return stats
    http = session or requests
    for i in range(0, len(a_faire), batch):
        if budget_usd is not None and stats["cout_usd"] >= budget_usd:
            stats["budget_atteint"] = True
            break
        chunk = a_faire[i:i + batch]
        payload = "\n".join(
            json.dumps({"id": a["id"], "objet": a["objet"], "acheteur": a["acheteur"], "metiers": a.get("metiers_libelles", []),
                        "departements": a["departements"], "details": a.get("texte", "")[:600]}, ensure_ascii=False)
            for a in chunk)
        for essai in (1, 2):   # un lot invalide est retenté une fois, puis mis de côté (alertes envoyées sans résumé)
            try:
                r = http.post(API_URL, timeout=120, headers={
                    "x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
                    json={"model": model, "max_tokens": 6000,
                          "messages": [{"role": "user", "content": PROMPT.format(avis=payload)}]})
                r.raise_for_status()
                rep = r.json()
                u = rep.get("usage") or {}
                stats["entree"] += u.get("input_tokens", 0)
                stats["sortie"] += u.get("output_tokens", 0)
                stats["cout_usd"] = round(cout_usd(model, stats["entree"], stats["sortie"]), 4)
                text = "".join(b.get("text", "") for b in rep.get("content", []))
                data = json.loads(text[text.index("["): text.rindex("]") + 1])
                by_id = {str(d.get("id")): d for d in data if isinstance(d, dict)}
                for a in chunk:
                    d = by_id.get(a["id"])
                    if d:
                        _appliquer(a, d)
                        cache[a["id"]] = {k: a.get(k) for k in CHAMPS}
                stats["lots_ok"] += 1
                break
            except Exception as exc:  # l'IA ne doit jamais bloquer l'envoi
                log.warning("Enrichissement IA, lot %d, tentative %d : %s", i // batch + 1, essai, exc)
                if essai == 1:
                    time.sleep(PAUSE)
                else:
                    stats["lots_echoues"] += 1
    return stats


def ask(prompt, model="claude-haiku-5-5", max_tokens=1500, session=None, timeout=120):
    """Question libre (rapport hebdo). Renvoie '' si pas de clé ou erreur."""
    key = (os.getenv("ANTHROPIC_API_KEY") or "").strip()
    if not key:
        return ""
    http = session or requests
    try:
        r = http.post(API_URL, timeout=timeout, headers={
            "x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
            json={"model": model, "max_tokens": max_tokens, "messages": [{"role": "user", "content": prompt}]})
        r.raise_for_status()
        return "".join(b.get("text", "") for b in r.json().get("content", []))
    except Exception as exc:
        log.warning("Appel IA échoué : %s", exc)
        return ""
