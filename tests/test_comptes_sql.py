"""Tests de la base privée (supabase/schema.sql) sur un vrai PostgreSQL local : RLS, isolation entre clients,
droits par offre, quotas, double clic, webhook rejoué. Ignorés si PostgreSQL n'est pas installé."""
import glob
import json
import os
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path

import pytest

from radar import comptes
from radar.config import business

ROOT = Path(__file__).resolve().parents[1]
BIN = sorted(glob.glob("/usr/lib/postgresql/*/bin"))
pytestmark = pytest.mark.skipif(not BIN, reason="PostgreSQL non installé")

U = {k: str(uuid.uuid4()) for k in ("ess", "pro", "cle", "cle_essai", "sans", "non_verifie", "annule")}


def _cmd(args):
    return (["runuser", "-u", "postgres", "--"] + args) if os.geteuid() == 0 else args


class PG:
    def __init__(self):
        self.dir = tempfile.mkdtemp(prefix="radar-pg-")
        if os.geteuid() == 0:
            shutil.chown(self.dir, "postgres")
        b = BIN[-1]
        subprocess.run(_cmd([f"{b}/initdb", "-A", "trust", "-U", "postgres", "-D", f"{self.dir}/data"]),
                       check=True, capture_output=True)
        subprocess.run(_cmd([f"{b}/pg_ctl", "-D", f"{self.dir}/data", "-w", "-l", f"{self.dir}/log",
                             "-o", f"-k {self.dir} -p 54329 -c listen_addresses=''", "start"]), check=True, capture_output=True)
        self.bin = b

    def stop(self):
        subprocess.run(_cmd([f"{self.bin}/pg_ctl", "-D", f"{self.dir}/data", "-m", "immediate", "stop"]), capture_output=True)
        shutil.rmtree(self.dir, ignore_errors=True)

    def q(self, sql, uid=None, role=None):
        """Exécute sql (en tant que client uid si donné) ; renvoie la sortie, lève RuntimeError avec le message d'erreur."""
        pre = "begin;\n"
        if uid:
            pre += f"set local role authenticated;\nset local request.jwt.claim.sub = '{uid}';\n"
        elif role:
            pre += f"set local role {role};\n"
        r = subprocess.run(_cmd([f"{self.bin}/psql", "-h", self.dir, "-p", "54329", "-U", "postgres", "-d", "postgres",
                                 "-v", "ON_ERROR_STOP=1", "-At", "-q"]), input=pre + sql.rstrip().rstrip(";") + ";\ncommit;\n",
                           capture_output=True, text=True)
        if r.returncode:
            raise RuntimeError(r.stderr.strip())
        return r.stdout.strip()


@pytest.fixture(scope="module")
def pg():
    db = PG()
    try:
        db.q((ROOT / "tests" / "supabase_stub.sql").read_text())
        schema = (ROOT / "supabase" / "schema.sql").read_text()
        db.q(schema)
        db.q(schema)                                   # rejouable sans erreur
        biz = business()
        rows = comptes.offres_sql(biz)
        sql = "".join(f"insert into public.offres values ('{o['cle']}', '{o['nom'].replace(chr(39), chr(39) * 2)}', "
                      f"{o['departements_max']}, {o['mots_cles']}, {o['historique_complet']}, {o['rapports_mois']}, "
                      f"{o['dossiers_mois']}, {o['rapports_en_essai']});\n" for o in rows)
        sql += "insert into public.offres_prix values ('price_pro', 'pro'), ('price_ess', 'essentiel');\n"
        sql += "insert into public.metiers values ('nettoyage', 'Nettoyage'), ('peinture', 'Peinture');\n"
        for k, u in U.items():
            conf = "null" if k == "non_verifie" else "now()"
            sql += f"insert into auth.users values ('{u}', '{k}@exemple.fr', {conf});\n"
        for k, offre, statut in (("ess", "essentiel", "active"), ("pro", "pro", "trialing"), ("cle", "cle", "active"),
                                 ("cle_essai", "cle", "trialing"), ("annule", "pro", "inactif")):
            sql += (f"insert into public.comptes (user_id, email, offre, statut, stripe_subscription) values "
                    f"('{U[k]}', '{k}@exemple.fr', '{offre}', '{statut}', 'sub_{k}');\n")
        sql += ("insert into public.alertes (email, avis_id, objet, date_limite) values "
                "('ess@exemple.fr', '26-1', 'Ménage mairie', current_date + 2), ('pro@exemple.fr', '26-2', 'Peinture école', null);\n")
        db.q(sql)
        db.biz = biz
        yield db
    finally:
        db.stop()


def test_droits_sql_identiques_a_python(pg):
    for o in ("essentiel", "pro", "cle", None):
        for st in ("trialing", "active", "past_due", "inactif", "aucun"):
            sql = json.loads(pg.q(f"select public.droits({repr(o) if o else 'null'}, '{st}')"))
            assert sql == comptes.droits(pg.biz, o, st), (o, st)


