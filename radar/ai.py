"""Résumés et notes de pertinence générés par Claude (API Anthropic). Facultatif : sans clé, on garde l'objet brut."""
import json
import logging
import os

import requests

log = logging.getLogger(__name__)
API_URL = "https://api.anthropic.com/v1/messages"

PROMPT = """Tu aides des TPE/PME françaises à repérer les marchés publics qui valent la peine d'y répondre.
Pour chaque avis ci-dessous, rédige :
- "resume" : une phrase claire (max 30 mots) qui dit concrètement ce que l'acheteur veut, où, et toute info utile (durée, lots, montant si cité).
- "score" : de 1 à 5, accessibilité pour une petite entreprise du métier (5 = petit marché local simple ; 1 = gros marché national complexe ou hors métier).
Réponds uniquement par un tableau JSON : [{{"id": "...", "resume": "...", "score": 3}}, ...]

Avis :
{avis}"""


def enrich(avis_list, model="claude-haiku-5-5", batch=25, session=None):
    key = os.getenv("ANTHROPIC_API_KEY")
    if not key or not avis_list:
        return avis_list
    http = session or requests
    for i in range(0, len(avis_list), batch):
        chunk = avis_list[i:i + batch]
        payload = "\n".join(
            json.dumps({"id": a["id"], "objet": a["objet"], "acheteur": a["acheteur"],
                        "departements": a["departements"], "details": a.get("texte", "")[:600]}, ensure_ascii=False)
            for a in chunk)
        try:
            r = http.post(API_URL, timeout=120, headers={
                "x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
                json={"model": model, "max_tokens": 4000,
                      "messages": [{"role": "user", "content": PROMPT.format(avis=payload)}]})
            r.raise_for_status()
            text = "".join(b.get("text", "") for b in r.json().get("content", []))
            data = json.loads(text[text.index("["): text.rindex("]") + 1])
            by_id = {str(d.get("id")): d for d in data}
            for a in chunk:
                d = by_id.get(a["id"])
                if d:
                    a["resume"] = str(d.get("resume", ""))[:300]
                    try:
                        a["score"] = max(1, min(5, int(d.get("score", 0))))
                    except (TypeError, ValueError):
                        pass
        except Exception as exc:  # l'IA ne doit jamais bloquer l'envoi
            log.warning("Enrichissement IA ignoré pour un lot : %s", exc)
    return avis_list


def ask(prompt, model="claude-haiku-5-5", max_tokens=1500, session=None):
    """Question libre (rapport hebdo). Renvoie '' si pas de clé ou erreur."""
    key = os.getenv("ANTHROPIC_API_KEY")
    if not key:
        return ""
    http = session or requests
    try:
        r = http.post(API_URL, timeout=120, headers={
            "x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
            json={"model": model, "max_tokens": max_tokens, "messages": [{"role": "user", "content": prompt}]})
        r.raise_for_status()
        return "".join(b.get("text", "") for b in r.json().get("content", []))
    except Exception as exc:
        log.warning("Appel IA échoué : %s", exc)
        return ""
