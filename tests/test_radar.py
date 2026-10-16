import json
from datetime import date, timedelta
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
    alerte = next(m for m in (tmp_path / "outbox").glob("*.html") if "Bienvenue" not in m.name and "essai" not in m.name).read_text()
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
                    "recurring": {"interval": data["recurring[interval]"]} if "recurring[interval]" in data else None}
        if path == "payment_links" and data["metadata[radar_offre]"] == "dossier":
            assert data["after_completion[redirect][url]"].endswith("/dossier/envoyer/") and "subscription_data[trial_period_days]" not in data
            return {"id": f"plink_{data['line_items[0][price]']}", "url": f"https://buy.stripe.com/test_dossier_{data['line_items[0][price]']}"}
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
    assert biz["dossier"]["lien_paiement"].endswith("price_19900") and biz["dossier"]["lien_pro"].endswith("price_14900")
    assert biz["dossier"]["stripe_link_ids"] == ["plink_price_19900", "plink_price_14900"]
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


def test_criteres_automatiques(monkeypatch, tmp_path):
    from radar import criteres
    monkeypatch.setenv("RESEND_INBOUND_KEY", "re_x")
    monkeypatch.setenv("RADAR_DRY_RUN", "1")
    monkeypatch.setattr("radar.mailer.OUTBOX", tmp_path)
    biz = dict(business(), criteres_adresse="criteres@abc.resend.app")
    corps = "Bonjour,\n\nMerci de modifier mes critères d'alerte :\n\nMétier : Carrelage et sols\nDépartements : 33, 47\nMots-clés : crèche, gymnase\n"
    rec = [{"id": "e1", "from": "Jean <Jean@Exemple.fr>", "to": ["criteres@abc.resend.app"], "subject": "Modifier mes critères"},
           {"id": "e2", "from": "x@y.fr", "to": ["autre@abc.resend.app"], "subject": "Modifier mes critères"}]
    monkeypatch.setattr(criteres, "_resend", lambda path, key, session=None: {"data": rec} if "limit" in path else {"text": corps})
    maj = []
    monkeypatch.setattr(criteres, "_stripe_update", lambda sid, meta, session=None: maj.append((sid, meta)))
    subs = [{"email": "jean@exemple.fr", "plan": "pro", "metiers": ["peinture"], "departements": ["91"], "mots_cles": [], "stripe_subscription": "sub_1"}]
    state = {}
    n, err = criteres.traiter(subs, biz, metiers(), state, lambda s, m: "ok")
    assert n == 1 and not err and maj == [("sub_1", {"metier": "carrelage-sols", "departements": "33, 47", "motscles": "crèche, gymnase"})]
    assert subs[0]["metiers"] == ["carrelage-sols"] and state["criteres_vus"] == ["e1"]
    assert criteres.traiter(subs, biz, metiers(), state, lambda s, m: "ok")[0] == 0   # pas de double traitement


def test_film_accueil_donnees_reelles(tmp_path):
    from datetime import timedelta
    from radar import site

    class H:
        def fiche(self, siret, metier):
            return {"n": 3, "mois": 24, "montant_median": 38000, "offres_median": 4,
                    "derniers": [{"date": "2026-03-02", "objet": "Entretien des parcs", "montant": 41200, "offres": 5, "gagnant": "X"},
                                 {"date": "2025-09-10", "objet": "Tonte", "montant": 36800, "offres": 3, "gagnant": "Y"}]}

        def sauver(self):
            pass

    dl = (date.today() + timedelta(days=20)).isoformat()
    avis = [{**a, "date_limite": dl, "acheteur_siret": "21330063500017"} for a in AVIS]
    site.build(avis, business(), metiers(), M, tmp_path / "s", stats={"total": 548, "metiers_hits": 9}, histo=H())
    html = (tmp_path / "s" / "index.html").read_text()
    assert 'id="film"' in html and "41 200" in html and "38 000" in html and "Saint-Exemple" not in html
    assert "{{" not in html and "static/film.mp3" in html
    site.build(avis, business(), metiers(), M, tmp_path / "t", stats={"total": 548, "metiers_hits": 9})
    sans = (tmp_path / "t" / "index.html").read_text()
    assert 'id="film"' in sans and "Avant de répondre" not in sans.split('id="film"')[1].split("</script>")[0]


