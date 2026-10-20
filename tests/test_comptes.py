"""Comptes clients (radar/comptes.py) : synchronisation Stripe, critères, historique, favoris, demandes.
La base Supabase est remplacée par une imitation en mémoire ; les règles SQL sont testées dans test_comptes_sql.py."""
import json
import os
import re
import subprocess
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from radar import cli, comptes, comptes_setup
from radar.config import business, metiers as load_metiers

ROOT = Path(__file__).resolve().parents[1]
MAINTENANT = datetime(2026, 10, 19, 8, 0, tzinfo=timezone.utc)


class FausseBase:
    def __init__(self, users=()):
        self.t = {k: [] for k in ("offres", "offres_prix", "metiers", "comptes", "criteres", "alertes", "demandes")}
        self.users, self.fichiers, self.ecritures = list(users), {}, 0

    @staticmethod
    def _ok(row, filtres):
        for k, f in filtres.items():
            if k in ("select", "order", "limit"):
                continue
            if k == "and":
                for cond in f.strip("()").split(","):
                    col, op, val = cond.split(".", 2)
                    if not FausseBase._ok(row, {col: f"{op}.{val}"}):
                        return False
                continue
            op, val = f.split(".", 1)
            v = row.get(k)
            if op == "not":
                if v is None:
                    return False
                continue
            if op == "eq" and str(v) != val or op == "is" and v is not {"true": True, "null": None}[val] \
               or op == "lt" and not (v is not None and str(v) < val) or op == "gte" and not (v is not None and str(v) >= val) \
               or op == "lte" and not (v is not None and str(v) <= val):
                return False
        return True

    def select(self, table, **f):
        rows = [dict(r) for r in self.t[table] if self._ok(r, f)]
        return rows[: int(f["limit"])] if "limit" in f else rows

    def upsert(self, table, rows, conflit=None, ignorer=False):
        cles = (conflit or "id").split(",")
        for r in rows:
            self.ecritures += 1
            old = next((x for x in self.t[table] if all(x.get(k) == r.get(k) for k in cles)), None)
            if old and not ignorer:
                old.update(r)
            elif not old:
                self.t[table].append({"id": len(self.t[table]) + 1, "favori": False, **dict(r)} if table == "alertes" else dict(r))

    def update(self, table, valeurs, **f):
        rows = [r for r in self.t[table] if self._ok(r, f)]
        for r in rows:
            r.update(valeurs)
        return [dict(r) for r in rows]

    def delete(self, table, **f):
        self.t[table] = [r for r in self.t[table] if not self._ok(r, f)]

    def utilisateurs(self):
        return self.users

    def deposer(self, chemin, contenu, type_mime):
        self.fichiers[chemin] = contenu

    def lire(self, chemin):
        return self.fichiers[chemin]

    def lister(self, prefixe):
        return [{"name": k[len(prefixe) + 1:], "id": k} for k in self.fichiers if k.startswith(prefixe + "/")]

    def effacer(self, chemins):
        for c in chemins:
            self.fichiers.pop(c, None)


def _u(uid, email, verifie=True):
    return {"id": uid, "email": email, "email_confirmed_at": "2026-10-17T10:00:00Z" if verifie else None}


def _s(email, plan="pro", statut="active", sub="sub_1", metier="nettoyage", deps=("75",), mots=(), debut=1):
    return {"email": email, "plan": plan, "statut": statut, "stripe_subscription": sub, "customer": "cus_" + sub,
            "metiers": [metier], "departements": list(deps), "mots_cles": list(mots), "source": "stripe", "debut": debut,
            "fin_essai": 1792400000 if statut == "trialing" else None}


@pytest.fixture
def biz():
    return business()


@pytest.fixture
def metiers():
    return load_metiers()


@pytest.fixture(autouse=True)
def hors_ligne(monkeypatch, tmp_path):
    monkeypatch.setenv("RADAR_DRY_RUN", "1")
    monkeypatch.setattr("radar.mailer.OUTBOX", tmp_path / "outbox")


