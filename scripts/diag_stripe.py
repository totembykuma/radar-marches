"""Diagnostic : derniers passages en caisse Stripe (e-mails masqués) et réglage « après paiement » des liens."""
import os, time, requests
K = os.environ["STRIPE_SECRET_KEY"]; S = "https://api.stripe.com/v1"
def get(p, **q):
    r = requests.get(f"{S}/{p}", params=q, auth=(K, ""), timeout=60)
    if r.status_code != 200:
        print(p, "ERREUR", r.status_code, r.json().get("error", {}).get("message", "")[:120]); return {"data": []}
    return r.json()
m = lambda e: (e or "?")[:2] + "***@" + (e or "@").split("@")[-1]
for pl in get("payment_links", active="true", limit=20)["data"]:
    ac = pl.get("after_completion") or {}
    print("LIEN", pl["url"][-6:], (pl.get("metadata") or {}).get("radar_offre"), ac.get("type"), (ac.get("redirect") or {}).get("url"))
depuis = int(time.time()) - 4 * 86400
for cs in get("checkout/sessions", limit=30, **{"created[gte]": depuis})["data"]:
    print("CAISSE", time.strftime("%d/%m %H:%M", time.gmtime(cs["created"])), cs["status"], cs["payment_status"],
          m((cs.get("customer_details") or {}).get("email") or cs.get("customer_email")), "abo:", bool(cs.get("subscription")))
for si in get("setup_intents", limit=10, **{"created[gte]": depuis})["data"]:
    e = si.get("last_setup_error") or {}
    print("CARTE", time.strftime("%d/%m %H:%M", time.gmtime(si["created"])), si["status"], e.get("code"), e.get("decline_code"))
for s in get("subscriptions", status="all", limit=10, **{"created[gte]": depuis})["data"]:
    print("ABO", time.strftime("%d/%m %H:%M", time.gmtime(s["created"])), s["status"])
for ev in get("events", limit=15, **{"created[gte]": depuis})["data"]:
    print("EVT", time.strftime("%d/%m %H:%M", time.gmtime(ev["created"])), ev["type"])
