"""Un seul essai gratuit par carte ou par adresse (radar/anti_abus.py), Stripe simulé."""
from radar import anti_abus


class Rep:
    def __init__(self, body, code=200):
        self.body, self.status_code = body, code

    def json(self):
        return self.body

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests
            raise requests.HTTPError(response=self)


class FauxStripe:
    def __init__(self, subs, cartes, cartes_code=200):
        self.subs, self.cartes, self.cartes_code, self.posts = subs, cartes, cartes_code, []

    def get(self, url, params=None, auth=None, timeout=None):
        if url.endswith("/subscriptions"):
            return Rep({"data": self.subs, "has_more": False})
        cust = url.split("/customers/")[1].split("/")[0]
        return Rep({"data": [{"card": {"fingerprint": f}} for f in self.cartes.get(cust, [])], "has_more": False}, self.cartes_code)

    def post(self, url, data=None, auth=None, timeout=None):
        self.posts.append((url.rsplit("/", 1)[1], data))
        return Rep({})


class FauxMailer:
    def __init__(self):
        self.envois = []

    def send(self, to, sujet, html, frm, reply_to=None):
        self.envois.append((to, sujet, html))


BIZ = {"nom": "Radar Marchés", "site_url": "https://exemple.fr", "email_expediteur": "x@exemple.fr",
       "email_contact": "c@exemple.fr", "email_proprietaire": ""}


def sub(i, cust, email, status, trial_start, carte=None, meta=None):
    return {"id": f"sub_{i}", "status": status, "trial_start": trial_start, "metadata": meta or {},
            "customer": {"id": cust, "email": email},
            "default_payment_method": {"card": {"fingerprint": carte}} if carte else None}


def lancer(subs, cartes, monkeypatch, **kw):
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_x")
    st, m = FauxStripe(subs, cartes, **kw), FauxMailer()
    return anti_abus.appliquer(BIZ, mailer=m, session=st), st, m


def test_meme_carte_autre_adresse_essai_arrete(monkeypatch):
    subs = [sub(1, "cus_a", "a@x.fr", "canceled", 100, "fpA"), sub(2, "cus_b", "b@y.fr", "trialing", 200, "fpA")]
    res, st, m = lancer(subs, {"cus_a": ["fpA"], "cus_b": ["fpA"]}, monkeypatch)
    assert res["essais_refuses"] == 1 and st.posts[0][0] == "sub_2"
    assert st.posts[0][1]["trial_end"] == "now" and st.posts[0][1]["metadata[essai_refuse]"] == "carte"
    assert m.envois[0][0] == "b@y.fr" and "carte bancaire" in m.envois[0][2]


def test_resiliation_puis_reinscription_meme_adresse(monkeypatch):
    subs = [sub(1, "cus_a", "Jean@X.fr", "canceled", 100, "fpA"), sub(2, "cus_b", "jean@x.fr ", "trialing", 200, "fpB")]
    res, st, _ = lancer(subs, {"cus_a": ["fpA"], "cus_b": ["fpB"]}, monkeypatch)
    assert res["essais_refuses"] == 1 and st.posts[0][1]["metadata[essai_refuse]"] == "adresse"


def test_carte_d_un_ancien_abonnement_retrouvee_chez_le_client(monkeypatch):
    # l'ancien abonnement n'a plus de carte par défaut, mais la carte est encore rattachée au client
    subs = [sub(1, "cus_a", "a@x.fr", "canceled", 100), sub(2, "cus_b", "b@y.fr", "trialing", 200, "fpA")]
    res, _, _ = lancer(subs, {"cus_a": ["fpA"], "cus_b": ["fpA"]}, monkeypatch)
    assert res["essais_refuses"] == 1


def test_premier_essai_et_client_different_intacts(monkeypatch):
    subs = [sub(1, "cus_a", "a@x.fr", "trialing", 100, "fpA"), sub(2, "cus_b", "b@y.fr", "trialing", 200, "fpB"),
            sub(3, "cus_c", "c@z.fr", "active", None, "fpC")]
    res, st, m = lancer(subs, {"cus_a": ["fpA"], "cus_b": ["fpB"], "cus_c": ["fpC"]}, monkeypatch)
    assert res == {"essais_verifies": 2, "essais_refuses": 0, "erreurs": []} and not st.posts and not m.envois


def test_abonnement_sans_essai_puis_essai_meme_carte_autorise(monkeypatch):
    # un ancien abonnement payé sans essai (ex. essai déjà refusé n'existe pas ici) ne compte pas comme essai
    subs = [sub(1, "cus_a", "a@x.fr", "active", None, "fpA"), sub(2, "cus_b", "b@y.fr", "trialing", 200, "fpA")]
    res, _, _ = lancer(subs, {"cus_a": ["fpA"], "cus_b": ["fpA"]}, monkeypatch)
    assert res["essais_refuses"] == 0


def test_deja_traite_et_troisieme_tentative(monkeypatch):
    subs = [sub(1, "cus_a", "a@x.fr", "canceled", 100, "fpA"),
            sub(2, "cus_b", "b@y.fr", "active", 200, "fpA", {"essai_refuse": "carte"}),
            sub(3, "cus_c", "c@z.fr", "trialing", 300, "fpA")]
    res, st, _ = lancer(subs, {"cus_a": ["fpA"], "cus_b": ["fpA"], "cus_c": ["fpA"]}, monkeypatch)
    assert [p[0] for p in st.posts] == ["sub_3"]


def test_cartes_illisibles_garde_la_carte_de_l_abonnement(monkeypatch):
    subs = [sub(1, "cus_a", "a@x.fr", "canceled", 100, "fpA"), sub(2, "cus_b", "b@y.fr", "trialing", 200, "fpA")]
    res, _, _ = lancer(subs, {}, monkeypatch, cartes_code=403)
    assert res["essais_refuses"] == 1 and res["erreurs"] == ["cartes illisibles (403)"]


def test_sans_cle_rien(monkeypatch):
    monkeypatch.delenv("STRIPE_SECRET_KEY", raising=False)
    assert anti_abus.appliquer(BIZ)["essais_verifies"] == 0