def test_liaison_seulement_adresse_verifiee(biz, metiers):
    db = FausseBase([_u("u1", "Client@Exemple.fr"), _u("u2", "pirate@exemple.fr", verifie=False), _u("u3", "seul@exemple.fr")])
    subs = [_s("client@exemple.fr"), _s("pirate@exemple.fr", sub="sub_2")]
    st = comptes.synchroniser(db, subs, biz, metiers, MAINTENANT)
    par = {c["user_id"]: c for c in db.t["comptes"]}
    assert par["u1"]["offre"] == "pro" and par["u1"]["statut"] == "active" and par["u1"]["stripe_subscription"] == "sub_1"
    assert "u2" not in par                                   # adresse non vérifiée : jamais reliée
    assert par["u3"]["statut"] == "aucun" and par["u3"]["offre"] is None
    assert st["relies"] == 1 and st["criteres_importes"] == 1
    assert db.t["criteres"][0]["departements"] == ["75"] and db.t["criteres"][0]["pousse_stripe"]
    assert {o["cle"] for o in db.t["offres"]} == set(biz["offres"])
    n = db.ecritures
    comptes.synchroniser(db, subs, biz, metiers, MAINTENANT)  # rien n'a changé : aucune réécriture de compte
    assert db.ecritures - n == len(db.t["offres"]) + len(db.t["offres_prix"]) + len(db.t["metiers"])


def test_resiliation_et_doublon_stripe(biz, metiers):
    db = FausseBase([_u("u1", "a@exemple.fr")])
    # deux abonnements pour la même adresse (nouvelle tentative de paiement) : l'actif l'emporte
    comptes.synchroniser(db, [_s("a@exemple.fr", plan="essentiel", statut="trialing", sub="sub_old", debut=1),
                              _s("a@exemple.fr", plan="pro", statut="active", sub="sub_new", debut=2)], biz, metiers, MAINTENANT)
    assert db.t["comptes"][0]["stripe_subscription"] == "sub_new"
    comptes.synchroniser(db, [], biz, metiers, MAINTENANT)    # résilié : plus dans les abonnements actifs
    c = db.t["comptes"][0]
    assert c["statut"] == "inactif" and c["offre"] == "pro" and not comptes.droits(biz, c["offre"], c["statut"])["veille"]


def test_droits_par_offre(biz):
    d = {o: comptes.droits(biz, o, "active") for o in biz["offres"]}
    assert d["essentiel"]["departements_max"] == 1 and not d["essentiel"]["mots_cles"]
    assert d["pro"]["mots_cles"] and d["pro"]["rapports_mois"] == 0 and d["pro"]["dossiers_mois"] == 0
    assert d["cle"]["rapports_mois"] == 20 and d["cle"]["dossiers_mois"] == 2
    assert comptes.droits(biz, "cle", "trialing")["dossiers_mois"] == 0          # dossiers inclus dès le 1er paiement
    assert comptes.droits(biz, "cle", "trialing")["rapports_mois"] == 20
    assert not comptes.droits(biz, "pro", "canceled")["veille"]
    assert not comptes.droits(biz, "inconnue", "active")["veille"]