def test_parrainage_mois_offerts(monkeypatch, tmp_path):
    from radar import parrainage
    monkeypatch.setenv("RADAR_DRY_RUN", "1")
    monkeypatch.setattr("radar.mailer.OUTBOX", tmp_path)
    appels = []

    def fake_call(method, path, data=None, params=None):
        appels.append((method, path, data))
        return {}
    monkeypatch.setattr(parrainage, "_call", fake_call)
    biz = business()
    parrain = {"email": "p@x.fr", "plan": "pro", "statut": "active", "stripe_subscription": "sub_p", "customer": "cus_p"}
    c = parrainage.code("sub_p")
    assert len(c) == 6 and c.isalnum() and parrainage.lien(biz, parrain).endswith(f"/parrainage/?ami={c}")
    filleul = {"email": "f@y.fr", "plan": "essentiel", "statut": "trialing", "stripe_subscription": "sub_f", "customer": "cus_f", "ref": c.lower()}
    tricheur = {"email": "P@x.fr", "plan": "pro", "statut": "active", "stripe_subscription": "sub_t", "customer": "cus_t", "ref": c}
    state = {}
    n, err = parrainage.traiter([parrain, filleul, tricheur], biz, state)
    assert n == 1 and not err and appels[0][1] == "customers/cus_f/balance_transactions" and appels[0][2]["amount"] == -2900
    assert filleul["filleul"]
    filleul["statut"] = "active"                      # fin de l'essai : le parrain est récompensé, une seule fois
    n, err = parrainage.traiter([parrain, filleul, tricheur], biz, state)
    assert n == 1 and appels[-1][1] == "customers/cus_p/balance_transactions" and appels[-1][2]["amount"] == -4900
    assert parrainage.traiter([parrain, filleul, tricheur], biz, state)[0] == 0
    assert sum("filleul" in m.name for m in tmp_path.glob("*.html")) == 1


def test_parrainage_repli_bon_de_reduction(monkeypatch):
    from radar import parrainage
    appels = []

    def fake_call(method, path, data=None, params=None):
        appels.append((method, path, data))
        if "balance_transactions" in path or path.startswith("coupons/"):
            raise RuntimeError("Stripe 403")
        return {"discounts": ["di_promo"]} if method == "GET" else {}
    monkeypatch.setattr(parrainage, "_call", fake_call)
    s = {"email": "a@x.fr", "plan": "essentiel", "stripe_subscription": "sub_1", "customer": "cus_1"}
    assert parrainage.crediter(s, business(), "test") == "bon"
    assert appels[-1] == ("POST", "subscriptions/sub_1", {"discounts[0][discount]": "di_promo", "discounts[1][coupon]": "radar-parrainage-essentiel"})


