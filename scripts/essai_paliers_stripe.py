"""Essai réel en mode TEST Stripe (horloges de test) des paliers de prix (radar/paliers.py) et de l'essai unique (radar/anti_abus.py).

Lancé par le workflow essai-paliers.yml avec le secret STRIPE_TEST_SECRET_KEY (sk_test_…). Ne touche jamais au mode réel.
Crée ses propres produit, prix et clients de test, avance le temps mois par mois, lit les factures émises par Stripe,
puis supprime tout. Imprime un résumé court : une ligne OK/ÉCHEC par vérification.
"""
import copy
import os
import secrets
import sys
import time

KEY = (os.getenv("STRIPE_TEST_SECRET_KEY") or "").strip()
if not KEY.startswith(("sk_test_", "rk_test_")):
    sys.exit("STRIPE_TEST_SECRET_KEY absent ou pas en mode test : rien n'est lancé.")
os.environ["STRIPE_SECRET_KEY"] = KEY
os.environ["RADAR_DRY_RUN"] = "1"           # aucun e-mail réel
os.environ.pop("RESEND_API_KEY", None)

from radar import anti_abus, paliers, stripe_setup  # noqa: E402
from radar.config import business, metiers as load_metiers  # noqa: E402
from radar.stripe_setup import _call  # noqa: E402

RES = []


def verif(nom, ok, detail=""):
    RES.append(ok)
    print(("OK     " if ok else "ÉCHEC  ") + nom + (f" ({detail})" if detail and not ok else ""))


def horloge(t):
    return _call("POST", "test_helpers/test_clocks", data={"frozen_time": int(t), "name": "radar-paliers"})


def avancer(clock, t):
    _call("POST", f"test_helpers/test_clocks/{clock['id']}/advance", data={"frozen_time": int(t)})
    for _ in range(120):
        c = _call("GET", f"test_helpers/test_clocks/{clock['id']}")
        if c["status"] == "ready":
            return c
        time.sleep(3)
    raise RuntimeError("horloge de test bloquée")


def client(clock, email, carte):
    c = _call("POST", "customers", data={"email": email, "test_clock": clock["id"], "payment_method": carte,
                                         "invoice_settings[default_payment_method]": carte})
    pm = c["invoice_settings"]["default_payment_method"]
    return c, pm


def abonner(cus, pm, price, essai=7):
    data = {"customer": cus["id"], "items[0][price]": price, "default_payment_method": pm}
    if essai:
        data["trial_period_days"] = essai
    return _call("POST", "subscriptions", data=data)


def sub(sid):
    return _call("GET", f"subscriptions/{sid}")


def factures(sid):
    inv = _call("GET", "invoices", params={"subscription": sid, "limit": 100})["data"]
    return [i for i in sorted(inv, key=lambda i: i["created"]) if i["total"] > 0]


def payees(sid):
    return [i["amount_paid"] for i in factures(sid) if i["status"] == "paid"]


def run_traiter(biz, clock, attendu_erreurs=0):
    """Même enchaînement que bienvenue.yml : essai unique (anti_abus), puis durée d'essai et paliers."""
    a = anti_abus.appliquer(biz)
    r = paliers.traiter(biz, maintenant=_call("GET", f"test_helpers/test_clocks/{clock['id']}")["frozen_time"])
    r["essais_refuses"] = a["essais_refuses"]
    r["erreurs"] += a["erreurs"]
    if len(r["erreurs"]) != attendu_erreurs:
        print("  erreurs :", r["erreurs"][:3])
    return r


