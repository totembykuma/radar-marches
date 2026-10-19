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
    mails = sorted(m.name for m in (tmp_path / "outbox").glob("*.html") if "attribués" not in m.name)   # sans le bilan du lundi
    assert len(mails) == 5 and sum("Bienvenue" in m for m in mails) == 2   # 2 bienvenues + 2 alertes + 1 rappel d'essai
    assert run["rappels_essai"] == 1 and sum("essai" in m for m in mails) == 1
    assert (tmp_path / "site" / "criteres" / "index.html").exists()
    assert not (tmp_path / "site" / "alerte-gratuite").exists()   # alerte gratuite retirée
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
            if data.get("metadata[radar_periode]") == "an":   # annuel : payé tout de suite, montant et renouvellement affichés
                assert "subscription_data[trial_period_days]" not in data and "12 mois" in data["custom_text[submit][message]"]
            else:
                assert data["subscription_data[trial_period_days]"] == 7
                assert "une seule fois par client" in data["custom_text[submit][message]"]
            return {"url": f"https://buy.stripe.com/test_{data['metadata[radar_offre]']}_{data['line_items[0][price]']}"}
        if path == "billing_portal/configurations":
            return {"login_page": {"url": "https://billing.stripe.com/p/login/test_x"}}
    monkeypatch.setattr(stripe_setup, "_call", fake)
    stripe_setup.run()
    biz = config.business()
    assert biz["offres"]["pro"]["lien_paiement"] == "https://buy.stripe.com/test_pro_price_4900"
    assert biz["offres"]["pro"]["lien_annuel"] == "https://buy.stripe.com/test_pro_price_39900"
    assert biz["offres"]["cle"]["lien_annuel"].endswith("price_119900")
    assert biz["offres"]["essentiel"]["stripe_paliers"] == ["price_2900", "price_1999", "price_999"]
    assert biz["offres"]["essentiel"]["stripe_price_ids"][-4:] == ["price_2900", "price_1999", "price_999", "price_16900"]
    ids_ess = biz["offres"]["essentiel"]["stripe_price_ids"]
    assert len(ids_ess) == len(set(ids_ess)) and "price_1UQrs879764n3f0zMVMks2Dq" in ids_ess   # anciens prix gardés pour les abonnés existants
    assert biz["stripe_mode"] == "test" and biz["portail_client"].endswith("test_x") and biz["nouvelle_grille_depuis"]
    assert biz["essai_gratuit_jours"] == 7
    assert biz["dossier"]["lien_paiement"].endswith("price_19900") and biz["dossier"]["lien_pro"].endswith("price_14900")
    assert biz["dossier"]["stripe_link_ids"] == ["plink_price_19900", "plink_price_14900", "plink_price_9900"]
    assert biz["dossier"]["lien_cle"].endswith("price_9900")
    assert biz["offres"]["cle"]["lien_paiement"].endswith("price_14900") and biz["offres"]["cle"]["stripe_paliers"][1] == "price_11999"
    depuis = biz["nouvelle_grille_depuis"]
    stripe_setup.run()  # relancer ne casse pas le fichier
    biz = config.business()
    assert biz["offres"]["pro"]["stripe_paliers"] == ["price_4900", "price_3999", "price_2999"]
    ids_pro = biz["offres"]["pro"]["stripe_price_ids"]
    assert len(ids_pro) == len(set(ids_pro)) and "price_39900" in ids_pro and biz["nouvelle_grille_depuis"] == depuis


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


def test_alerte_gratuite(monkeypatch, tmp_path):
    from radar import gratuit
    monkeypatch.setenv("RESEND_INBOUND_KEY", "re_x")
    monkeypatch.setenv("RADAR_DRY_RUN", "1")
    monkeypatch.setattr("radar.mailer.OUTBOX", tmp_path / "out")
    monkeypatch.setattr(gratuit, "FICHIER", tmp_path / "g.json")
    biz = dict(business(), alerte_gratuite={"adresse": "alerte@abc.resend.app"})
    dl = (date.today() + timedelta(days=20)).isoformat()
    avis = [{**a, "date_limite": dl} for a in AVIS]
    metier = next(k for k in metiers() if any(k in M.metiers_of(a) for a in avis))
    lib = metiers()[metier]["libelle"]
    rec = [{"id": "g1", "from": "Paul <Paul@PME.fr>", "to": ["alerte@abc.resend.app"], "subject": "Alerte gratuite"},
           {"id": "g2", "from": "client@x.fr", "to": ["alerte@abc.resend.app"], "subject": "Alerte gratuite"}]
    monkeypatch.setattr(gratuit, "_resend", lambda path, key, session=None: {"data": rec} if "limit" in path
                        else {"text": f"Métier : {lib}\nDépartements : 33\n"})
    subs = [{"email": "client@x.fr"}]
    state = {}
    st, err = gratuit.traiter(biz, metiers(), M, state, subs, avis, quota=50)
    assert not err and st["inscrits"] == 1 and st["envoyes"] == 1 and st["total"] == 1
    brut = (tmp_path / "g.json").read_text()
    assert "paul@pme.fr" not in brut.lower()                       # adresse chiffrée dans le dépôt public
    mail = next((tmp_path / "out").glob("*.html")).read_text()
    assert "STOP" in mail and "Essayer gratuitement" in mail and "{{" not in mail
    # même semaine : rien de plus ; désinscription : sortie de la liste
    assert gratuit.traiter(biz, metiers(), M, state, subs, avis, quota=50)[0]["envoyes"] == 0
    rec.append({"id": "g3", "from": "paul@pme.fr", "to": ["alerte@abc.resend.app"], "subject": "STOP alerte gratuite"})
    st, _ = gratuit.traiter(biz, metiers(), M, state, subs, avis, quota=50)
    assert st["desinscrits"] == 1 and st["total"] == 0