def test_isolation_entre_clients(pg):
    assert pg.q("select count(*) from public.comptes", uid=U["ess"]) == "1"
    assert pg.q("select email from public.comptes", uid=U["ess"]) == "ess@exemple.fr"
    assert pg.q("select avis_id from public.alertes", uid=U["ess"]) == "26-1"
    assert pg.q("select avis_id from public.alertes", uid=U["pro"]) == "26-2"
    assert pg.q("select count(*) from public.comptes", role="anon") == "0"
    assert pg.q("select count(*) from public.offres_prix", uid=U["ess"]) == "0"      # table interne illisible
    autre = pg.q("select id from public.alertes where avis_id = '26-2'")
    with pytest.raises(RuntimeError, match="introuvable"):
        pg.q(f"select public.basculer_favori({autre})", uid=U["ess"])


def test_navigateur_ne_peut_pas_changer_ses_droits(pg):
    for sql in (f"update public.comptes set offre = 'cle', statut = 'active' where user_id = '{U['ess']}'",
                f"insert into public.demandes (user_id, type, avis_ref, statut, cle_idem) values ('{U['ess']}', 'rapport', '26-1', 'recue', 'xxxxxxxxxx')",
                f"update public.criteres set departements = '{{01,02,03}}'",
                "insert into public.stripe_evenements values ('evt_x', 'x')"):
        with pytest.raises(RuntimeError, match="permission denied"):
            pg.q(sql, uid=U["ess"])
    with pytest.raises(RuntimeError, match="permission denied"):
        pg.q("select public.appliquer_abonnement('evt_1', 't', 'ess@exemple.fr', 'price_pro', 'active', 'cus', 'sub_ess', null)",
             uid=U["ess"])
    with pytest.raises(RuntimeError, match="permission denied"):
        pg.q("select public.mon_espace()", role="anon")


def test_espace_et_criteres_selon_offre(pg):
    e = json.loads(pg.q("select public.mon_espace()", uid=U["ess"]))
    assert e["offre"] == "essentiel" and e["droits"]["departements_max"] == 1 and e["droits"]["rapports_mois"] == 0
    assert e["abonnement"] == {"prix_cents": None, "periode": None, "renouvellement": None, "resilie": False, "debut": None}
    with pytest.raises(RuntimeError, match="1 département"):
        pg.q("select public.modifier_criteres('nettoyage', '{33,47}', '{}')", uid=U["ess"])
    with pytest.raises(RuntimeError, match="mots-clés sont inclus"):
        pg.q("select public.modifier_criteres('nettoyage', '{33}', '{vitrerie}')", uid=U["ess"])
    with pytest.raises(RuntimeError, match="Métier inconnu"):
        pg.q("select public.modifier_criteres('plongee', '{33}', '{}')", uid=U["ess"])
    with pytest.raises(RuntimeError, match="Département invalide"):
        pg.q("select public.modifier_criteres('nettoyage', '{99}', '{}')", uid=U["ess"])
    e = json.loads(pg.q("select public.modifier_criteres('nettoyage', '{ 33 }', '{}')", uid=U["ess"]))
    assert e["criteres"]["departements"] == ["33"] and e["criteres"]["applique"] is False
    with pytest.raises(RuntimeError, match="patientez"):              # double clic
        pg.q("select public.modifier_criteres('nettoyage', '{47}', '{}')", uid=U["ess"])
    e = json.loads(pg.q("select public.modifier_criteres('peinture', '{33,47,2A}', '{ravalement, façade}')", uid=U["pro"]))
    assert e["criteres"]["mots_cles"] == ["ravalement", "façade"]
    with pytest.raises(RuntimeError, match="Aucun abonnement actif"):
        pg.q("select public.modifier_criteres('peinture', '{33}', '{}')", uid=U["annule"])
    with pytest.raises(RuntimeError, match="Aucun abonnement actif"):
        pg.q("select public.modifier_criteres('peinture', '{33}', '{}')", uid=U["sans"])


def test_demandes_quotas_et_double_clic(pg):
    with pytest.raises(RuntimeError, match="réservée à l'offre Clé en main"):
        pg.q("select id from public.demander('rapport', '26-123456', '', 'cle-ess-0001')", uid=U["pro"])
    a = pg.q("select id from public.demander('rapport', 'avis 26-123456', '', 'cle-rapport-1')", uid=U["cle"])
    b = pg.q("select id from public.demander('rapport', 'avis 26-123456', '', 'cle-rapport-1')", uid=U["cle"])
    assert a == b and pg.q("select count(*) from public.demandes", uid=U["cle"]) == "1"
    assert pg.q("select count(*) from public.demandes", uid=U["pro"]) == "0"          # isolation
    with pytest.raises(RuntimeError, match="référence"):
        pg.q("select id from public.demander('rapport', 'pas de ref', '', 'cle-rapport-x')", uid=U["cle"])
    for i in range(2, 21):
        pg.q(f"select id from public.demander('rapport', '26-1234{i:02d}', '', 'cle-rapport-{i:02d}')", uid=U["cle"])
    with pytest.raises(RuntimeError, match="Quota du mois atteint"):
        pg.q("select id from public.demander('rapport', '26-999999', '', 'cle-rapport-21')", uid=U["cle"])
    pg.q(f"update public.demandes set statut = 'echec' where id = '{a}'")             # un échec ne consomme pas le quota
    pg.q("select id from public.demander('rapport', '26-999999', '', 'cle-rapport-21')", uid=U["cle"])
    # Rapports possibles pendant l'essai Clé en main, dossiers inclus seulement après le 1er paiement
    pg.q("select id from public.demander('rapport', '26-555555', '', 'essai-rapport-1')", uid=U["cle_essai"])
    with pytest.raises(RuntimeError, match="premier paiement"):
        pg.q("select id from public.demander('dossier', '26-555555', '', 'essai-dossier-1')", uid=U["cle_essai"])