def test_criteres_espace_vers_stripe(biz, metiers):
    db = FausseBase([_u("u1", "a@exemple.fr"), _u("u2", "b@exemple.fr")])
    subs = [_s("a@exemple.fr", plan="pro", sub="sub_a"), _s("b@exemple.fr", plan="essentiel", sub="sub_b")]
    comptes.synchroniser(db, subs, biz, metiers, MAINTENANT)
    plus_tard = (MAINTENANT + timedelta(minutes=5)).isoformat()
    db.update("criteres", {"metier": "peinture", "departements": ["33", "47"], "mots_cles": ["façade"], "maj": plus_tard}, user_id="eq.u1")
    # essentiel avec 2 départements : refusé par la base, et de nouveau par le robot (défense en profondeur)
    db.update("criteres", {"departements": ["33", "47"], "maj": plus_tard}, user_id="eq.u2")
    pousses = []
    n, errs = comptes.pousser_criteres(db, subs, biz, metiers, cli.criteres_txt, lambda sid, meta: pousses.append((sid, meta)),
                                       MAINTENANT + timedelta(minutes=10))
    assert n == 1 and len(errs) == 1 and "hors offre" in errs[0]
    assert pousses == [("sub_a", {"metier": "peinture", "departements": "33, 47", "motscles": "façade"})]
    par = {c["user_id"]: c for c in db.t["criteres"]}
    assert par["u1"]["pousse_stripe"] > par["u1"]["maj"] and par["u2"]["erreur"]
    # la synchro suivante ne réécrase pas une demande en attente, et ne repousse pas ce qui est déjà appliqué
    comptes.synchroniser(db, subs, biz, metiers, MAINTENANT + timedelta(minutes=11))
    assert par["u2"]["departements"] == ["33", "47"]
    n, _ = comptes.pousser_criteres(db, subs, biz, metiers, cli.criteres_txt, lambda *a: pousses.append(a))
    assert len(pousses) == 1


def test_critere_modifie_pendant_la_copie(biz, metiers):
    db = FausseBase([_u("u1", "a@exemple.fr")])
    subs = [_s("a@exemple.fr", sub="sub_a")]
    comptes.synchroniser(db, subs, biz, metiers, MAINTENANT)
    db.update("criteres", {"departements": ["33"], "maj": "2026-10-19T08:05:00+00:00"}, user_id="eq.u1")

    def stripe(sid, meta):          # le client modifie encore pendant l'appel à Stripe
        db.update("criteres", {"departements": ["47"], "maj": "2026-10-19T08:06:00+00:00"}, user_id="eq.u1")
    comptes.pousser_criteres(db, subs, biz, metiers, cli.criteres_txt, stripe, MAINTENANT + timedelta(minutes=10))
    c = db.t["criteres"][0]
    assert c["departements"] == ["47"] and c["pousse_stripe"] < "2026-10-19T08:06"   # la nouvelle version repartira


def test_historique_et_rappels_favoris(biz):
    db = FausseBase()
    avis = [{"id": "26-1", "objet": "Ménage", "acheteur": "Mairie", "ville": "Paris", "date_limite": "2026-10-21T12:00",
             "url": "https://boamp.fr/26-1", "resume": "Ménage des écoles"},
            {"id": "26-2", "objet": "Vitrerie", "date_limite": "", "departements": ["75"]}]
    comptes.enregistrer_alertes(db, "A@exemple.fr", avis, date(2026, 10, 19))
    comptes.enregistrer_alertes(db, "a@exemple.fr", avis, date(2026, 10, 20))     # renvoyé : pas de doublon
    assert len(db.t["alertes"]) == 2 and db.t["alertes"][0]["recu"] == "2026-10-19"
    assert db.t["alertes"][1]["date_limite"] is None and db.t["alertes"][1]["lieu"] == "75"
    db.t["alertes"][0]["favori"] = True
    subs = [_s("a@exemple.fr")]
    assert comptes.rappels_favoris(db, biz, subs, date(2026, 10, 17)) == (0, [])     # à 4 jours : pas encore
    assert comptes.rappels_favoris(db, biz, subs, date(2026, 10, 19))[0] == 1
    assert comptes.rappels_favoris(db, biz, subs, date(2026, 10, 20))[0] == 0        # une seule fois
    db.t["alertes"][0]["rappel_envoye"] = None
    assert comptes.rappels_favoris(db, biz, [], date(2026, 10, 19))[0] == 0          # abonnement terminé : rien