def test_relance_unique_prospects(monkeypatch):
    import radar.cli as cli
    from radar import gratuit
    monkeypatch.setattr(gratuit, "emails_connus", lambda: set())
    monkeypatch.setattr(cli.time, "sleep", lambda s: None)
    evts = {"a": {"to": ["a@pme.fr"], "last_event": "clicked"}, "b": {"to": ["b@pme.fr"], "last_event": "delivered"},
            "c": {"to": ["client@pme.fr"], "last_event": "clicked"}, "old": {"to": ["o@pme.fr"], "last_event": "clicked"}}
    monkeypatch.setattr(cli.mailer, "lire_email", lambda i: evts[i])
    envoyes = []
    monkeypatch.setattr(cli.mailer, "send", lambda to, *a, **k: envoyes.append(to) or {"id": "x"})
    m = next(iter(metiers()))
    il_y_a = (date.today() - timedelta(days=5)).isoformat()
    journal = [{"date": il_y_a, "envoyes": 3, "ids": ["a", "b", "c"], "suivi": {i: [m, "33"] for i in "abc"}},
               {"date": il_y_a, "envoyes": 1, "ids": ["old"]},                                    # ancien e-mail sans annonce de rappel
               {"date": date.today().isoformat(), "envoyes": 1, "ids": ["a"], "suivi": {"a": [m, "33"]}}]   # trop récent
    zone = lambda metier, dep, source="": (4, "dans le département 33", None, "https://x")
    n = cli.relancer(journal, business(), metiers(), {"expediteur": "b@x.fr"}, zone, ["client@pme.fr"], quota=10)
    assert n == 1 and envoyes == ["a@pme.fr"] and journal[0]["relance_faite"] and "relance_faite" not in journal[2]
    assert cli.relancer(journal, business(), metiers(), {"expediteur": "b@x.fr"}, zone, [], quota=10) == 0   # jamais deux fois


class _FakeAnn:
    def __init__(self, cache):
        self.cache = cache


def _lignes():
    return [{"d": "2026-09-0%d" % (i + 1), "o": f"Nettoyage des écoles lot {i}", "m": 50000 + i, "n": 3, "a": "21690123000011",
             "l": "69", "k": "nettoyage", "t": ["11122233300011" if i < 3 else "99988877700011"]} for i in range(5)]


def test_laureats_exclut_les_entrepreneurs_individuels():
    from radar import laureats
    m = metiers()
    assert laureats.metier_du_cpv("90911200-8", m) == "nettoyage"
    assert laureats.metier_du_cpv("03000000-1", m) is None
    ann = _FakeAnn({"111222333": ["PROPRE SAS", "5710", "LYON", "69"], "999888777": ["JEAN DUPONT", "1000", "BRON", "69"],
                    "216901230": ["COMMUNE DE LYON", "7210", "LYON", "69"]})
    data = laureats.preparer(_lignes(), ann, m)
    assert [f["nom"] for f in data["fiches"]] == ["PROPRE SAS"]   # l'entrepreneur individuel n'a pas de page
    f = data["fiches"][0]
    assert f["slug"] == "propre-sas-111222333" and f["marches"][0]["acheteur"] == "Commune De Lyon"
    assert data["reperes"]["nettoyage"]["offres_median"] == 3


def test_pages_entreprises_barometre_et_posts(tmp_path, monkeypatch):
    from radar import barometre, laureats, site
    m = metiers()
    monkeypatch.setattr(barometre, "DOSSIER", tmp_path / "baro")
    b = {"mois": "2026-09", "avis_boamp": 9000, "appels": 1200,
         "par_metier": {k: {"n": 100, "pme": 10, "top_deps": [["69", 12]]} for k in m},
         "par_region": [["Auvergne-Rhône-Alpes", 150]], "top_deps": [["69", 40]], "top_acheteurs": [["Ville de Lyon", 9]],
         "precedent": {"mois": "2026-08", "appels": 1000, "par_metier": {"nettoyage": 80}}, "reperes": {}}
    barometre.enregistrer(b)
    ann = _FakeAnn({"111222333": ["PROPRE SAS", "5710", "LYON", "69"], "216901230": ["COMMUNE DE LYON", "7210", "LYON", "69"]})
    data = laureats.preparer(_lignes(), ann, m)
    avis = [{"id": str(i), "objet": f"Nettoyage des locaux lot {i}", "acheteur": "Mairie de Lyon", "departements": ["69"],
             "descripteurs": ["Nettoyage"], "date_parution": "2026-01-01", "date_limite": "2099-02-01", "url": "https://x/",
             "nature": "", "type_marche": "", "texte": "", "pme": "", "ville": "Lyon", "metiers": ["nettoyage"]} for i in range(7)]
    site.build(avis, business(), m, Matcher(m), tmp_path / "s", laureats=data)
    page = (tmp_path / "s" / "entreprises" / "propre-sas-111222333.html").read_text()
    assert "PROPRE SAS : marchés publics remportés" in page and "Commune De Lyon" in page
    assert "propre-sas-111222333.html" in (tmp_path / "s" / "entreprises" / "departements" / "69.html").read_text()
    baro = (tmp_path / "s" / "barometre" / "index.html").read_text()
    assert "septembre 2026" in baro and "+20 %" in baro and "+25 %" in baro
    assert (tmp_path / "s" / "barometre" / "2026-09.html").exists()
    sm = (tmp_path / "s" / "sitemap.xml").read_text()
    assert "/entreprises/propre-sas-111222333.html" in sm and "/barometre/2026-09.html" in sm
    posts = json.loads((tmp_path / "s" / "linkedin" / "semaine.json").read_text())["posts"]
    assert len(posts) >= 3 and all(p["date"].endswith("T09:00:00") for p in posts)
    rss = (tmp_path / "s" / "linkedin" / "flux.xml").read_text()
    assert rss.startswith("<?xml") and rss.count("<item>") == sum(p["date"][:10] <= date.today().isoformat() for p in posts)
    assert any("PROPRE SAS" in p["texte"] for p in posts) and any("/appels-offres/nettoyage/" in p["texte"] for p in posts)


