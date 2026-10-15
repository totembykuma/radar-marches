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
    assert subs[0]["departements"] == ["33"]                        # 1er saisi seulement (Essentiel : 1 département)
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
    assert len(mails) == 5 and sum("Bienvenue" in m for m in mails) == 2   # 2 bienvenues + 2 alertes + 1 rappel d'essai
    assert run["rappels_essai"] == 1 and sum("essai" in m for m in mails) == 1
    assert (tmp_path / "site" / "criteres" / "index.html").exists()
    assert (tmp_path / "site" / "appels-offres" / "nettoyage" / "33.html").exists()
    assert (tmp_path / "site" / "rappel" / "index.html").exists()
    g = (tmp_path / "site" / "guides" / "mapa-procedure-adaptee.html").read_text()
    assert "Accessible PME" in g and "{{" not in g
    alerte = next(m for m in (tmp_path / "outbox").glob("*.html") if "Bienvenue" not in m.name).read_text()
    assert "/rappel/?o=" in alerte
    # pas de doublon au 2e passage
    run2 = cli.run_demo()
    assert run2["emails_envoyes"] == 0 and "bienvenues" not in run2 and "rappels_essai" not in run2


def test_stripe_setup_writes_config(tmp_path, monkeypatch):
    import shutil
    from radar import stripe_setup, config
    (tmp_path / "config").mkdir()
    for f in ("business.yaml", "metiers.yaml"):
        shutil.copy(config.ROOT / "config" / f, tmp_path / "config" / f)
    monkeypatch.setattr(config, "ROOT", tmp_path)
    monkeypatch.setattr(stripe_setup, "ROOT", tmp_path)
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_x")
    calls = []

    def fake(method, path, data=None, params=None):
        calls.append((method, path, data))
        if method == "GET":
            return {"data": []}
        if path == "products":
            return {"id": f"prod_{data['metadata[radar_offre]']}"}
        if path == "prices":
            return {"id": f"price_{data['unit_amount']}", "unit_amount": data["unit_amount"],
                    "recurring": {"interval": data["recurring[interval]"]}}
        if path == "payment_links":
            assert data["custom_fields[0][dropdown][options][1][value]"] == "espacesverts"
            assert data["subscription_data[trial_period_days]"] == 14
            return {"url": f"https://buy.stripe.com/test_{data['metadata[radar_offre]']}_{data['line_items[0][price]']}"}
        if path == "billing_portal/configurations":
            return {"login_page": {"url": "https://billing.stripe.com/p/login/test_x"}}
    monkeypatch.setattr(stripe_setup, "_call", fake)
    stripe_setup.run()
    biz = config.business()
    assert biz["offres"]["pro"]["lien_paiement"] == "https://buy.stripe.com/test_pro_price_4900"
    assert biz["offres"]["pro"]["lien_annuel"] == "https://buy.stripe.com/test_pro_price_49000"
    assert biz["offres"]["essentiel"]["stripe_price_ids"] == ["price_2900", "price_29000"]
    assert biz["stripe_mode"] == "test" and biz["portail_client"].endswith("test_x")
    stripe_setup.run()  # relancer ne casse pas le fichier
    assert config.business()["offres"]["pro"]["stripe_price_ids"] == ["price_4900", "price_49000"]


def test_secrets_never_logged(monkeypatch):
    from radar.cli import safe
    monkeypatch.setenv("RESEND_API_KEY", " re_abcdef123456\n")
    assert "re_abcdef123456" not in safe("Invalid header value: 'Bearer re_abcdef123456'")


def _award(donnees, objet="Nettoyage des locaux de la mairie", dep="91"):
    return {"idweb": "26-9", "objet": objet, "nomacheteur": "Ville X", "code_departement": [dep],
            "dateparution": "2026-10-14", "nature_libelle": "Résultat de marché", "donnees": json.dumps(donnees)}