def test_dossier_pieces_privees(pg):
    d = pg.q("select id from public.demander('dossier', '26-777777', 'SARL Test', 'cle-dossier-1')", uid=U["cle"])
    assert pg.q(f"select statut from public.demandes where id = '{d}'") == "action_requise"
    with pytest.raises(RuntimeError, match="Ajoutez au moins une pièce"):
        pg.q(f"select statut from public.soumettre_dossier('{d}')", uid=U["cle"])
    with pytest.raises(RuntimeError, match="row-level security"):               # dépôt dans le dossier d'un autre client
        pg.q(f"insert into storage.objects (bucket_id, name) values ('documents', '{U['cle']}/{d}/dce/rc.pdf')", uid=U["pro"])
    with pytest.raises(RuntimeError, match="row-level security"):               # hors du sous-dossier dce/
        pg.q(f"insert into storage.objects (bucket_id, name) values ('documents', '{U['cle']}/{d}/resultat/x.docx')", uid=U["cle"])
    pg.q(f"insert into storage.objects (bucket_id, name) values ('documents', '{U['cle']}/{d}/dce/rc.pdf')", uid=U["cle"])
    assert pg.q(f"select statut from public.soumettre_dossier('{d}')", uid=U["cle"]) == "recue"
    assert pg.q("select count(*) from storage.objects", uid=U["pro"]) == "0"
    assert pg.q("select count(*) from storage.objects", uid=U["cle"]) == "1"
    with pytest.raises(RuntimeError, match="row-level security"):               # plus de dépôt une fois envoyé
        pg.q(f"insert into storage.objects (bucket_id, name) values ('documents', '{U['cle']}/{d}/dce/autre.pdf')", uid=U["cle"])
    with pytest.raises(RuntimeError, match="Seule une demande"):
        pg.q(f"select statut from public.annuler_demande('{d}')", uid=U["cle"])
    e = json.loads(pg.q("select public.mon_espace()", uid=U["cle"]))
    assert e["quotas"]["dossiers_utilises"] == 1 and e["quotas"]["dossiers_max"] == 2


def test_webhook_idempotent_et_compte_verifie(pg):
    appel = "select public.appliquer_abonnement('{e}', 'customer.subscription.updated', '{m}', '{p}', '{s}', 'cus_1', '{sub}', null)"
    assert pg.q(appel.format(e="evt_1", m="SANS@exemple.fr", p="price_pro", s="trialing", sub="sub_new")) == "applique"
    assert pg.q(appel.format(e="evt_1", m="sans@exemple.fr", p="price_pro", s="trialing", sub="sub_new")) == "deja_traite"
    assert pg.q(f"select offre || ' ' || statut from public.comptes where user_id = '{U['sans']}'") == "pro trialing"
    assert pg.q(appel.format(e="evt_2", m="non_verifie@exemple.fr", p="price_pro", s="active", sub="sub_x")) == "sans_compte"
    # un ancien abonnement résilié du même client n'écrase pas l'abonnement en cours
    assert pg.q(appel.format(e="evt_3", m="sans@exemple.fr", p="price_ess", s="canceled", sub="sub_old")) == "ignore"
    assert pg.q(appel.format(e="evt_4", m="sans@exemple.fr", p="price_pro", s="canceled", sub="sub_new")) == "applique"
    assert pg.q(f"select statut from public.comptes where user_id = '{U['sans']}'") == "inactif"


def test_criteres_acces_offert_modifiables(pg):
    pg.q(f"update public.comptes set stripe_subscription = null, source = 'interne' where user_id = '{U['cle']}'")
    try:
        e = json.loads(pg.q("select public.mon_espace()", uid=U["cle"]))
        assert e["gere_par_stripe"] is True
        e = json.loads(pg.q("select public.modifier_criteres('peinture', '{33}', '{}')", uid=U["cle"]))
        assert e["criteres"]["metier"] == "peinture"
        pg.q(f"update public.comptes set source = 'fichier' where user_id = '{U['cle']}'")
        with pytest.raises(RuntimeError, match="géré par le support"):   # autres comptes hors Stripe : toujours par le support
            pg.q("select public.modifier_criteres('nettoyage', '{33}', '{}')", uid=U["cle"])
    finally:
        pg.q(f"update public.comptes set stripe_subscription = 'sub_cle', source = null where user_id = '{U['cle']}'")