def test_espace_client_lien_et_page(tmp_path):
    import base64, json as _json
    from radar import compte
    from radar.config import business, metiers as load_metiers
    biz, metiers = business(), load_metiers()
    k = next(iter(metiers))
    sub = {"email": "client@example.com", "plan": "essentiel", "statut": "trialing", "metiers": [k],
           "departements": ["33"], "mots_cles": [], "source": "stripe", "stripe_subscription": "sub_X", "fin_essai": 1800000000}
    url = compte.lien(biz, sub, metiers)
    assert "/compte/#" in url
    code = url.split("#", 1)[1]
    d = _json.loads(base64.urlsafe_b64decode(code + "=" * (-len(code) % 4)))
    assert d["e"] == "client@example.com" and d["d"] == ["33"] and d["m"][0][0] == k
    # Clients d'avant l'espace : le lien part une seule fois ; les nouveaux l'ont dans la bienvenue.
    import hashlib
    h = hashlib.sha256(b"client@example.com").hexdigest()[:16]
    state = {"known": [h]}
    n1, _ = compte.traiter([sub], biz, metiers, state)
    n2, _ = compte.traiter([sub], biz, metiers, state)
    assert (n1, n2) == (1, 0)


def test_flou_ne_contient_pas_le_vrai_texte():
    from datetime import date
    from radar import site
    a = {"id": "26-1", "objet": "Nettoyage secret de la mairie", "acheteur": "Ville de Test", "departements": ["33"],
         "date_parution": date.today().isoformat(), "jours_restants": 10, "montant_estime": 120000}
    f = site._recents([a], date.today(), "nettoyage")[0]
    assert "secret" not in str(f) and "Ville de Test" not in str(f) and f["montant"] == "120 000 €"


def test_montant_estime():
    from radar.boamp import _montant_estime as m
    assert m({"EFORMS": {"x": {"cbc:EstimatedOverallContractAmount": {"#text": "120000"}}}}) == 120000
    assert m("{}") is None and m(None) is None

def _biz_cle(monkeypatch):
    from radar import config
    biz = _biz_dossier(monkeypatch)
    biz["offres"]["cle"].update(lien_paiement="https://buy.stripe.com/c", lien_annuel="https://buy.stripe.com/ca")
    biz["dossier"]["lien_cle"] = "https://buy.stripe.com/x"
    biz["cle_actif"] = config.cle_actif(biz)
    return biz


def test_cle_dossiers_inclus_sans_paiement(monkeypatch):
    """Clé en main : l'e-mail suffit, 2 dossiers par mois, pas pendant l'essai, rien de personnel dans l'état."""
    from radar import dossier, mailer
    biz = _biz_cle(monkeypatch)
    envoyes = []
    monkeypatch.setattr(mailer, "send", lambda to, sujet, html, exp, reply_to=None, attachments=None, **k: envoyes.append((to, sujet.split(" : ", 1)[1], html)))
    monkeypatch.setattr(dossier, "paiements", lambda biz, session=None: {})
    monkeypatch.setattr(dossier, "pieces_jointes", lambda *a, **k: [("RC.pdf", "Règlement. " * 300)])
    mails = [{"id": f"e{i}", "from": "cle@ex.fr", "to": ["dossier@elkaurtel.resend.app"]} for i in range(3)] \
        + [{"id": "e9", "from": "essai@ex.fr", "to": ["dossier@elkaurtel.resend.app"]}]
    monkeypatch.setattr(dossier, "_resend", lambda path, key, session=None: {"data": mails} if "?" in path else {"text": "Net33"})
    gen = lambda dce, infos: {"objet": "Nettoyage", "date_limite": (date.today() + timedelta(days=30)).isoformat(), "memoire": [], "pieces": []}
    abonnes = [{"email": "cle@ex.fr", "plan": "cle", "statut": "active"}, {"email": "essai@ex.fr", "plan": "cle", "statut": "trialing"}]
    state = {}
    livres, erreurs = dossier.traiter(biz, state, generer_fn=gen, abonnes=abonnes)
    assert (livres, erreurs) == (2, [])
    sujets = [(to, s) for to, s, _ in envoyes]
    assert sujets.count(("cle@ex.fr", "votre dossier est prêt")) == 2
    assert ("cle@ex.fr", "vos dossiers inclus du mois sont utilisés") in sujets
    assert ("essai@ex.fr", "vos dossiers inclus démarrent au 1er paiement") in sujets
    assert "buy.stripe.com/x" in next(h for to, s, h in envoyes if "utilisés" in s)
    assert dossier.inclus_utilises(state["dossiers"], "cle@ex.fr") == 2 and "cle@ex.fr" not in json.dumps(state)