def test_avis_clients(monkeypatch, tmp_path):
    from radar import avis
    monkeypatch.setenv("RESEND_INBOUND_KEY", "re_x")
    monkeypatch.setenv("RADAR_DRY_RUN", "1")
    monkeypatch.setattr("radar.mailer.OUTBOX", tmp_path)
    biz = dict(business(), criteres_adresse="criteres@abc.resend.app")
    corps = {"e1": "Bonjour,\n\nNote : 4/5\nNom affiché : Julien M.\nActivité : Paysagiste, Agen\nPublication : oui\nAvis : Très utile, "
                   "j'ai trouvé 2 marchés.\nAppelez-moi au 06 12 34 56 78 ou www.moi.fr\n\nLe 3 nov. 2026, Julien a écrit :\n> vieux",
             "e2": "Note : 1/5\nNom affiché : X\nPublication : oui\nAvis : bande de connards",
             "e3": "Note : 2/5\nNom affiché : Y\nPublication : oui\nAvis : Trop d'alertes hors sujet."}
    rec = [{"id": "e1", "from": "Julien <julien@ex.fr>", "to": ["criteres@abc.resend.app"], "subject": "Mon avis"},
           {"id": "e2", "from": "b@ex.fr", "to": ["criteres@abc.resend.app"], "subject": "Mon avis"},
           {"id": "e3", "from": "inconnu@ex.fr", "to": ["criteres@abc.resend.app"], "subject": "Mon avis"},
           {"id": "e4", "from": "julien@ex.fr", "to": ["criteres@abc.resend.app"], "subject": "Modifier mes critères"}]
    monkeypatch.setattr(avis, "_resend", lambda path, key, session=None: {"data": rec} if "limit" in path else {"text": corps[path.rsplit("/", 1)[1]]})
    subs = [{"email": "julien@ex.fr", "plan": "essentiel", "statut": "active", "metiers": ["espaces-verts"], "debut": 1790000000,
             "stripe_subscription": "sub_1"}, {"email": "b@ex.fr", "plan": "pro", "statut": "active", "metiers": []}]
    alertes, state, f = [], {}, tmp_path / "avis.json"
    n, err = avis.recevoir(subs, biz, metiers(), state, lambda s, h: alertes.append(s), path=f)
    pub = avis.charger(f)
    assert n == 1 and not err and len(pub) == 1 and pub[0]["note"] == 4 and pub[0]["nom"] == "Julien M."
    assert pub[0]["texte"] == "Très utile, j'ai trouvé 2 marchés. Appelez-moi au ou" and pub[0]["depuis"]
    assert len(alertes) == 2 and "non publié" in alertes[1] and state["avis_vus"] == ["e1", "e2", "e3"]
    assert avis.recevoir(subs, biz, metiers(), state, lambda s, h: None, path=f)[0] == 0
    # demande d'avis : seulement après 1 mois d'abonnement payant, une seule fois
    now = 1790000000 + 31 * 86400
    monkeypatch.delenv("RADAR_DEMO", raising=False)
    assert avis.demander(subs, biz, state, lambda b, s: "https://lien", now_ts=now)[0] == 1   # simulé : envoyé mais non marqué ; b sans date de début ignoré
    monkeypatch.setenv("RADAR_DEMO", "1")
    assert avis.demander(subs, biz, state, lambda b, s: "https://lien", now_ts=now)[0] == 1
    assert avis.demander(subs, biz, state, lambda b, s: "https://lien", now_ts=now)[0] == 0
    assert avis.resume(pub)["moyenne"] == 4


def test_ia_attend_le_premier_client_payant(monkeypatch, tmp_path):
    import radar.cli as cli
    from radar.config import ia_autorisee
    monkeypatch.setattr("radar.mailer.OUTBOX", tmp_path / "outbox")
    monkeypatch.setattr(cli, "OUT", tmp_path)
    monkeypatch.setenv("OWNER_EMAIL", "owner@example.com")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    st = tmp_path / "state.json"
    st.write_text("{}")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-x")
    assert not ia_autorisee(st)
    st.write_text('{"premier_client_payant": "2026-11-01"}')
    assert ia_autorisee(st)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    appels = []
    monkeypatch.setattr(cli.ai, "enrich", lambda *a, **k: appels.append(1))
    run = cli.run_demo()                                   # abonnés de démo : aucun payant Stripe
    assert "premier_client_payant" not in run and not appels
    subs = [{"email": "c@example.com", "plan": "essentiel", "statut": "active", "source": "stripe", "metiers": ["nettoyage"],
             "departements": ["33"], "mots_cles": []}]
    run = cli.run_daily(avis_new=[dict(a) for a in AVIS], avis_open=[], subs=subs)
    assert run["premier_client_payant"] and not appels    # pas de clé : rien n'est dépensé, le propriétaire est prévenu
    assert any("Premier_client_payant" in m.name for m in (tmp_path / "outbox").glob("*.html"))
    assert json.loads((tmp_path / "demo-data" / "state.json").read_text())["premier_client_payant"]


