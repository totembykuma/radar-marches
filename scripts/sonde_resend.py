"""Sonde : Resend peut-il recevoir des e-mails ? N'affiche aucune adresse ni contenu, seulement des compteurs."""
import os
import requests

k = os.environ["RESEND_API_KEY"].strip()
h = {"Authorization": f"Bearer {k}"}
for path in ("domains", "emails/receiving?limit=5"):
    r = requests.get(f"https://api.resend.com/{path}", headers=h, timeout=30)
    body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    data = body.get("data") or []
    print(path, r.status_code, "n=", len(data), "msg=", (body.get("message") or "")[:120])
    if path == "domains":
        for d in data:
            print("  domaine:", d.get("name"), d.get("status"), "receiving=", (d.get("capabilities") or {}).get("receiving"), "keys=", sorted(d)[:12])