def test_cle_rapport_comment_gagner(monkeypatch):
    from radar import rapport, mailer
    biz = _biz_cle(monkeypatch)
    envoyes = []
    monkeypatch.setattr(mailer, "send", lambda to, sujet, html, exp, reply_to=None, **k: envoyes.append((to, sujet.split(" : ", 1)[1], html)))
    monkeypatch.setattr(rapport, "pieces_jointes", lambda *a, **k: [])
    mails = {"data": [{"id": "r1", "from": "cle@ex.fr", "to": ["rapport@elkaurtel.resend.app"], "subject": "Rapport 26-101001"},
                      {"id": "r2", "from": "pro@ex.fr", "to": ["rapport@elkaurtel.resend.app"], "subject": "Rapport 26-101001"},
                      {"id": "r3", "from": "cle@ex.fr", "to": ["rapport@elkaurtel.resend.app"], "subject": "Bonjour"}]}
    monkeypatch.setattr(rapport, "_resend", lambda path, key, session=None: mails if "?" in path else {"text": ""})
    prompts = []
    def ask(prompt, **k):
        prompts.append(prompt)
        return 'Voici : {"en_bref": "Ménage de la mairie", "chances": "bonnes", "pourquoi_chances": "Petit lot.", ' \
               '"criteres": [{"critere": "Prix", "poids": "40 %", "comment_gagner_des_points": "Rester sous 50 000 €"}], ' \
               '"prix": {"repere": "45 000 € HT", "source": "3 marchés précédents", "conseil": "Visez la médiane."}, ' \
               '"pieces": [{"piece": "Kbis", "ou_la_trouver": "infogreffe.fr", "obligatoire": true}], "pieges": ["Visite obligatoire"], ' \
               '"plan": [{"quand": "J-10", "action": "Visiter le site"}], "a_demander": []}'
    class H:
        def fiche(self, siret, metier):
            return {"n": 3, "mois": 24, "montant_median": 45000, "offres_median": 4,
                    "derniers": [{"date": "2025-03-01", "objet": "Ménage", "montant": 44000, "offres": 4, "gagnant": "Net SA"}]}
    abonnes = [{"email": "cle@ex.fr", "plan": "cle", "statut": "trialing", "metiers": ["nettoyage"]},
               {"email": "pro@ex.fr", "plan": "pro", "statut": "active", "metiers": ["nettoyage"]}]
    state = {}
    n, erreurs = rapport.traiter(biz, state, abonnes, metiers(), histo=H(), ask_fn=ask, avis_fn=lambda ref: dict(by_id(101001), acheteur_siret="123"))
    assert (n, erreurs) == (1, [])
    to, sujet, html = envoyes[0]
    assert to == "cle@ex.fr" and sujet.startswith("comment gagner") and "45 000 € HT" in html and "Visite obligatoire" in html
    assert "Net SA" in prompts[0] and "dossier/envoyer/?avis=26-101001" in html
    assert ("pro@ex.fr", "rapport réservé à l'offre Clé en main", ) in [(t, s) for t, s, _ in envoyes]
    assert ("cle@ex.fr", "avis introuvable") in [(t, s) for t, s, _ in envoyes]
    assert "cle@ex.fr" not in json.dumps(state)
    assert rapport.reference("Rapport 26-123456") == "26-123456"


def test_cle_visible_seulement_avec_ia(monkeypatch, tmp_path):
    from radar import cli, compte
    from radar.render import render
    biz = _biz_cle(monkeypatch)
    html = render("index.html", biz=biz, root="./", avis=[], stats={}, metiers={})
    assert "Clé en main" in html and "prices-3" in html
    biz["cle_actif"] = False
    assert "Clé en main" not in render("legal/cgv.html", biz=biz, root="./")
    l = cli.lien_dossier(dict(biz, cle_actif=True), {"plan": "cle", "email": "a@b.fr"})
    assert l["inclus"] and l["rapport"] == "rapport@elkaurtel.resend.app"
    lien = compte.lien(biz, {"email": "a@b.fr", "plan": "cle", "metiers": [], "statut": "active"}, metiers())
    import base64
    code = lien.split("#")[1]
    d = json.loads(base64.urlsafe_b64decode(code + "=" * (-len(code) % 4)))
    assert d["pro"] and d["cle"]["n"] == 2


def test_reessais_passagers_seulement(monkeypatch):
    import requests
    from radar import reprise
    appels = []

    def flaky():
        appels.append(1)
        if len(appels) < 3:
            raise requests.ConnectionError("coupure")
        return "ok"
    assert reprise.avec_reessais(flaky, "test", pauses=(0, 0)) == "ok" and len(appels) == 3
    refus = RuntimeError("400")
    refus.status_code = 400
    appels.clear()

    def mauvais():
        appels.append(1)
        raise refus
    try:
        reprise.avec_reessais(mauvais, "test", pauses=(0, 0))
    except RuntimeError:
        pass
    assert len(appels) == 1                                    # une erreur définitive n'est pas répétée


def test_resend_reessaie_sans_doublon(monkeypatch):
    from radar import mailer, reprise
    monkeypatch.setattr(reprise, "PAUSES", (0, 0))
    monkeypatch.setenv("RESEND_API_KEY", "re_x")
    monkeypatch.delenv("RADAR_DRY_RUN", raising=False)
    cles = []

    class R:
        def __init__(self, code):
            self.status_code, self.text = code, "x"

        def json(self):
            return {"id": "e1"}

    class S:
        def post(self, url, json=None, timeout=None, headers=None):
            cles.append(headers["Idempotency-Key"])
            return R(503 if len(cles) == 1 else 200)
    monkeypatch.setattr(mailer, "avec_reessais", lambda fn, quoi, pauses=None: reprise.avec_reessais(fn, quoi, pauses=(0, 0)))
    assert mailer.send("a@example.com", "Sujet", "<p>x</p>", "from@example.com", session=S()) == {"id": "e1"}
    assert len(cles) == 2 and cles[0] == cles[1]                 # même clé : Resend n'enverra pas deux fois