def test_ville_acheteur():
    from radar.boamp import _ville_acheteur as v
    ef = {"EFORMS": {"ContractNotice": {
        "cac:ContractingParty": {"cac:Party": {"cac:PartyIdentification": {"cbc:ID": "ORG-0001"}}},
        "ext:UBLExtensions": {"ext:UBLExtension": {"ext:ExtensionContent": {"efext:EformsExtension": {"efac:Organizations": {
            "efac:Organization": [
                {"efac:Company": {"cac:PartyIdentification": {"cbc:ID": "ORG-0002"}, "cac:PostalAddress": {"cbc:CityName": "Grenoble", "cbc:PostalZone": "38000"}}},
                {"efac:Company": {"cac:PartyIdentification": {"cbc:ID": "ORG-0001"}, "cac:PostalAddress": {"cbc:CityName": "Seyssinet-Pariset", "cbc:PostalZone": "38170"}}}]}}}}}}}}
    assert v(ef, ["38"]) == "Seyssinet-Pariset"          # l'acheteur, pas le tribunal de recours
    assert v({"FNSimple": {"organisme": {"ville": "LYON CEDEX 03", "cp": "69003"}}}, ["69"]) == "Lyon"
    assert v({"MAPA": {"organisme": {"adr": {"ville": "MANZAT", "cp": "63410"}}}}, ["63"]) == "Manzat"
    assert v({"FNSimple": {"organisme": {"ville": "Paris", "cp": "75001"}}}, ["33"]) == ""   # hors département
    assert v("pas du json", ["33"]) == ""


def test_pages_villes(tmp_path):
    from radar import site
    from radar.config import business, metiers as load_metiers
    from radar.matching import Matcher
    biz, metiers = business(), load_metiers()
    avis = [{"id": str(i), "objet": f"Nettoyage des locaux lot {i}", "acheteur": "Mairie de Lyon", "departements": ["69"],
             "descripteurs": ["Nettoyage"], "date_parution": "2099-01-01", "date_limite": "2099-02-01", "url": "https://x/",
             "nature": "", "type_marche": "", "texte": "", "pme": "", "ville": "Lyon" if i < 5 else "Bron",
             "metiers": ["nettoyage"]} for i in range(7)]
    site.build(avis, biz, metiers, Matcher(metiers), tmp_path)
    page = tmp_path / "appels-offres" / "villes" / "lyon-69.html"
    assert page.exists() and "à Lyon (69)" in page.read_text()
    assert not (tmp_path / "appels-offres" / "villes" / "bron-69.html").exists()   # trop peu d'avis : pas de page creuse
    assert "villes/lyon-69.html" in (tmp_path / "appels-offres" / "nettoyage" / "69.html").read_text()
    assert "/appels-offres/villes/lyon-69.html" in (tmp_path / "sitemap.xml").read_text()


def test_quota_prospection(tmp_path, monkeypatch):
    import json
    import radar.cli as cli
    from datetime import date
    monkeypatch.setattr(cli, "DATA", tmp_path)
    (tmp_path / "runs").mkdir()
    assert cli.quota_prospection(20, [], date(2026, 11, 3)) == 20
    (tmp_path / "runs" / "2026-11-03.json").write_text(json.dumps({"emails_envoyes": 70}))
    assert cli.quota_prospection(20, [], date(2026, 11, 3)) == 10      # 100/jour Resend, marge 20
    for d in range(4, 10):
        (tmp_path / "runs" / f"2026-11-{d:02d}.json").write_text(json.dumps({"emails_envoyes": 300}))
    assert cli.quota_prospection(20, [], date(2026, 11, 10)) == 0      # le mois serait dépassé


