"""Envoi d'e-mails via Resend (gratuit jusqu'à 3 000 e-mails/mois). Mode simulation si pas de clé."""
import logging
import os
from pathlib import Path

import requests

log = logging.getLogger(__name__)
OUTBOX = Path(os.getenv("RADAR_OUTBOX", "out/outbox"))


def send(to, subject, html, sender, reply_to=None, session=None, headers=None, text=None):
    key = (os.getenv("RESEND_API_KEY") or "").strip()
    if not key or os.getenv("RADAR_DRY_RUN") == "1":
        OUTBOX.mkdir(parents=True, exist_ok=True)
        safe = "".join(c if c.isalnum() else "_" for c in f"{to}_{subject}")[:120]
        (OUTBOX / f"{safe}.html").write_text(html, encoding="utf-8")
        log.info("[simulation] e-mail '%s' → %s", subject, to)
        return {"simulated": True}
    http = session or requests
    body = {"from": sender, "to": [to], "subject": subject, "html": html}
    if reply_to:
        body["reply_to"] = reply_to
    if headers:
        body["headers"] = headers
    if text:
        body["text"] = text
    r = http.post("https://api.resend.com/emails", json=body, timeout=30,
                  headers={"Authorization": f"Bearer {key}"})
    if r.status_code >= 400:
        raise RuntimeError(f"Resend a refusé l'envoi ({r.status_code}) : {r.text[:300]}")
    return r.json()


def _api(method, path, **kw):
    key = (os.getenv("RESEND_API_KEY") or "").strip()
    if not key:
        return None
    r = requests.request(method, "https://api.resend.com" + path, timeout=30,
                         headers={"Authorization": f"Bearer {key}"}, **kw)
    if r.status_code >= 400:
        raise RuntimeError(f"Resend {method} {path} : {r.status_code}")
    return r.json()


def activer_suivi_clics(domaine):
    """Active le suivi des clics de Resend sur le domaine d'envoi (idempotent)."""
    for d in (_api("GET", "/domains") or {}).get("data", []):
        if d.get("name") == domaine:
            _api("PATCH", f"/domains/{d['id']}", json={"click_tracking": True})
            return True
    return False


def dernier_evenement(email_id):
    """'delivered', 'opened', 'clicked', 'bounced'… pour un e-mail envoyé."""
    return (_api("GET", f"/emails/{email_id}") or {}).get("last_event")