def _demande(db, uid, typ, ref="26-123456", statut="recue"):
    d = {"id": f"d{len(db.t['demandes']) + 1}", "user_id": uid, "type": typ, "avis_ref": ref, "infos": "SARL Test",
         "statut": statut, "essais": 0, "cree": "2026-10-19T07:00:00+00:00", "maj": "2026-10-19T07:00:00+00:00"}
    db.t["demandes"].append(d)
    return d


def _cle(db, statut="active"):
    db.t["comptes"].append({"user_id": "u1", "email": "cle@exemple.fr", "offre": "cle", "statut": statut})


def test_demandes_succes_echec_et_reprise(biz, metiers):
    db = FausseBase()
    _cle(db)
    ok, ko = _demande(db, "u1", "rapport"), _demande(db, "u1", "rapport", ref="26-000001")
    appels = []

    def rapport(d, c):
        appels.append(d["id"])
        if d["avis_ref"] == "26-000001":
            raise RuntimeError("Claude indisponible")
        return f"u1/{d['id']}/resultat/r.html", None
    n, errs = comptes.traiter_demandes(db, biz, metiers, rapport, None, MAINTENANT)
    assert n == 1 and ok["statut"] == "terminee" and ok["fichier"] and ok["termine"]
    assert ko["statut"] == "recue" and ko["essais"] == 1 and errs        # 1er échec : nouvel essai au passage suivant
    comptes.traiter_demandes(db, biz, metiers, rapport, None, MAINTENANT)
    assert ko["statut"] == "echec" and "pas décompté" in ko["message"]
    assert appels.count(ok["id"]) == 1                                   # jamais refait une fois terminé


def test_demande_bloquee_si_abonnement_change(biz, metiers):
    db = FausseBase()
    _cle(db, statut="inactif")
    d = _demande(db, "u1", "rapport")
    comptes.traiter_demandes(db, biz, metiers, lambda *a: pytest.fail("ne doit pas appeler l'IA"), None, MAINTENANT)
    assert d["statut"] == "echec"


def test_demande_restee_en_traitement_reprise(biz, metiers):
    db = FausseBase()
    _cle(db)
    d = _demande(db, "u1", "rapport", statut="en_traitement")
    d["maj"] = "2026-10-19T05:00:00+00:00"                                 # arrêt brutal il y a 3 h
    comptes.traiter_demandes(db, biz, metiers, lambda dem, c: ("x", None), None, MAINTENANT)
    assert d["statut"] == "terminee" and d["essais"] == 1


def test_demandes_espace_bout_en_bout(biz, metiers, monkeypatch):
    biz["cle_actif"] = True
    db = FausseBase()
    _cle(db)
    r = _demande(db, "u1", "rapport")
    dos = _demande(db, "u1", "dossier", ref="26-222222")
    court = _demande(db, "u1", "dossier", ref="26-333333")
    db.fichiers[f"u1/{dos['id']}/dce/rc.txt"] = ("Règlement de consultation. " * 200).encode()
    db.fichiers[f"u1/{court['id']}/dce/rc.txt"] = b"trop court"
    avis = {"id": "26-123456", "objet": "Nettoyage des écoles", "acheteur": "Ville", "metiers": ["nettoyage"], "texte": ""}
    faux_rapport = {"en_bref": "x", "chances": "bonnes", "pourquoi_chances": "y", "criteres": [], "prix": {}, "pieces": [],
                    "pieges": [], "plan": [], "questions": []}
    faux_dossier = {"objet": "Nettoyage", "date_limite": (date.today() + timedelta(days=20)).isoformat(), "memoire": [],
                    "criteres": [], "pieces": [], "vigilance": [], "lettre": "", "dume": [], "references": []}
    cles = [_s("cle@exemple.fr", plan="cle")]
    n, errs = cli.demandes_espace(biz, metiers, cles, None, supa=db, avis_fn=lambda ref: avis,
                                  generer_rapport=lambda *a: faux_rapport, generer_dossier=lambda dce, infos: faux_dossier)
    assert not errs, errs
    assert r["statut"] == "terminee" and b"<!doctype html>" in db.fichiers[r["fichier"]][:50]
    assert dos["statut"] == "terminee" and dos["fichier"].endswith(".docx") and db.fichiers[dos["fichier"]][:2] == b"PK"
    assert f"u1/{dos['id']}/dce/rc.txt" not in db.fichiers                  # pièces du client effacées après usage
    assert court["statut"] == "echec" and "Non décompté" in court["message"]