def test_emails_habillage_commun():
    """Tous les e-mails portent le logo hébergé (avec texte de secours) et la signature de l'entreprise."""
    from radar import render
    biz = business()
    offre = biz["offres"]["essentiel"]
    p = {"marche_gagne": "Nettoyage", "nom": "X"}
    cas = {
        "email_bienvenue.html": {"criteres": "nettoyage", "sub": {}},
        "email_fin_essai.html": {"criteres": "nettoyage", "offre": offre, "fin_fr": "1 mai"},
        "email_avis.html": {"offre": offre},
        "email_parrainage.html": {"offre": offre, "lien": "https://x"},
        "email_criteres.html": {"ok": True, "criteres": "nettoyage"},
        "email_digest.html": {"avis": [], "date_fr": "", "criteres": ""},
        "email_resultats.html": {"items": [], "criteres": ""},
        "email_interne.html": {"contenu": "<p>Test</p>"},
        "email_prospect.html": {"p": p, "n": 3, "zone": "", "ex": None, "lien": "https://x", "metier": {"libelle": "Nettoyage"}},
    }
    for nom, ctx in cas.items():
        html = render.render(nom, biz=biz, **ctx)
        assert f"{biz['site_url']}/static/logo-email.png" in html and f'alt="{biz["nom"]}"' in html, nom
        assert f"L'équipe {biz['nom']}" in html, nom
        assert html.count("L'équipe") == 1, nom
        assert biz["editeur"]["nom"] not in html, nom  # jamais le nom de la personne


def test_recents_reserves_et_carte(tmp_path):
    from radar import site
    from radar.config import business, metiers as load_metiers
    from radar.matching import Matcher
    biz, metiers = business(), load_metiers()
    auj, vieux = date.today().isoformat(), (date.today() - timedelta(days=5)).isoformat()
    avis = [{"id": str(i), "objet": f"Nettoyage lot {i}", "acheteur": "Mairie", "departements": [dep], "descripteurs": [],
             "date_parution": parution, "date_limite": "2099-02-01", "url": f"https://x/{i}", "nature": "", "type_marche": "",
             "texte": "", "pme": "", "ville": "", "metiers": ["nettoyage"]}
            for i, (dep, parution) in enumerate([("33", auj), ("33", vieux), ("971", vieux), ("2A", auj)])]
    site.build(avis, biz, metiers, Matcher(metiers), tmp_path)
    p = (tmp_path / "appels-offres" / "nettoyage" / "33.html").read_text()
    assert "https://x/1" in p and "https://x/0" not in p and "réservé aux abonnés" in p and "Gironde (33)" in p
    m = (tmp_path / "appels-offres" / "nettoyage" / "index.html").read_text()
    assert 'class="carte"' in m and 'href="971.html"' in m and 'data-code="2B"' in m and "https://x/3" not in m
    hub = (tmp_path / "appels-offres" / "index.html").read_text()
    assert 'href="departements/971.html"' in hub
    d = (tmp_path / "appels-offres" / "departements" / "2A.html").read_text()
    assert "Corse-du-Sud" in d and "https://x/3" not in d and "réservé aux abonnés" in d


def _biz_dossier(monkeypatch):
    from radar import config
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    monkeypatch.setenv("RESEND_INBOUND_KEY", "k")
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_x")
    monkeypatch.setattr(config, "premier_client_payant", lambda *a: "2026-10-20")
    biz = config.business()
    biz["dossier"].update(lien_paiement="https://buy.stripe.com/d", lien_pro="https://buy.stripe.com/p", stripe_link_ids=["plink_1"])
    biz["dossier_actif"] = config.dossier_actif(biz)
    return biz


def test_dossier_inactif_sans_ia(monkeypatch):
    from radar import config, dossier
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    biz = config.business()
    biz["dossier"]["lien_paiement"] = "https://buy.stripe.com/d"
    assert not config.dossier_actif(biz) and dossier.traiter(biz, {}) == (0, [])