def test_collecte_anormale():
    import radar.cli as cli
    ok = [{"id": str(i), "objet": "x"} for i in range(1000)]
    assert cli.collecte_anormale(ok, [1900, 2000, 1800]) is None
    assert "seulement 100" in cli.collecte_anormale(ok[:100], [1900, 2000, 1800])
    assert "sans numéro" in cli.collecte_anormale([{"id": "1", "objet": ""}] * 10, [])
    assert cli.collecte_anormale(ok[:100], [1900]) is None           # trop peu d'historique pour juger


def test_daily_reporte_les_alertes_si_collecte_vide(tmp_path, monkeypatch):
    import radar.cli as cli
    monkeypatch.setattr(cli, "DATA", tmp_path)
    monkeypatch.setattr(cli, "OUT", tmp_path / "out")
    monkeypatch.setenv("RADAR_OUTBOX", str(tmp_path / "outbox"))
    monkeypatch.setattr(cli.mailer, "OUTBOX", tmp_path / "outbox")
    monkeypatch.setenv("OWNER_EMAIL", "owner@example.com")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    (tmp_path / "runs").mkdir()
    for d in ("2026-10-12", "2026-10-13", "2026-10-14"):
        (tmp_path / "runs" / f"{d}.json").write_text(json.dumps({"avis_recuperes": 1900}))
    monkeypatch.setattr(cli.boamp, "fetch_since", lambda since: [dict(a) for a in AVIS])   # quelques avis seulement
    subs = [{"email": "c@example.com", "plan": "essentiel", "statut": "trialing", "source": "stripe", "metiers": ["nettoyage"],
             "departements": ["33"], "mots_cles": []}]
    run = cli.run_daily(avis_open=[], subs=subs)
    assert run["collecte_anormale"] and run["emails_envoyes"] == 0
    assert not json.loads((tmp_path / "state.json").read_text())["sent"]   # rien marqué envoyé : rattrapé le lendemain


def test_ia_cache_cout_et_reessai(monkeypatch):
    from radar import ai
    monkeypatch.setattr(ai, "PAUSE", 0)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-x")
    appels = []

    class R:
        def __init__(self, txt):
            self.txt = txt

        def raise_for_status(self):
            pass

        def json(self):
            return {"content": [{"type": "text", "text": self.txt}], "usage": {"input_tokens": 1000, "output_tokens": 200}}

    class S:
        def post(self, url, timeout=None, headers=None, json=None):
            appels.append(1)
            if len(appels) == 1:
                return R("pas du json")                          # 1re réponse invalide : retentée
            return R('[{"id": "a1", "resume": "Ménage école", "score": 4, "a_verifier": ["Visite obligatoire"], "hors_metier": false}]')
    cache = {}
    avis = [{"id": "a1", "objet": "Nettoyage", "acheteur": "Ville", "departements": ["33"]}]
    st = ai.enrich(avis, session=S(), cache=cache)
    assert avis[0]["resume"] == "Ménage école" and avis[0]["a_verifier"] == ["Visite obligatoire"] and len(appels) == 2
    assert st["lots_ok"] == 1 and st["cout_usd"] == round(ai.cout_usd("claude-haiku-5-5", 2000, 400), 4)  # la réponse ratée est payée aussi
    autre = [{"id": "a1", "objet": "Nettoyage", "acheteur": "Ville", "departements": ["33"]}]
    st = ai.enrich(autre, session=S(), cache=cache)
    assert autre[0]["resume"] == "Ménage école" and st["depuis_cache"] == 1 and len(appels) == 2   # pas de nouvel appel payant
    st = ai.enrich([{"id": "b2", "objet": "x", "acheteur": "y", "departements": []}], session=S(), cache=cache, budget_usd=0)
    assert st["budget_atteint"] and len(appels) == 2


def test_veille_relance_si_pas_d_envoi(tmp_path, monkeypatch):
    import radar.cli as cli
    monkeypatch.setattr(cli, "DATA", tmp_path)
    monkeypatch.setattr(cli, "OUT", tmp_path / "out")
    monkeypatch.setattr(cli.mailer, "OUTBOX", tmp_path / "outbox")
    (tmp_path / "runs").mkdir()
    res = cli.run_veille()
    if date.today().weekday() < 5:
        assert res["relance"] and (tmp_path / "out" / "relancer").exists()
        (tmp_path / "runs" / f"{date.today().isoformat()}.json").write_text("{}")
        assert cli.run_veille()["ok"]


# ---------------------------------------------------------------- prix selon l'ancienneté et essai unique (radar/paliers.py)

def _biz_paliers(**extra):
    biz = business()
    for k, o in biz["offres"].items():
        o["stripe_paliers"] = [f"price_{k}_{i}" for i in range(3)]
    biz.update({"nouvelle_grille_depuis": "2026-10-19T00:00:00+00:00", "nouvelle_grille_anciens": False}, **extra)
    return biz


