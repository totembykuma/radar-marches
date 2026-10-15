"""Envoi d'e-mails via Resend (gratuit jusqu'à 3 000 e-mails/mois). Mode simulation si pas de clé."""
import logging
import os
from pathlib import Path

import requests

log = logging.getLogger(__name__)
OUTBOX = Path(os.getenv("RADAR_OUTBOX", "out/outbox"))


def send(to, subject, html, sender, reply_to=None, session=None):
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
    r = http.post("https://api.resend.com/emails", json=body, timeout=30,
                  headers={"Authorization": f"Bearer {key}"})
    if r.status_code >= 400:
        raise RuntimeError(f"Resend a refusé l'envoi ({r.status_code}) : {r.text[:300]}")
    return r.json()