def test_dossier_extraction_et_priorite():
    import io, zipfile
    from docx import Document
    from radar import dossier
    doc = Document(); doc.add_paragraph("Article 1 du CCTP : nettoyage quotidien des bureaux")
    b = io.BytesIO(); doc.save(b)
    z = io.BytesIO()
    with zipfile.ZipFile(z, "w") as f:
        f.writestr("DCE/annexe-plan.txt", "plan des locaux")
        f.writestr("DCE/CCTP.docx", b.getvalue())
        f.writestr("DCE/RC.txt", "Règlement de consultation : critère valeur technique 60 %, prix 40 %")
        f.writestr("DCE/photo.jpg", b"\xff\xd8")
    pieces = dossier.extraire_texte("dce.zip", z.getvalue())
    assert {p[0].split("/")[-1] for p in pieces} == {"annexe-plan.txt", "CCTP.docx", "RC.txt"}
    texte = dossier.assembler(pieces)
    assert texte.index("RC.txt") < texte.index("CCTP.docx") < texte.index("annexe-plan.txt")
    assert "nettoyage quotidien" in texte


def test_dossier_parcours_complet(monkeypatch, tmp_path):
    """Paiement → accusé ; e-mail avec DCE → dossier Word envoyé ; délai trop court → remboursement."""
    import base64, io
    from docx import Document
    from radar import dossier, mailer
    biz = _biz_dossier(monkeypatch)
    envoyes, rembourses = [], []
    monkeypatch.setattr(mailer, "send", lambda to, sujet, html, exp, reply_to=None, attachments=None, **k: envoyes.append((to, sujet, html, attachments)))
    dans = lambda j: (date.today() + timedelta(days=j)).isoformat()
    achats = {"cs_1": {"email": "client@ex.fr", "montant": 199, "payment_intent": "pi_1", "date": date.today().isoformat(), "avis": "26-101001"},
              "cs_2": {"email": "presse@ex.fr", "montant": 149, "payment_intent": "pi_2", "date": date.today().isoformat(), "avis": ""},
              "cs_3": {"email": "oubli@ex.fr", "montant": 199, "payment_intent": "pi_3", "date": (date.today() - timedelta(days=8)).isoformat(), "avis": ""}}
    monkeypatch.setattr(dossier, "paiements", lambda biz, session=None: achats)
    monkeypatch.setattr(dossier, "rembourser", lambda a, session=None: rembourses.append(a["payment_intent"]))
    dce = "Règlement de consultation. " * 200
    monkeypatch.setattr(dossier, "pieces_jointes", lambda eid, key, session=None: [("RC.pdf", dce)] if eid != "e_vide" else [])
    recus = {"data": [{"id": "e1", "from": "Client <client@ex.fr>", "to": ["dossier@elkaurtel.resend.app"]},
                      {"id": "e2", "from": "presse@ex.fr", "to": ["dossier@elkaurtel.resend.app"]},
                      {"id": "e3", "from": "inconnu@ex.fr", "to": ["dossier@elkaurtel.resend.app"]},
                      {"id": "e_vide", "from": "oubli@ex.fr", "to": ["criteres@elkaurtel.resend.app"]}]}
    monkeypatch.setattr(dossier, "_resend", lambda path, key, session=None: recus if path.startswith("/emails/receiving?") else {"text": "Entreprise : Net33, 12 salariés"})
    vus_infos = []
    def gen(texte, infos):
        vus_infos.append(infos)
        urgent = "presse" in infos or len(vus_infos) == 2
        return {"objet": "Nettoyage des locaux", "acheteur": "Mairie", "date_limite": dans(2 if urgent else 20) + " 12:00",
                "criteres": [{"critere": "Valeur technique", "poids": "60 %", "conseil": "Détailler"}],
                "memoire": [{"titre": "1. Moyens humains", "texte": "Équipe de [À COMPLÉTER : nombre] agents.\n\nEncadrement."}],
                "lettre": "Madame, Monsieur", "dume": [{"rubrique": "II-A", "reponse": "Net33"}], "references": [], "pieces": []}
    state = {}
    livres, erreurs = dossier.traiter(biz, state, generer_fn=gen)
    assert (livres, erreurs) == (1, [])
    sujets = {(to, sujet.split(" : ", 1)[1]) for to, sujet, _, _ in envoyes}
    assert ("client@ex.fr", "votre dossier est prêt") in sujets
    assert ("presse@ex.fr", "délai trop court, vous êtes remboursé") in sujets
    assert ("inconnu@ex.fr", "aucun dossier en attente") in sujets
    assert ("oubli@ex.fr", "remboursement de votre dossier") in sujets
    assert sorted(rembourses) == ["pi_2", "pi_3"] and "Net33" in vus_infos[0] and "26-101001" in vus_infos[0]
    pj = next(a for to, s, h, a in envoyes if a)[0]
    assert pj["filename"] == "dossier-nettoyage-des-locaux.docx"
    texte = "\n".join(p.text for p in Document(io.BytesIO(base64.b64decode(pj["content"]))).paragraphs)
    assert "Moyens humains" in texte and "[À COMPLÉTER : nombre]" in texte
    assert state["dossiers"]["cs_1"]["etat"] == "livre" and "client@ex.fr" not in json.dumps(state)   # rien de personnel dans le dépôt public
    envoyes.clear()
    assert dossier.traiter(biz, state, generer_fn=gen) == (0, []) and not envoyes   # rien n'est refait au passage suivant