class FauxStripe:
    """Abonnements Stripe simulés : mise à jour des prix, fin d'essai, factures mensuelles au prix en cours."""
    def __init__(self, biz):
        self.subs, self.posts, self.factures = [], [], {}
        self.montants = {pid: round(p * 100) for o in biz["offres"].values()
                         for pid, (_, p) in zip(o["stripe_paliers"], __import__("radar.paliers").paliers.grille(o))}

    def ajouter(self, sid, debut, plan="essentiel", essai=7, email="a@x.fr", carte="fp_a", status=None, **kw):
        fin_essai = debut + essai * 86400 if essai else None
        pid = f"price_{plan}_0"
        s = {"id": sid, "status": status or ("trialing" if essai else "active"), "created": debut, "start_date": debut,
             "trial_start": debut if essai else None, "trial_end": fin_essai, "billing_cycle_anchor": fin_essai or debut,
             "current_period_end": fin_essai or self.mois(debut, 1), "metadata": {}, "cancel_at_period_end": False,
             "customer": {"id": f"cus_{sid}", "email": email},
             "default_payment_method": {"type": "card", "card": {"fingerprint": carte}},
             "items": {"data": [{"id": f"si_{sid}", "price": {"id": pid, "unit_amount": self.montants[pid], "recurring": {"interval": "month"}}}]}}
        s.update(kw)
        self.subs.append(s)
        self.factures[sid] = [] if essai else [self.montants[pid]]   # sans essai : 1er mois facturé à la création
        return s

    @staticmethod
    def mois(ts, n):
        from datetime import datetime, timezone
        d = datetime.fromtimestamp(ts, timezone.utc)
        m = d.month - 1 + n
        y, m = d.year + m // 12, m % 12 + 1
        import calendar
        return int(d.replace(year=y, month=m, day=min(d.day, calendar.monthrange(y, m)[1])).timestamp())

    def renouveler(self, s):
        """Fin de période : Stripe facture le prix en cours et passe à la période suivante."""
        s["status"] = "active"
        self.factures[s["id"]].append(s["items"]["data"][0]["price"]["unit_amount"])
        k = len(self.factures[s["id"]])
        s["current_period_end"] = self.mois(s["billing_cycle_anchor"], k)

    def call(self, method, path, data=None, params=None):
        if method == "GET":
            return {"data": self.subs, "has_more": False}
        self.posts.append((path, data))
        s = next(x for x in self.subs if f"subscriptions/{x['id']}" == path)
        if data.get("trial_end") == "now":
            s["trial_end"] = s["billing_cycle_anchor"] = self.maintenant
            self.renouveler(s)   # le 1er mois est facturé tout de suite
        elif "trial_end" in data:
            s["trial_end"] = s["billing_cycle_anchor"] = s["current_period_end"] = data["trial_end"]
        if "items[0][price]" in data:
            pid = data["items[0][price]"]
            s["items"]["data"][0]["price"] = {"id": pid, "unit_amount": self.montants[pid], "recurring": {"interval": "month"}}
        for k, v in data.items():
            if k.startswith("metadata["):
                s["metadata"][k[9:-1]] = v
        return dict(s)


T0 = 1792540800   # 2026-10-20 00:00 UTC, après nouvelle_grille_depuis


def test_grille_et_textes():
    from radar import paliers
    biz = business()
    assert paliers.texte_grille(biz["offres"]["essentiel"]) == "29 € les 2 premiers mois, 19,99 € du 3e au 5e mois, puis 9,99 € à partir du 6e mois"
    assert [paliers.prix_du_mois(biz["offres"]["pro"], n) for n in (1, 2, 3, 5, 6, 40)] == [49, 49, 39.99, 39.99, 29.99, 29.99]
    assert [paliers.centimes(p) for p in (19.99, 119.99, 9.99, 29.99)] == [1999, 11999, 999, 2999]   # pas d'erreur d'arrondi


def test_paliers_factures_exactes_sur_un_an(monkeypatch, tmp_path):
    """Essai 7 j, puis 29, 29, 19,99 x3, 9,99… à la date anniversaire, même avec un début un 31 et une ancienneté longue."""
    from radar import paliers
    monkeypatch.setenv("RADAR_DRY_RUN", "1")
    monkeypatch.setattr("radar.mailer.OUTBOX", tmp_path)
    for plan, attendu in (("essentiel", [2900, 2900, 1999, 1999, 1999] + [999] * 31),
                          ("pro", [4900, 4900, 3999, 3999, 3999] + [2999] * 31),
                          ("cle", [14900, 14900, 11999, 11999, 11999] + [9999] * 31)):
        biz = _biz_paliers()
        f = FauxStripe(biz)
        s = f.ajouter("sub_1", T0 + 11 * 86400, plan=plan)    # essai jusqu'au 7/11 ; une fin de mois testée plus bas
        dates = []
        for _ in range(36):
            r = paliers.traiter(biz, call=f.call, maintenant=s["current_period_end"] - 3600)
            assert not r["erreurs"]
            assert paliers.traiter(biz, call=f.call, maintenant=s["current_period_end"] - 60)["paliers"] == 0   # rien en double
            dates.append(s["current_period_end"])
            f.renouveler(s)
        assert f.factures["sub_1"] == attendu, (plan, f.factures["sub_1"])
        assert dates[0] == s["trial_end"] and all(FauxStripe.mois(s["billing_cycle_anchor"], i) == d for i, d in enumerate(dates))
        assert len(f.posts) == 2 and all(d["proration_behavior"] == "none" for _, d in f.posts)   # 2 changements de prix, sans prorata
    assert sum("baisse" in m.name for m in tmp_path.glob("*.html")) == 6   # client prévenu à chaque baisse
    f = FauxStripe(_biz_paliers())
    s = f.ajouter("sub_31", 1801958400, essai=0)   # abonnement sans essai démarré un 31 janvier
    for _ in range(8):
        paliers.traiter(_biz_paliers(), call=f.call, maintenant=s["current_period_end"] - 3600)
        f.renouveler(s)
    assert f.factures["sub_31"] == [2900, 2900, 1999, 1999, 1999, 999, 999, 999, 999]