def test_config_auth_securisee(biz):
    c = comptes_setup.config_auth(biz, "re_secret")
    assert c["mailer_autoconfirm"] is False and c["password_min_length"] >= 10 and c["mailer_secure_email_change_enabled"]
    assert c["mailer_otp_exp"] <= 3600 and c["uri_allow_list"].startswith(biz["site_url"])
    for cas in ("confirmation", "recovery", "email_change"):
        assert "{{ .TokenHash }}" in c[f"mailer_templates_{cas}_content"]
        assert "{{ .ConfirmationURL }}" not in c[f"mailer_templates_{cas}_content"]


def test_inactif_sans_configuration(monkeypatch):
    for k in ("SUPABASE_URL", "SUPABASE_SERVICE_KEY", "SUPABASE_ACCESS_TOKEN"):
        monkeypatch.delenv(k, raising=False)
    assert comptes.connexion() is None
    assert cli.comptes_espace(business(), load_metiers(), [], {"erreurs": []}) is None


def test_aucun_secret_ni_donnee_client_dans_le_depot():
    fichiers = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True).stdout.split()
    motifs = re.compile(r"(sk_live_[A-Za-z0-9]{10}|rk_live_[A-Za-z0-9]{10}|sk_test_[A-Za-z0-9]{10}|sb_secret_|sbp_[a-f0-9]{20}|whsec_[A-Za-z0-9]{10}|"
                        r"re_[A-Za-z0-9]{8}_[A-Za-z0-9]{10}|eyJhbGciOi[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]{20,})")
    trouves = []
    for f in fichiers:
        p = ROOT / f
        if f == "tests/test_comptes.py":
            continue
        if p.suffix in (".png", ".mp3", ".mp4", ".pyc", ".woff2", ".ico") or not p.is_file() or p.stat().st_size > 2_000_000:
            continue
        if motifs.search(p.read_text(encoding="utf-8", errors="ignore")):
            trouves.append(f)
    assert not trouves, trouves
    conf = ROOT / "config" / "comptes.json"
    if conf.exists():                                # seule la clé PUBLIQUE peut y être
        d = json.loads(conf.read_text())
        assert set(d) <= {"url", "cle"} and not d["cle"].startswith("sb_secret_")
        assert "service_role" not in d["cle"]


def test_invitation_unique_des_clients_existants(biz):
    state = {}
    subs = [_s("a@exemple.fr"), _s("b@exemple.fr", sub="sub_b"), _s("c@exemple.fr", statut="canceled", sub="sub_c")]
    users = [_u("u1", "B@exemple.fr")]
    os.environ["RADAR_DEMO"] = "1"
    try:
        assert comptes.inviter(subs, biz, users, state) == (1, [])      # a seulement : b a déjà un compte, c est résilié
        assert comptes.inviter(subs, biz, users, state) == (0, [])      # jamais deux fois
    finally:
        del os.environ["RADAR_DEMO"]