def test_dossier_echec_ia_rembourse_apres_deux_essais(monkeypatch):
    from radar import dossier, mailer
    biz = _biz_dossier(monkeypatch)
    alertes, rembourses = [], []
    monkeypatch.setattr(mailer, "send", lambda *a, **k: None)
    monkeypatch.setattr(dossier, "paiements", lambda biz, session=None: {"cs_1": {"email": "c@ex.fr", "montant": 199, "payment_intent": "pi_1", "date": date.today().isoformat(), "avis": ""}})
    monkeypatch.setattr(dossier, "rembourser", lambda a, session=None: rembourses.append(1))
    monkeypatch.setattr(dossier, "pieces_jointes", lambda *a, **k: [("RC.pdf", "x" * 3000)])
    monkeypatch.setattr(dossier, "_resend", lambda path, key, session=None: {"data": [{"id": "e1", "from": "c@ex.fr", "to": ["dossier@elkaurtel.resend.app"]}]} if "?" in path else {"text": ""})
    def gen(*a):
        raise RuntimeError("panne")
    state = {}
    dossier.traiter(biz, state, alerte=lambda s, h: alertes.append(s), generer_fn=gen)
    assert state["dossiers"]["cs_1"]["etat"] == "attente" and not rembourses
    dossier.traiter(biz, state, alerte=lambda s, h: alertes.append(s), generer_fn=gen)
    assert state["dossiers"]["cs_1"]["etat"] == "rembourse" and rembourses and any("non préparé" in a for a in alertes)


def test_bouton_dossier_dans_les_alertes(monkeypatch):
    from radar import cli
    from radar.render import render
    biz = _biz_dossier(monkeypatch)
    a = dict(by_id(101001), date_limite_fr="6 novembre", fiche=None)
    pro = cli.lien_dossier(biz, {"plan": "pro", "email": "a@b.fr"})
    assert pro["prix"] == 149 and pro["url"].endswith("/p")
    html = render("email_digest.html", biz=biz, avis=[a], date_fr="lundi", criteres="x", lien_dossier=pro)
    assert "Préparer ma réponse (149 €)" in html and "client_reference_id=26-101001" in html
    assert cli.lien_dossier(biz, {"plan": "essentiel"})["prix"] == 199
    biz["dossier_actif"] = False
    assert cli.lien_dossier(biz, {"plan": "pro"}) is None