def test_reabonnement_repart_au_prix_de_depart(monkeypatch, tmp_path):
    """Résilier puis revenir = nouvel abonnement = prix de départ ; changer de carte ne change rien."""
    from radar import paliers
    monkeypatch.setenv("RADAR_DRY_RUN", "1")
    monkeypatch.setattr("radar.mailer.OUTBOX", tmp_path)
    biz = _biz_paliers()
    f = FauxStripe(biz)
    ancien = f.ajouter("sub_old", T0, carte="fp_1", essai=7)
    for mois in range(4):   # 4 mois payés (carte changée au 3e), puis résiliation
        if mois == 2:
            ancien["default_payment_method"] = {"type": "card", "card": {"fingerprint": "fp_9"}}
        paliers.traiter(biz, call=f.call, maintenant=ancien["current_period_end"] - 3600)
        f.renouveler(ancien)
    assert f.factures["sub_old"] == [2900, 2900, 1999, 1999]
    ancien["status"] = "canceled"
    t1 = ancien["current_period_end"] + 20 * 86400
    retour = f.ajouter("sub_new", t1, carte="fp_1", essai=0)   # sans essai (anti_abus.py l'a arrêté)
    for _ in range(4):
        paliers.traiter(biz, call=f.call, maintenant=retour["current_period_end"] - 3600)
        f.renouveler(retour)
    assert f.factures["sub_new"] == [2900, 2900, 1999, 1999, 1999]
    paliers.traiter(biz, call=f.call, maintenant=retour["current_period_end"] - 3600)
    n = len(f.posts)
    paliers.traiter(biz, call=f.call, maintenant=retour["current_period_end"] - 60)
    assert len(f.posts) == n   # rien de refait au passage suivant


def test_essai_trop_long_et_exceptions(monkeypatch, tmp_path):
    from radar import paliers
    monkeypatch.setenv("RADAR_DRY_RUN", "1")
    monkeypatch.setattr("radar.mailer.OUTBOX", tmp_path)
    biz = _biz_paliers()
    f = FauxStripe(biz)
    long = f.ajouter("sub_long", T0, essai=14, carte="fp_l", email="l@x.fr")
    client = f.ajouter("sub_client", T0 - 4 * 86400, plan="pro", essai=14, carte="fp_c", email="c@x.fr")   # avant la nouvelle grille
    fin = f.ajouter("sub_fin", T0 - 90 * 86400, essai=0, carte="fp_f", email="f@x.fr", cancel_at_period_end=True)
    non = f.ajouter("sub_non", T0 - 90 * 86400, essai=0, carte="fp_n", email="n@x.fr")
    non["metadata"]["paliers"] = "non"
    autre = f.ajouter("sub_autre", T0, essai=0, carte="fp_o", email="o@x.fr")
    autre["items"]["data"][0]["price"] = {"id": "price_inconnu", "unit_amount": 1234, "recurring": {"interval": "month"}}
    r = paliers.traiter(biz, call=f.call, maintenant=T0 + 3600)
    assert r["essais_raccourcis"] == 1 and long["trial_end"] == T0 + 7 * 86400
    assert [p for p, _ in f.posts] == ["subscriptions/sub_long"]   # rien d'autre touché
    # Choix du propriétaire : l'ancien client garde ses 14 jours, puis suit la nouvelle grille
    biz["nouvelle_grille_anciens"] = True
    client["trial_end"] = client["billing_cycle_anchor"] = client["current_period_end"]
    for _ in range(3):
        paliers.traiter(biz, call=f.call, maintenant=client["current_period_end"] - 3600)
        f.renouveler(client)
    assert client["trial_start"] + 14 * 86400 == client["billing_cycle_anchor"]   # essai de 14 jours conservé
    assert f.factures["sub_client"] == [4900, 4900, 3999]
    assert paliers.traiter({**biz, "nouvelle_grille_depuis": ""}, call=lambda *a, **k: 1 / 0)["paliers"] == 0   # inactif avant stripe.yml
    # Les mentions affichées suivent la même grille
    assert paliers.tarif_abonne(biz, {"plan": "pro", "debut": T0}) == paliers.texte_grille(biz["offres"]["pro"])
    assert paliers.tarif_abonne({**biz, "nouvelle_grille_anciens": False}, {"plan": "pro", "debut": T0 - 86400 * 4, "prix_mois": 4900}) == "49 € par mois"


def test_annuel_economie_sur_grille_degressive():
    from radar import annuel
    o = {"nom": "Essentiel", "prix_mensuel": 29, "prix_annuel": 169,
         "paliers": [{"des_mois": 3, "prix": 19.99}, {"des_mois": 6, "prix": 9.99}]}
    assert annuel.mensualites(o)[:6] == [29, 29, 19.99, 19.99, 19.99, 9.99]
    assert annuel.cout_douze_mois(o) == 187.9 and annuel.economie(o) == 18.9
    assert annuel.euros(18.9) == "18,90" and annuel.euros(1199) == "1199"
    from datetime import date
    assert annuel.date_renouvellement(date(2028, 2, 29)) == date(2029, 2, 28)
    assert "19/10/2027" in annuel.texte_paiement({"nom": "Pro", "prix_annuel": 399}, date(2026, 10, 19))