def test_pages_compte_generees_et_privees(biz, metiers, tmp_path, monkeypatch):
    from radar import site
    from radar.matching import Matcher
    monkeypatch.setattr(comptes, "config_publique", lambda: {"url": "https://abc.supabase.co", "cle": "sb_publishable_x"})
    site.build([], biz, metiers, Matcher(metiers), tmp_path / "site")
    for rel in ("compte/index.html", "compte/inscription/index.html", "compte/oubli/index.html", "compte/confirmation/index.html"):
        html = (tmp_path / "site" / rel).read_text()
        assert 'name="robots" content="noindex"' in html and "Content-Security-Policy" in html and "sb_publishable_x" in html
    assert "/compte/" not in (tmp_path / "site" / "sitemap.xml").read_text()
    assert (tmp_path / "site" / "static" / "vendor" / "supabase-2.99.3.js").exists()
    assert 'id="ap-data"' in (tmp_path / "site" / "index.html").read_text()      # outil de découverte
    monkeypatch.setattr(comptes, "config_publique", lambda: None)
    site.build([], biz, metiers, Matcher(metiers), tmp_path / "site2")
    assert not (tmp_path / "site2" / "compte" / "inscription").exists()        # sans configuration : ancien espace seul
    assert "ancienEspace(false)" in (tmp_path / "site2" / "compte" / "index.html").read_text()


def test_menage_conservation():
    db = FausseBase()
    db.t["alertes"] = [{"id": 1, "recu": "2025-01-01"}, {"id": 2, "recu": "2026-10-01"}]
    db.t["demandes"] = [{"id": "a", "fichier": "u/a/resultat/x.docx", "termine": "2026-01-01T00:00:00+00:00"},
                        {"id": "b", "fichier": "u/b/resultat/y.docx", "termine": "2026-10-01T00:00:00+00:00"}]
    db.fichiers = {"u/a/resultat/x.docx": b"x", "u/b/resultat/y.docx": b"y"}
    comptes.menage(db, date(2026, 10, 19))
    assert [a["id"] for a in db.t["alertes"]] == [2]
    assert list(db.fichiers) == ["u/b/resultat/y.docx"] and db.t["demandes"][0]["fichier"] is None


def test_acces_offert_au_proprietaire(biz, metiers, monkeypatch):
    from radar import subscribers
    monkeypatch.delenv("OWNER_EMAIL", raising=False)
    assert subscribers.from_proprietaire(biz) == []                  # sans secret : aucun accès offert
    monkeypatch.setenv("OWNER_EMAIL", "Patron@Exemple.fr")
    subs = subscribers.from_proprietaire(biz)
    assert subs[0]["plan"] == "cle" and subs[0]["statut"] == "active" and subs[0]["source"] == "interne"
    assert "stripe_subscription" not in subs[0]                       # aucun abonnement ni prélèvement Stripe
    db = FausseBase([_u("u1", "patron@exemple.fr")])
    comptes.synchroniser(db, subs, biz, metiers, MAINTENANT)
    c = db.t["comptes"][0]
    assert c["offre"] == "cle" and c["source"] == "interne" and comptes.droits(biz, c["offre"], c["statut"])["dossiers_mois"] == 2
    assert db.t["criteres"][0]["metier"] == "nettoyage"
    # critères changés dans l'espace : appliqués sans Stripe, sans erreur, et jamais écrasés par les réglages
    db.update("criteres", {"metier": "peinture", "departements": ["33"], "maj": (MAINTENANT + timedelta(minutes=5)).isoformat()},
              user_id="eq.u1")
    n, errs = comptes.pousser_criteres(db, subs, biz, metiers, cli.criteres_txt, lambda *a: pytest.fail("Stripe appelé"),
                                       MAINTENANT + timedelta(minutes=10))
    assert not errs and subs[0]["metiers"] == ["peinture"] and subs[0]["departements"] == ["33"]
    frais = subscribers.from_proprietaire(biz)
    comptes.synchroniser(db, frais, biz, metiers, MAINTENANT + timedelta(minutes=11))
    assert db.t["criteres"][0]["metier"] == "peinture"


