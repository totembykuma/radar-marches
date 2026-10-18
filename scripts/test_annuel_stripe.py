"""Test réel des abonnements annuels en MODE TEST Stripe (clé sk_test_…, aucun argent réel, rien n'est commité).

Scénarios : liens de paiement annuels (sans essai, texte montant/engagement/renouvellement), passage mensuel → annuel
sans double prélèvement (ancien arrêté + remboursement au prorata), renouvellement au bout de 12 mois au même prix
(horloge de test Stripe), paiement de renouvellement refusé, résiliation en fin de période.
Imprime un résumé court ; code de sortie 1 si un contrôle échoue.
"""
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from radar import annuel, stripe_setup  # noqa: E402
from radar.config import business  # noqa: E402

KEY = os.environ["STRIPE_SECRET_KEY"].strip()
assert KEY.startswith(("sk_test_", "rk_test_")), "clé de TEST Stripe obligatoire"
OK, KO = [], []


def call(method, path, data=None, params=None):
    return annuel._call(method, path, KEY, data=data, params=params)


def check(nom, cond, detail=""):
    (OK if cond else KO).append(nom)
    print(("OK   " if cond else "ÉCHEC ") + nom + (f" ({detail})" if detail and not cond else ""))


def avancer(clock, ts):
    call("POST", f"test_helpers/test_clocks/{clock}/advance", data={"frozen_time": int(ts)})
    for _ in range(90):
        if call("GET", f"test_helpers/test_clocks/{clock}")["status"] == "ready":
            return
        time.sleep(2)
    raise RuntimeError("horloge de test bloquée")


def client(clock, email, carte="pm_card_visa"):
    c = call("POST", "customers", data={"email": email, "test_clock": clock, "payment_method": carte,
                                        "invoice_settings[default_payment_method]": carte})
    return c["id"]


def abonner(cust, price):
    return call("POST", "subscriptions", data={"customer": cust, "items[0][price]": price,
                                               "payment_behavior": "error_if_incomplete"})


