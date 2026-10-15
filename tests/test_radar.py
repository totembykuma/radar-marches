import json
from datetime import date
from pathlib import Path

from radar import boamp, finance, subscribers
from radar.config import business, metiers
from radar.matching import Matcher, parse_departements

FIX = json.loads((Path(__file__).parent / "fixtures" / "boamp_sample.json").read_text(encoding="utf-8"))
AVIS = [boamp.normalize(r) for r in FIX]
M = Matcher(metiers())


def by_id(i):
    return next(a for a in AVIS if a["id"] == f"26-{i}")


def test_normalize_fields():
    a = by_id(101001)
    assert a["objet"].startswith("Nettoyage") and a["departements"] == ["33"]
    assert a["date_limite"].startswith("2026-11-06") and "durée de 12 mois" in a["texte"]


def test_metiers_and_exclusions():
    assert M.metiers_of(by_id(101001)) == ["nettoyage"]
    assert "nettoyage" not in M.metiers_of(by_id(101002))          # voirie exclue
    assert "peinture" not in M.metiers_of(by_id(101004))           # peinture routière exclue
    assert "peinture" in M.metiers_of(by_id(101008))
    assert "electricite" not in M.metiers_of(by_id(101007))        # fourniture d'électricité exclue
    assert "plomberie-cvc" in M.metiers_of(by_id(101010))


def test_subscriber_filter():
    sub = {"metiers": ["nettoyage"], "departements": ["47"], "mots_cles": []}
    ids = [a["id"] for a in M.for_subscriber(AVIS, sub)]
    assert ids == ["26-101009"]                                     # 101006 = résultat de marché, ignoré
    pro = {"metiers": ["espaces-verts"], "departements": [], "mots_cles": ["aire de jeux"]}
    assert {a["id"] for a in M.for_subscriber(AVIS, pro)} == {"26-101003", "26-101005"}


def test_parse_departements():
    assert parse_departements("33, 47 et 2a") == ["33", "47", "2A"]
    assert parse_departements("Toute la France") == []
    assert parse_departements("971") == ["971"]


def test_bilan():
    fis = business()["fiscalite"]
    b = finance.bilan(1000, 0, 30, 10, fis)
    assert b["benefice_apres_depenses"] == 960
    assert b["cotisations_et_impot"] == 256.0 and b["revenu_net"] == 704.0


class FakeStripe:
    def __init__(self, pages):
        self.pages = pages

    def get(self, url, params=None, auth=None, timeout=None):
        key = url.rsplit("/v1/", 1)[1]

        class R:
            def __init__(s, body): s.body = body
            def raise_for_status(s): pass
            def json(s): return s.body
        return R({"data": self.pages[key], "has_more": False})


def test_subscribers_from_stripe(monkeypatch):
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_x")
    biz = business()
    biz["offres"]["pro"]["stripe_price_ids"] = ["price_pro"]
    fake = FakeStripe({
        "subscriptions": [
            {"id": "sub_1", "status": "trialing", "items": {"data": [{"price": {"id": "price_ess"}}]}},
            {"id": "sub_2", "status": "active", "items": {"data": [{"price": {"id": "price_pro"}}]},
             "metadata": {"departements": "64"}},
            {"id": "sub_3", "status": "canceled", "items": {"data": [{"price": {"id": "price_ess"}}]}},
        ],
        "checkout/sessions": [
            {"subscription": "sub_1", "customer_details": {"email": "a@x.fr"}, "custom_fields": [
                {"key": "metier", "dropdown": {"value": "nettoyage"}}, {"key": "departements", "text": {"value": "33, 47, 24, 40"}}]},
            {"subscription": "sub_2", "customer_details": {"email": "b@x.fr"}, "custom_fields": [
                {"key": "metier", "dropdown": {"value": "espacesverts"}}, {"key": "departements", "text": {"value": "France"}},
                {"key": "motscles", "text": {"value": "aire de jeux, city stade"}}]},
            {"subscription": "sub_3", "customer_details": {"email": "c@x.fr"}, "custom_fields": []},
        ]})
    subs = subscribers.from_stripe(biz, metiers(), session=fake)
    assert [s["email"] for s in subs] == ["a@x.fr", "b@x.fr"]
    assert subs[0]["departements"] == ["33", "47", "24"]            # 3 premiers saisis (Essentiel)
    assert subs[1]["plan"] == "pro" and subs[1]["metiers"] == ["espaces-verts"] and subs[1]["mots_cles"] == ["aire de jeux", "city stade"]
    assert subs[1]["departements"] == ["64"]                       # critère modifié par le support


def test_demo_end_to_end(tmp_path, monkeypatch):
    monkeypatch.setenv("RADAR_OUTBOX", str(tmp_path / "outbox"))
    import radar.cli as cli
    import radar.mailer as mailer
    monkeypatch.setattr(mailer, "OUTBOX", tmp_path / "outbox")
    monkeypatch.setattr(cli, "OUT", tmp_path)
    run = cli.run_demo()
    assert run["emails_envoyes"] == 2 and not run["erreurs"] or run["erreurs"] == []
    mails = sorted(m.name for m in (tmp_path / "outbox").glob("*.html"))
    assert len(mails) == 4 and sum("Bienvenue" in m for m in mails) == 2   # 2 bienvenues + 2 alertes
    assert (tmp_path / "site" / "appels-offres" / "nettoyage" / "33.html").exists()
    # pas de doublon au 2e passage
    run2 = cli.run_demo()
    assert run2["emails_envoyes"] == 0 and "bienvenues" not in run2