def test_prospects_winners_and_dedupe():
    from radar import prospects
    org = lambda i, mail, cp: {"efac:Company": {"cac:PartyIdentification": {"cbc:ID": {"#text": i}},
                                                 "cac:PartyName": {"cbc:Name": "Soc " + i},
                                                 "cac:Contact": {"cbc:ElectronicMail": mail},
                                                 "cac:PostalAddress": {"cbc:PostalZone": cp}}}
    eforms = {"EFORMS": {"ContractAwardNotice": {"ext": {"efac:Organizations": {"efac:Organization": [
        org("ORG-1", "achats@mairie-x.fr", "91000"), org("ORG-2", "Contact@Propre-Net.fr", "91310"),
        org("ORG-3", "jean@propre-net.fr", "75001")]},
        "efac:NoticeResult": {"efac:TenderingParty": [{"efac:Tenderer": [{"cbc:ID": "ORG-2"}, {"cbc:ID": "ORG-3"}]}]}}}}}
    mapa = {"MAPA": {"attribution": {"titulaire": {"denomination": "Clean SARL", "coord": {"mel": "info@clean.fr", "cp": "33000"}}}}}
    recs = [_award(eforms), _award(mapa), _award(mapa, objet="Fourniture de carburant")]
    assert [w["email"] for w in prospects.winners(recs[0])] == ["contact@propre-net.fr", "jean@propre-net.fr"]
    c = prospects.candidates(recs, M, set())
    assert [x["email"] for x in c] == ["contact@propre-net.fr", "info@clean.fr"]   # 1 par domaine, métier requis
    assert c[0]["dep"] == "91" and c[0]["metier"] == "nettoyage" and c[1]["dep"] == "33"
    done = {prospects.h("dom:clean.fr")}
    assert [x["email"] for x in prospects.candidates(recs, M, done, ["contact@propre-net.fr"])] == []


def test_bilan_resultats_pro(tmp_path, monkeypatch):
    from radar import cli, prospects
    monkeypatch.setenv("RADAR_DRY_RUN", "1")
    monkeypatch.setattr("radar.mailer.OUTBOX", tmp_path)
    org = {"efac:Company": {"cac:PartyIdentification": {"cbc:ID": "ORG-2"}, "cac:PartyName": {"cbc:Name": "Propre Net"},
                            "cac:Contact": {"cbc:ElectronicMail": "c@propre-net.fr"}}}
    don = {"EFORMS": {"x": {"efac:Organization": [org], "efac:Tenderer": {"cbc:ID": "ORG-2"},
                            "efac:LotTender": [{"cac:LegalMonetaryTotal": {"cbc:PayableAmount": {"#text": "45000.50", "@currencyID": "EUR"}}}]}}}
    rec = _award(don)
    assert prospects.titulaires(rec) == ["Propre Net"] and prospects.montant(rec) == 45000
    subs = [{"email": "pro@example.com", "plan": "pro", "metiers": ["nettoyage"], "departements": ["91"], "mots_cles": []},
            {"email": "ess@example.com", "plan": "essentiel", "metiers": ["nettoyage"], "departements": ["91"], "mots_cles": []},
            {"email": "pro2@example.com", "plan": "pro", "metiers": ["nettoyage"], "departements": ["33"], "mots_cles": []}]
    n = cli.envoyer_resultats(subs, business(), metiers(), M, {}, records=[rec])
    assert n == 1
    html = next(tmp_path.iterdir()).read_text(encoding="utf-8")
    assert "Propre Net" in html and "45 000 € HT" in html


def test_intrus_ecartes():
    def av(objet, desc=(), texte=""):
        return {"objet": objet, "descripteurs": list(desc), "texte": texte}
    assert "nettoyage" not in M.metiers_of(av("Prestations de balayage de la voirie", ["Nettoyage urbain"]))
    assert "nettoyage" in M.metiers_of(av("Entretien des locaux de la mairie", ["Nettoyage de locaux"]))
    assert "communication-web" not in M.metiers_of(av("Location de copieurs", ["Matériel d'imprimerie"]))
    assert "communication-web" not in M.metiers_of(av("Maintenance du système de vidéoprotection", ["Audiovisuel"]))
    assert "communication-web" in M.metiers_of(av("Refonte du site internet de la ville"))
    assert "informatique" not in M.metiers_of(av("Contrat d'assurances", texte="flotte informatique"))
    assert "electricite" not in M.metiers_of(av("Aménagement d'un parking", texte="éclairage et électricité"))


def test_siret_acheteur():
    fn = {"FNSimple": {"organisme": {"codeIdentificationNational": "217 703 073 00015"}}}
    assert boamp._siret_acheteur(json.dumps(fn)) == "21770307300015"
    ef = {"EFORMS": {"ContractNotice": {
        "cac:ContractingParty": {"cac:Party": {"cac:PartyIdentification": {"cbc:ID": "ORG-0002"}}},
        "ext:UBLExtensions": {"ext:UBLExtension": {"ext:ExtensionContent": {"efext:EformsExtension": {
            "efac:Organizations": {"efac:Organization": [
                {"efac:Company": {"cac:PartyIdentification": {"cbc:ID": "ORG-0001"}, "cac:PartyLegalEntity": {"cbc:CompanyID": "11111111111111"}}},
                {"efac:Company": {"cac:PartyIdentification": {"cbc:ID": "ORG-0002"}, "cac:PartyLegalEntity": {"cbc:CompanyID": "22222222222222"}}},
            ]}}}}}}}}
    assert boamp._siret_acheteur(json.dumps(ef)) == "22222222222222"
    assert boamp._siret_acheteur("{}") == ""