def test_annuel_remplace_ancien_abonnement_sans_double_prelevement():
    from radar import annuel
    now = 1_800_000_000
    mois = {"id": "sub_m", "status": "active", "created": now - 10 * 86400, "latest_invoice": "in_m",
            "current_period_start": now - 10 * 86400, "current_period_end": now + 20 * 86400,
            "customer": {"email": "A@x.fr"}, "items": {"data": [{"price": {"product": "prod_pro", "recurring": {"interval": "month"}}}]}}
    an = {"id": "sub_a", "status": "active", "created": now - 60, "customer": {"email": "a@x.fr"},
          "items": {"data": [{"price": {"product": "prod_pro", "recurring": {"interval": "year"}}}]}}
    autre = {"id": "sub_b", "status": "trialing", "created": now, "customer": {"email": "b@x.fr"},
             "items": {"data": [{"price": {"product": "prod_pro", "recurring": {"interval": "month"}}}]}}
    appels = []

    class R:
        def __init__(self, body): self.status_code, self._b, self.text = 200, body, ""
        def json(self): return self._b

    class S:
        def request(self, method, url, data=None, params=None, **kw):
            path = url.split("/v1/")[1]
            appels.append((method, path, data, kw["headers"].get("Idempotency-Key")))
            if path == "products":
                return R({"data": [{"id": "prod_pro", "metadata": {"radar_offre": "pro"}}]})
            if path == "subscriptions":
                return R({"data": {"active": [mois, an], "trialing": [autre]}.get(params["status"], [])})
            if path == "invoices/in_m":
                return R({"status": "paid", "amount_paid": 4900, "charge": "ch_1"})
            if path == "refunds" and method == "GET":
                return R({"data": []})
            if path == "charges/ch_1":
                return R({"amount": 4900, "amount_refunded": 0})
            return R({"id": "ok"})
    import os
    os.environ["STRIPE_SECRET_KEY"] = "sk_test_x"
    try:
        res = annuel.remplacer_doublons(maintenant=now, session=S())
    finally:
        del os.environ["STRIPE_SECRET_KEY"]
    assert res["erreurs"] == [] and len(res["remplaces"]) == 1
    r = res["remplaces"][0]
    assert r["ancien"] == "sub_m" and r["nouveau"] == "sub_a" and r["rembourse"] == 32.66   # 20 jours sur 30
    rembourse = [a for a in appels if a[:2] == ("POST", "refunds")]
    assert rembourse[0][2]["amount"] == 3266 and rembourse[0][3] == "radar-doublon-sub_m"
    assert ("DELETE", "subscriptions/sub_m") in [a[:2] for a in appels]
    assert not any(a[1] in ("subscriptions/sub_a", "subscriptions/sub_b") for a in appels)   # le nouveau et l'autre client intacts
    assert annuel.part_non_utilisee(4900, 0, 100, 150) == 0 and annuel.part_non_utilisee(0, 0, 100, 50) == 0


def test_annuel_rappel_30_jours_et_echec_paiement():
    from radar import annuel
    now = 1_800_000_000
    base = {"email": "a@x.fr", "source": "stripe", "periode": "an", "statut": "active", "renouvellement": now + 29 * 86400}
    subs = [base, {**base, "email": "b@x.fr", "renouvellement": now + 60 * 86400},
            {**base, "email": "c@x.fr", "periode": "mois"}, {**base, "email": "d@x.fr", "statut": "past_due"},
            {**base, "email": "e@x.fr", "resilie": True}]
    out = annuel.a_prevenir(subs, set(), now)
    assert [(s["email"], g) for s, g, _ in out] == [("a@x.fr", "rappel"), ("d@x.fr", "echec")]
    assert annuel.a_prevenir(subs, {c for _, _, c in out}, now) == []


def test_point_du_jour_sans_nouveaute(monkeypatch, tmp_path):
    import radar.cli as cli
    from radar.config import business, metiers as load_metiers
    from radar.matching import Matcher
    biz, metiers = business(), load_metiers()
    envoyes = []
    monkeypatch.setattr(cli.mailer, "send", lambda to, sujet, html, *a, **k: envoyes.append((to, sujet, html)) or {"id": "x"})

    def avis(i, dep):
        return {"id": str(i), "objet": f"Nettoyage des locaux lot {i}", "acheteur": "Mairie", "departements": [dep],
                "descripteurs": ["Nettoyage"], "date_parution": "2099-01-01", "date_limite": f"2099-02-0{i}", "url": "https://x/",
                "nature": "", "type_marche": "", "texte": "", "pme": "", "metiers": ["nettoyage"]}
    ouverts, recents = [avis(1, "75"), avis(2, "75")], [avis(3, "92"), avis(4, "93"), avis(5, "92"), avis(6, "33")]
    subs = [{"email": "a@example.com", "plan": "pro", "metiers": ["nettoyage"], "departements": ["75"], "mots_cles": []},
            {"email": "b@example.com", "plan": "pro", "metiers": ["nettoyage"], "departements": ["92"], "mots_cles": []}]
    cle_b = cli.hashlib.sha256(b"b@example.com").hexdigest()[:16]
    state, run = {}, {"erreurs": []}
    cli.envoyer_rien_de_neuf(subs, {cle_b: None}, recents, ouverts, biz, metiers, Matcher(metiers), state, run)
    assert [e[0] for e in envoyes] == ["a@example.com"]           # b a reçu son alerte du jour : rien de plus
    to, sujet, html = envoyes[0]
    assert "2 encore ouverts" in sujet and "Nettoyage des locaux lot 1" in html
    assert "2 dans le 92" in html and "1 dans le 93" in html and "dans le 33" not in html
    cli.envoyer_rien_de_neuf(subs, {cle_b: None}, recents, ouverts, biz, metiers, Matcher(metiers), state, run)
    assert len(envoyes) == 1 and run["emails_rien_de_neuf"] == 1  # une seule fois par jour