def main():
    biz = business()
    tag = secrets.token_hex(3)
    prod = _call("POST", "products", data={"name": f"Essai paliers {tag}", "metadata[radar_offre]": f"essai-{tag}"})
    offre = copy.deepcopy(biz["offres"]["essentiel"])
    ids = [stripe_setup._price_for(prod, paliers.centimes(p))["id"] for _, p in paliers.grille(offre)]
    offre.update(stripe_paliers=ids, stripe_price_ids=ids)
    biz["offres"] = {"essentiel": offre}
    clocks = []
    try:
        # 0. Page de paiement : essai ramené à 7 jours et grille affichée
        b14 = dict(biz, essai_gratuit_jours=14)
        price0 = _call("GET", f"prices/{ids[0]}")
        lien = stripe_setup._link_for(f"essai-{tag}", price0, b14, load_metiers(), "test", offre)
        lien = stripe_setup._link_for(f"essai-{tag}", price0, biz, load_metiers(), "test", offre)
        lien = _call("GET", f"payment_links/{lien['id']}")
        verif("lien de paiement : essai 7 jours", (lien.get("subscription_data") or {}).get("trial_period_days") == 7)
        verif("lien de paiement : grille et essai unique affichés",
              "une seule fois par client" in lien["custom_text"]["submit"]["message"] and "19,99 €" in lien["custom_text"]["submit"]["message"])
        _call("POST", f"payment_links/{lien['id']}", data={"active": "false"})

        # A. Un client sur 7 mois : changement de carte, résiliation annulée, remboursement ; puis réabonnement
        t0 = int(time.time())
        ca = horloge(t0)
        clocks.append(ca)
        biz["nouvelle_grille_depuis"] = time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(t0 - 60))
        email = f"radar.paliers.{tag}@example.com"
        c1, pm1 = client(ca, email, "pm_card_visa_debit")
        s1 = abonner(c1, pm1, ids[0])
        run_traiter(biz, ca)
        s = sub(s1["id"])
        verif("essai de 7 jours pour un nouveau client", s["status"] == "trialing" and s["trial_end"] - s["trial_start"] == 7 * 86400)
        echeances = []
        for mois in range(1, 8):
            s = sub(s1["id"])
            fin = paliers._fin_periode(s)
            echeances.append(fin)
            ca = avancer(ca, fin + 7200)
            if mois == 3:   # remboursement du 3e mois : ne change pas l'ancienneté
                ch = _call("GET", "charges", params={"customer": c1["id"], "limit": 1})["data"]
                rf = _call("POST", "refunds", data={"charge": ch[0]["id"]}) if ch and ch[0]["amount"] == 1999 else {}
                verif("remboursement du 3e mois accepté", rf.get("status") in ("succeeded", "pending"), rf.get("status"))
            if mois == 4:   # nouvelle carte
                pm = _call("POST", "payment_methods/pm_card_amex/attach", data={"customer": c1["id"]})["id"]
                _call("POST", f"subscriptions/{s1['id']}", data={"default_payment_method": pm})
            if mois == 5:   # résiliation programmée puis annulée (« je reste »)
                _call("POST", f"subscriptions/{s1['id']}", data={"cancel_at_period_end": "true"})
                _call("POST", f"subscriptions/{s1['id']}", data={"cancel_at_period_end": "false"})
            r = run_traiter(biz, ca)
            r2 = run_traiter(biz, ca)
            if r2["paliers"] or r2["essais_refuses"]:
                verif(f"rien en double au mois {mois}", False)
        p = payees(s1["id"])
        verif("factures 29 / 29 / 19,99 x3 / 9,99 x2", p == [2900, 2900, 1999, 1999, 1999, 999, 999], p)
        f = factures(s1["id"])
        dates = [i["lines"]["data"][0]["period"]["start"] for i in f]
        verif("une facture par mois, à la date anniversaire", len(f) == 7 and
              all(abs(d - e) < 60 for d, e in zip(dates, echeances)), [round((d - e) / 3600) for d, e in zip(dates, echeances)])
        _call("DELETE", f"subscriptions/{s1['id']}")
        c2, pm2 = client(ca, email.upper(), "pm_card_mastercard")          # même personne, autre carte
        s2 = abonner(c2, pm2, ids[0])
        c3, pm3 = client(ca, f"autre.{tag}@example.com", "pm_card_visa_debit")   # autre e-mail, même carte
        s3 = abonner(c3, pm3, ids[0])
        r = run_traiter(biz, ca)
        run_traiter(biz, ca)
        verif("2 essais refusés", r["essais_refuses"] == 2, r)
        ca = avancer(ca, ca["frozen_time"] + 7200)
        for nom, sid in (("même e-mail", s2["id"]), ("même carte", s3["id"])):
            s = sub(sid)
            verif(f"réabonnement ({nom}) : pas de 2e essai, 29 € prélevés une seule fois",
                  s["status"] == "active" and payees(sid) == [2900], (s["status"], payees(sid)))
        ca = avancer(ca, paliers._fin_periode(sub(s2["id"])) + 7200)
        run_traiter(biz, ca)
        ca = avancer(ca, paliers._fin_periode(sub(s2["id"])) + 7200)
        verif("réabonnement : ancienneté repartie de zéro (29, 29, 19,99)", payees(s2["id"]) == [2900, 2900, 1999], payees(s2["id"]))

        # B. Paiement refusé, puis réglé ; essai trop long raccourci
        t0 = int(time.time())
        cb = horloge(t0)
        clocks.append(cb)
        c4, pm4 = client(cb, f"refus.{tag}@example.com", "pm_card_chargeCustomerFail")
        s4 = abonner(c4, pm4, ids[0])
        c5, pm5 = client(cb, f"long.{tag}@example.com", "pm_card_discover")
        s5 = abonner(c5, pm5, ids[0], essai=14)
        run_traiter(biz, cb)
        s = sub(s5["id"])
        verif("essai de 14 jours ramené à 7", s["trial_end"] - s["trial_start"] == 7 * 86400, s["trial_end"] - s["trial_start"])
        cb = avancer(cb, paliers._fin_periode(sub(s4["id"])) + 7200)
        r = run_traiter(biz, cb)
        s = sub(s4["id"])
        verif("paiement refusé : abonnement impayé, aucune erreur", s["status"] in ("past_due", "unpaid") and not r["erreurs"], s["status"])
        pm = _call("POST", "payment_methods/pm_card_jcb/attach", data={"customer": c4["id"]})["id"]
        _call("POST", f"subscriptions/{s4['id']}", data={"default_payment_method": pm})
        ouverte = [i for i in factures(s4["id"]) if i["status"] == "open"][0]
        _call("POST", f"invoices/{ouverte['id']}/pay", data={"payment_method": pm})
        for _ in range(2):
            cb = avancer(cb, paliers._fin_periode(sub(s4["id"])) + 7200)
            run_traiter(biz, cb)
        verif("après régularisation : 29, 29, 19,99 sans double prélèvement", payees(s4["id"]) == [2900, 2900, 1999], payees(s4["id"]))

        # C. Client déjà en essai avant la nouvelle grille (choix du propriétaire) : garde ses 14 jours, puis la grille
        t0 = int(time.time())
        cc = horloge(t0)
        clocks.append(cc)
        c6, pm6 = client(cc, f"ancien.{tag}@example.com", "pm_card_unionpay")
        s6 = abonner(c6, pm6, ids[0], essai=14)
        bc = dict(biz, nouvelle_grille_depuis=time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(t0 + 3600)), nouvelle_grille_anciens=True)
        for _ in range(3):
            run_traiter(bc, cc)
            cc = avancer(cc, paliers._fin_periode(sub(s6["id"])) + 7200)
        s = sub(s6["id"])
        verif("ancien client : essai de 14 jours gardé", s["trial_end"] - s["trial_start"] == 14 * 86400)
        verif("ancien client : 29, 29, 19,99", payees(s6["id"]) == [2900, 2900, 1999], payees(s6["id"]))
    finally:
        for c in clocks:
            try:
                _call("DELETE", f"test_helpers/test_clocks/{c['id']}")
            except RuntimeError:
                pass
        for pid in ids:
            _call("POST", f"prices/{pid}", data={"active": "false"})
        _call("POST", f"products/{prod['id']}", data={"active": "false"})
    print(f"Résumé : {sum(RES)}/{len(RES)} vérifications réussies")
    sys.exit(0 if all(RES) else 1)


if __name__ == "__main__":
    main()