def test_fiche_avant_de_repondre(tmp_path, monkeypatch):
    from radar import historique, render

    class R:
        def __init__(self, j):
            self.j, self.ok, self.status_code = j, True, 200

        def json(self):
            return self.j

        def raise_for_status(self):
            pass

    class S:
        def __init__(self):
            self.where = None

        def get(self, url, params=None, timeout=None):
            if "recherche-entreprises" in url:
                return R({"results": [{"nom_raison_sociale": "PROPRE ET NET"}]})
            self.where = params["where"]
            rows = [{"id": "A", "objet": "Nettoyage des écoles", "montant": 55000.0, "offresrecues": "12",
                     "titulaire_id_1": "84265429500018", "datenotification": "2026-03-28"},
                    {"id": "A", "objet": "Nettoyage des écoles", "montant": 55000.0, "offresrecues": "12",
                     "titulaire_id_1": "84265429500018", "datenotification": "2026-03-28"},
                    {"id": "B", "objet": "Vitrerie", "montant": 15000.0, "offresrecues": "3",
                     "titulaire_id_1": "12345678900011", "datenotification": "2025-06-01"}]
            return R({"results": rows})

    monkeypatch.setattr(historique, "NOMS", tmp_path / "noms.json")
    s = S()
    h = historique.Historique(metiers(), session=s)
    f = h.fiche("21770307300015", "nettoyage")
    assert 'startswith(codecpv, "9091")' in s.where and "21770307300015" in s.where
    assert f["n"] == 2 and f["offres_median"] == 8 and f["derniers"][0]["gagnant"] == "PROPRE ET NET"
    a = {**by_id(101001), "fiche": f, "fiche_complete": True, "metier_libelle": "nettoyage", "date_limite_fr": ""}
    html = render.render("email_digest.html", biz=business(), avis=[a], date_fr="", criteres="")
    assert "Avant de répondre" in html and "PROPRE ET NET" in html and "55 000 €" in html
    html = render.render("email_digest.html", biz=business(), avis=[{**a, "fiche_complete": False}], date_fr="", criteres="")
    assert "réservés à l'offre Pro" in html and "PROPRE ET NET" not in html
    assert render.euros(1250000) == "1,25 M€"


def test_badge_pme():
    from radar.boamp import _pme
    assert _pme({"famille": "MAPA"}).startswith("Petit marché")
    assert _pme({"famille": "FNS"})
    assert _pme({"famille": "JOUE", "donnees": '{"cbc:SMESuitableIndicator": "true"}'})
    assert _pme({"famille": "JOUE", "donnees": '{"cbc:SMESuitableIndicator": true}'})
    assert not _pme({"famille": "JOUE", "donnees": '{"cbc:SMESuitableIndicator": "false"}'})


def test_promo_hors_periode_ou_non_publiee(monkeypatch):
    from datetime import date
    import radar.promo as promo
    appels = []
    monkeypatch.setattr(promo, "_call", lambda *a, **k: appels.append(a) or {})
    assert promo.code_du_jour(date(2026, 10, 19)) == "PRO1910"
    assert promo.en_promo(date(2026, 10, 19)) and promo.en_promo(date(2026, 10, 23))
    assert not promo.en_promo(date(2026, 10, 24)) and not promo.en_promo(date(2026, 10, 16))
    assert promo.en_promo(date(2026, 11, 16)) and not promo.en_promo(date(2026, 11, 13))
    biz = promo.business()
    biz["promo"]["publie"] = False
    monkeypatch.setattr(promo, "business", lambda: biz)
    assert promo.run(date(2026, 10, 20)) is None and not appels
    biz["promo"]["publie"] = True
    assert promo.run(date(2026, 10, 24)) is None and not appels


def test_nouveaux_metiers_et_faux_amis():
    from radar.matching import Matcher
    m = Matcher(metiers())
    assert "platrerie" in m.metiers_of({"objet": "Travaux de cloisons et faux plafonds au groupe scolaire", "descripteurs": []})
    assert "carrelage-sols" not in m.metiers_of({"objet": "Restauration de la chapelle Saint-Jean", "descripteurs": []})
    assert "voirie-vrd" not in m.metiers_of({"objet": "Nettoyage de voirie et balayage mécanisé", "descripteurs": []})
    assert "voirie-vrd" in m.metiers_of({"objet": "Réfection de chaussée et trottoirs rue des Lilas", "descripteurs": []})
