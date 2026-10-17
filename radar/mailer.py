"""Envoi d'e-mails via Resend (gratuit jusqu'à 3 000 e-mails/mois). Mode simulation si pas de clé."""
import hashlib
import logging
import os
from datetime import date
from pathlib import Path

import requests

from .reprise import avec_reessais

log = logging.getLogger(__name__)
OUTBOX = Path(os.getenv("RADAR_OUTBOX", "out/outbox"))


def send(to, subject, html, sender, reply_to=None, session=None, headers=None, text=None, attachments=None):
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
    if attachments:
        body["attachments"] = attachments       # [{"filename": ..., "content": base64}]
    # Même clé pour le même e-mail le même jour : si une tentative a abouti sans réponse, Resend n'envoie pas de doublon.
    cle = hashlib.sha256(f"{to}|{subject}|{date.today()}|{html}".encode()).hexdigest()[:48]

    def poster():
        r = http.post("https://api.resend.com/emails", json=body, timeout=30,
                      headers={"Authorization": f"Bearer {key}", "Idempotency-Key": cle})
        if r.status_code >= 400:
            exc = RuntimeError(f"Resend a refusé l'envoi ({r.status_code}) : {r.text[:300]}")
            exc.status_code = r.status_code
            raise exc
        return r.json()
    return avec_reessais(poster, "Resend", pauses=(5, 20))


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


def lire_email(email_id):
    """L'e-mail envoyé tel que Resend le connaît (destinataire, last_event…)."""
    return _api("GET", f"/emails/{email_id}") or {}


def dernier_evenement(email_id):
    """'delivered', 'opened', 'clicked', 'bounced'… pour un e-mail envoyé."""
    return (_api("GET", f"/emails/{email_id}") or {}).get("last_event")
