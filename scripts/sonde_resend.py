"""Sonde : réception Resend + droit d'écriture Stripe. Imprime seulement des compteurs et codes HTTP."""
import os
import time
import requests

rk, sk, stripe = (os.environ.get(k, "").strip() for k in ("RESEND_INBOUND_KEY", "RESEND_API_KEY", "STRIPE_SECRET_KEY"))
r = requests.post("https://api.resend.com/emails", headers={"Authorization": f"Bearer {sk}"}, timeout=30,
                  json={"from": "Radar Marchés <alertes@radar-marches.fr>", "to": ["criteres@elkaurtel.resend.app"],
                        "subject": "sonde reception", "text": "Métier : Électricité\nDépartements : 33\n"})
print("envoi test", r.status_code)
n = 0
for _ in range(12):
    time.sleep(10)
    r = requests.get("https://api.resend.com/emails/receiving?limit=10", headers={"Authorization": f"Bearer {rk}"}, timeout=30)
    data = (r.json() or {}).get("data") or [] if r.ok else []
    n = sum(1 for e in data if e.get("subject") == "sonde reception")
    if not r.ok or n:
        break
print("liste reçus", r.status_code, "sonde trouvée", n, (r.json().get("message") or "")[:100] if not r.ok else "")
if n:
    e = next(e for e in data if e.get("subject") == "sonde reception")
    d = requests.get(f"https://api.resend.com/emails/receiving/{e['id']}", headers={"Authorization": f"Bearer {rk}"}, timeout=30)
    print("lecture corps", d.status_code, "texte lu", "Électricité" in (d.json().get("text") or ""))
lst = requests.get("https://api.stripe.com/v1/subscriptions", params={"limit": 1, "status": "all"}, auth=(stripe, ""), timeout=30)
print("lecture Stripe", lst.status_code, (lst.json().get("error") or {}).get("message", "")[:150])
subs = lst.json().get("data") or [{"id": "sub_inexistant_sonde"}]   # 404 = droit OK, 403 = droit manquant
if subs:
    w = requests.post(f"https://api.stripe.com/v1/subscriptions/{subs[0]['id']}", data={"metadata[radar_sonde]": ""}, auth=(stripe, ""), timeout=30)
    print("écriture Stripe", w.status_code, (w.json().get("error") or {}).get("message", "")[:150])