def main():
    biz = business()
    # 1. Offres et liens (mode test) : prix annuels, liens sans essai, texte affiché avant paiement
    res = stripe_setup.run()
    biz = business()
    prices = {}
    for k, o in biz["offres"].items():
        prod = stripe_setup._find_product(k)
        allp = call("GET", "prices", params={"product": prod["id"], "active": "true", "limit": 100})["data"]
        prices[k] = {"mois": next(p["id"] for p in allp if p["recurring"]["interval"] == "month" and p["unit_amount"] == int(o["prix_mensuel"] * 100)),
                     "an": next(p["id"] for p in allp if p["recurring"]["interval"] == "year" and p["unit_amount"] == int(o["prix_annuel"] * 100))}
        liens = [pl for pl, ok in annuel.liens_annuels(KEY) if ok == k]
        check(f"{o['nom']} : 1 seul lien annuel actif à {o['prix_annuel']} €", len(liens) == 1
              and (liens[0].get("metadata") or {}).get("radar_price") == prices[k]["an"], f"{len(liens)} liens")
        if liens:
            pl = liens[0]
            check(f"{o['nom']} : lien annuel sans essai", not (pl.get("subscription_data") or {}).get("trial_period_days"))
            txt = ((pl.get("custom_text") or {}).get("submit") or {}).get("message") or ""
            check(f"{o['nom']} : montant, engagement et date affichés", f"{annuel.euros(o['prix_annuel'])} €" in txt
                  and "12 mois" in txt and "Renouvellement automatique le" in txt, txt[:80])
        print(f"     lien annuel {o['nom']} : {res[k].get('lien_annuel')}")
    n = annuel.maj_textes(biz, {})
    check("mise à jour quotidienne du texte (idempotente)", annuel.maj_textes(biz, {}) == 0, f"{n} puis encore")

    t0 = int(datetime.now(timezone.utc).timestamp())
    clock = call("POST", "test_helpers/test_clocks", data={"frozen_time": t0, "name": "radar-annuel"})["id"]
    try:
        email = f"test-annuel-{t0}@example.com"
        # 2. Mensuel Pro payé, puis le même client prend l'annuel Clé en main 10 jours plus tard
        m = abonner(client(clock, email), prices["pro"]["mois"])
        check("mensuel Pro payé 49 €", m["status"] == "active")
        avancer(clock, t0 + 10 * 86400)
        a = abonner(client(clock, email), prices["cle"]["an"])
        inv = call("GET", f"invoices/{a['latest_invoice']}")
        check("annuel Clé en main payé en une fois", a["status"] == "active" and inv["amount_paid"] == int(biz["offres"]["cle"]["prix_annuel"] * 100),
              str(inv["amount_paid"]))
        r = annuel.remplacer_doublons(maintenant=t0 + 10 * 86400)
        mine = [x for x in r["remplaces"] if x["ancien"] == m["id"]]
        check("ancien mensuel arrêté automatiquement", mine and call("GET", f"subscriptions/{m['id']}")["status"] == "canceled", json.dumps(r)[:200])
        check("annuel conservé", call("GET", f"subscriptions/{a['id']}")["status"] == "active")
        rembourse = mine[0]["rembourse"] if mine else 0
        check("part non utilisée du mois remboursée (≈ 2/3 de 49 €)", 30 <= rembourse <= 34, str(rembourse))
        r2 = annuel.remplacer_doublons(maintenant=t0 + 10 * 86400)
        check("2e passage : rien de plus (pas de double remboursement)", not [x for x in r2["remplaces"] if x["ancien"] == m["id"]])

        # 3. Renouvellement au bout de 12 mois, même prix, une seule facture ; puis carte refusée l'année suivante
        fin = annuel.fin_periode(call("GET", f"subscriptions/{a['id']}"))
        avancer(clock, fin + 3600)
        s = call("GET", f"subscriptions/{a['id']}")
        invs = call("GET", "invoices", params={"subscription": a["id"], "limit": 10})["data"]
        check("renouvelé après 12 mois au même prix", s["status"] == "active" and len(invs) == 2
              and invs[0]["amount_paid"] == int(biz["offres"]["cle"]["prix_annuel"] * 100), f"{s['status']} {[i['amount_paid'] for i in invs]}")
        check("nouvelle échéance 1 an plus tard", 364 * 86400 <= annuel.fin_periode(s) - fin <= 366 * 86400)
        refus = call("POST", "payment_methods", data={"type": "card", "card[token]": "tok_chargeCustomerFail"})
        call("POST", f"payment_methods/{refus['id']}/attach", data={"customer": s["customer"]})
        call("POST", f"customers/{s['customer']}", data={"invoice_settings[default_payment_method]": refus["id"]})
        call("POST", f"subscriptions/{a['id']}", data={"default_payment_method": refus["id"]})
        fin2 = annuel.fin_periode(s)
        avancer(clock, fin2 + 3600)
        s = call("GET", f"subscriptions/{a['id']}")
        check("paiement refusé → impayé, service maintenu pendant les relances", s["status"] == "past_due", s["status"])
        sub = {"email": email, "source": "stripe", "periode": annuel.periode(s), "statut": s["status"],
               "renouvellement": annuel.fin_periode(s)}
        check("e-mail « paiement refusé » prévu", [g for _, g, _ in annuel.a_prevenir([sub], set(), fin2 + 3600)] == ["echec"])

        # 4. Résiliation : effet à la fin de la période payée, aucun prélèvement ensuite
        e2 = f"test-resil-{t0}@example.com"
        b = abonner(client(clock, e2), prices["essentiel"]["an"])
        call("POST", f"subscriptions/{b['id']}", data={"cancel_at_period_end": "true"})
        sub = {"email": e2, "source": "stripe", "periode": "an", "statut": "active", "resilie": True,
               "renouvellement": annuel.fin_periode(b)}
        check("pas de rappel de renouvellement après résiliation", annuel.a_prevenir([sub], set(), annuel.fin_periode(b) - 10 * 86400) == [])
        avancer(clock, annuel.fin_periode(b) + 3600)
        invs = call("GET", "invoices", params={"subscription": b["id"], "limit": 10})["data"]
        check("résilié à la date anniversaire, sans nouveau prélèvement",
              call("GET", f"subscriptions/{b['id']}")["status"] == "canceled" and len(invs) == 1, str(len(invs)))
    finally:
        call("DELETE", f"test_helpers/test_clocks/{clock}")
    print(f"Résumé : {len(OK)} contrôles OK, {len(KO)} en échec" + (f" : {', '.join(KO)}" if KO else ""))
    sys.exit(1 if KO else 0)


if __name__ == "__main__":
    main()