def test_affichage_abonnement_seulement_si_base_a_jour(biz, metiers):
    db = FausseBase([_u("u1", "a@exemple.fr")])
    s = {**_s("a@exemple.fr"), "prix_mois": 4900, "periode": "mois", "renouvellement": 1793000000, "resilie": False}
    comptes.synchroniser(db, [s], biz, metiers, MAINTENANT)
    assert "renouvellement" not in db.t["comptes"][0]          # ancienne base : colonnes absentes, rien d'écrit
    db.t["comptes"][0]["renouvellement"] = None                 # schema.sql appliqué : la colonne existe
    comptes.synchroniser(db, [s], biz, metiers, MAINTENANT)
    c = db.t["comptes"][0]
    assert c["prix_cents"] == 4900 and c["periode"] == "mois" and c["renouvellement"].startswith("2026-10-26") and c["resilie"] is False


def test_acces_offerts_par_secret(biz, monkeypatch):
    from radar import subscribers
    monkeypatch.delenv("OWNER_EMAIL", raising=False)
    monkeypatch.setenv("ACCES_OFFERTS", " Client@Exemple.fr, autre@exemple.fr ;pas-une-adresse")
    subs = subscribers.from_proprietaire(biz)
    assert [s["email"] for s in subs] == ["Client@Exemple.fr", "autre@exemple.fr"]
    assert all(s["plan"] == "cle" and s["source"] == "interne" and "stripe_subscription" not in s for s in subs)
    monkeypatch.setenv("OWNER_EMAIL", "patron@exemple.fr")
    assert len(subscribers.from_proprietaire(biz)) == 3


def test_marches_de_depart_nouvel_espace(metiers):
    from radar.matching import Matcher
    db = FausseBase(users=[{"id": "u1", "email": "Neuf@exemple.fr", "email_confirmed_at": "x"},
                           {"id": "u2", "email": "ancien@exemple.fr", "email_confirmed_at": "x"},
                           {"id": "u3", "email": "pasconfirme@exemple.fr", "email_confirmed_at": None}])
    comptes.enregistrer_alertes(db, "ancien@exemple.fr", [{"id": "26-9", "objet": "Ménage"}], date(2026, 10, 1))
    subs = [_s("neuf@exemple.fr"), _s("ancien@exemple.fr", sub="sub_2"), _s("pasconfirme@exemple.fr", sub="sub_3")]

    def avis(i, dl, parution, dep="75", objet="Nettoyage des locaux de la mairie"):
        return {"id": f"26-{i}", "objet": objet, "descripteurs": [], "departements": [dep], "date_limite": dl,
                "date_parution": parution, "nature": "Avis de marché", "acheteur": "Mairie", "ville": "Paris"}
    liste = [avis(1, "2026-11-20", "2026-10-15"), avis(2, "2026-11-10", "2026-10-19"), avis(3, "2026-10-22", "2026-10-20"),
             avis(4, "2026-11-05", "2026-10-18"), avis(5, "2026-11-30", "2026-10-17"), avis(6, "2026-11-30", "2026-10-20", dep="33"),
             avis(7, "2026-11-30", "2026-10-20", objet="Travaux de voirie")]
    appels, deja = [], set()

    def charger():
        appels.append(1)
        return liste
    faits = comptes.marches_de_depart(db, subs, db.utilisateurs(), charger, Matcher(metiers), metiers, deja, date(2026, 10, 20))
    # Les 3 plus récents de sa veille, avec au moins 7 jours pour répondre (26-3 trop proche, 33 et voirie hors veille)
    assert [a["id"] for a in faits["neuf@exemple.fr"]] == ["26-2", "26-4", "26-5"] and list(faits) == ["neuf@exemple.fr"]
    neuf = [r for r in db.t["alertes"] if r["email"] == "neuf@exemple.fr"]
    assert len(neuf) == 3 and neuf[0]["recu"] == "2026-10-20" and neuf[0]["metier"]
    assert len(deja) == 2 and not any("@" in d for d in deja)          # empreintes seulement (state.json public)
    # Deuxième passage : rien à refaire, et pas d'appel BOAMP
    assert comptes.marches_de_depart(db, subs, db.utilisateurs(), charger, Matcher(metiers), metiers, deja, date(2026, 10, 20)) == {}
    assert len(appels) == 1
