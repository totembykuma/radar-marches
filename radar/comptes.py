"""Comptes clients avec e-mail et mot de passe (Supabase), reliés aux abonnements Stripe.

Répartition des rôles :
- Supabase Auth gère mots de passe (hachés), sessions, vérification d'e-mail, réinitialisation, changement d'adresse ;
- la base Supabase (supabase/schema.sql, RLS) garde comptes, critères, historique des alertes, favoris et demandes ;
- Stripe reste la référence des paiements : ce module recopie toutes les 10 min (bienvenue.yml) l'état réel des
  abonnements vers les comptes dont l'adresse est VÉRIFIÉE et identique (le webhook Stripe le fait aussi en direct) ;
- le navigateur ne décide jamais de l'offre ni des droits : il lit ses lignes (RLS) et appelle des fonctions SQL
  qui revérifient tout côté serveur.

Inactif tant que config/comptes.json (adresse du projet + clé publique) n'existe pas et qu'aucune clé n'est fournie :
l'ancien espace par lien (radar/compte.py) reste alors seul en service.
Clé du robot : secrets SUPABASE_URL + SUPABASE_SERVICE_KEY, ou à défaut un seul secret SUPABASE_ACCESS_TOKEN
(jeton du compte Supabase) avec lequel on lit la clé du projet au démarrage.
"""
import json
import logging
import os
import re
from datetime import date, datetime, timedelta, timezone

import requests

from . import mailer, parrainage
from .config import ROOT
from .matching import parse_departements
from .render import render
from .subscribers import PLANS_PRO

log = logging.getLogger(__name__)
API = "https://api.supabase.com/v1"
CONFIG = ROOT / "config" / "comptes.json"
STATUTS_ACTIFS = ("trialing", "active", "past_due")
PROJET = "radar-marches"
BUCKET = "documents"
DCE_MAX_OCTETS = 20 * 1024 * 1024
DCE_TYPES = (".pdf", ".docx", ".doc", ".odt", ".zip", ".txt")
GARDER_ALERTES_JOURS = 400          # historique : ~13 mois, puis effacé
GARDER_RESULTATS_JOURS = 180        # rapports et dossiers générés : 6 mois dans le stockage privé


# ---------------------------------------------------------------- configuration

def config_publique():
    """{url, cle} du projet (valeurs publiques par conception, protégées par RLS) ou None si les comptes sont inactifs."""
    try:
        d = json.loads(CONFIG.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return d if d.get("url") and d.get("cle") else None


def _masquer(valeur):
    """Empêche une clé lue à l'exécution d'apparaître dans les journaux GitHub."""
    if valeur and os.getenv("GITHUB_ACTIONS") == "true":
        print(f"::add-mask::{valeur}")


class Gestion:
    """API de gestion Supabase (jeton du compte : secret SUPABASE_ACCESS_TOKEN)."""

    def __init__(self, jeton, session=None):
        self.jeton, self.http = jeton, session or requests

    def req(self, method, path, **kw):
        r = self.http.request(method, f"{API}{path}", headers={"Authorization": f"Bearer {self.jeton}"}, timeout=120, **kw)
        if r.status_code >= 400:
            raise RuntimeError(f"Supabase gestion {method} {path.split('?')[0]} : {r.status_code} {r.text[:200]}")
        return r.json() if r.content else {}

    def projet(self):
        ref = (os.getenv("SUPABASE_PROJECT_REF") or "").strip()
        projets = self.req("GET", "/projects")
        p = next((p for p in projets if p.get("id") == ref or p.get("ref") == ref), None) if ref else None
        p = p or next((p for p in projets if (p.get("name") or "").lower().replace(" ", "-") == PROJET), None)
        p = p or (projets[0] if len(projets) == 1 else None)
        if not p:
            raise RuntimeError(f"projet Supabase « {PROJET} » introuvable ({len(projets)} projet(s) sur le compte)")
        return p

    def cles(self, ref):
        """(clé publique, clé secrète) : nouvelles clés publishable/secret si présentes, sinon anon/service_role."""
        cles = self.req("GET", f"/projects/{ref}/api-keys?reveal=true")
        def choisir(nouveau, ancien):
            k = next((k for k in cles if k.get("type") == nouveau and k.get("api_key")), None) \
                or next((k for k in cles if k.get("name") == ancien and k.get("api_key")), None)
            return (k or {}).get("api_key")
        return choisir("publishable", "anon"), choisir("secret", "service_role")


class Supa:
    """Accès robot à la base, à l'authentification et au stockage (clé secrète, jamais envoyée au navigateur)."""

    def __init__(self, url, cle, session=None):
        self.url, self.cle, self.http = url.rstrip("/"), cle, session or requests
        self.h = {"apikey": cle}
        if cle.startswith("ey"):                     # ancienne clé service_role (JWT)
            self.h["Authorization"] = f"Bearer {cle}"

    def _req(self, method, path, params=None, json_=None, headers=None, data=None, attendu=None):
        r = self.http.request(method, f"{self.url}{path}", params=params, json=json_, data=data,
                              headers={**self.h, **(headers or {})}, timeout=120)
        if r.status_code >= 400 and r.status_code not in (attendu if isinstance(attendu, tuple) else (attendu,)):
            raise RuntimeError(f"Supabase {method} {path.split('?')[0]} : {r.status_code} {r.text[:200]}")
        return r

    def select(self, table, **params):
        return self._req("GET", f"/rest/v1/{table}", params=params).json()

    def upsert(self, table, rows, conflit=None, ignorer=False):
        if not rows:
            return
        prefer = "resolution=ignore-duplicates" if ignorer else "resolution=merge-duplicates"
        self._req("POST", f"/rest/v1/{table}", params={"on_conflict": conflit} if conflit else None, json_=rows,
                  headers={"Prefer": f"{prefer},return=minimal"})

    def update(self, table, valeurs, **filtres):
        """PATCH filtré ; renvoie les lignes réellement modifiées (vide si la condition ne tenait plus)."""
        return self._req("PATCH", f"/rest/v1/{table}", params=filtres, json_=valeurs,
                         headers={"Prefer": "return=representation"}).json()

    def delete(self, table, **filtres):
        self._req("DELETE", f"/rest/v1/{table}", params=filtres, headers={"Prefer": "return=minimal"})

    def utilisateurs(self):
        """Utilisateurs Supabase (paginé)."""
        out, page = [], 1
        while True:
            lot = self._req("GET", "/auth/v1/admin/users", params={"page": page, "per_page": 1000}).json()
            lot = lot.get("users", lot) if isinstance(lot, dict) else lot
            out += lot
            if len(lot) < 1000:
                return out
            page += 1

    def deposer(self, chemin, contenu, type_mime):
        self._req("POST", f"/storage/v1/object/{BUCKET}/{chemin}", data=contenu,
                  headers={"Content-Type": type_mime, "x-upsert": "true"})

    def lire(self, chemin):
        return self._req("GET", f"/storage/v1/object/{BUCKET}/{chemin}").content

    def lister(self, prefixe):
        return self._req("POST", f"/storage/v1/object/list/{BUCKET}",
                         json_={"prefix": prefixe, "limit": 100, "sortBy": {"column": "name", "order": "asc"}}).json()

    def effacer(self, chemins):
        if chemins:
            self._req("DELETE", f"/storage/v1/object/{BUCKET}", json_={"prefixes": list(chemins)})


def connexion(session=None):
    """Client robot, ou None si les comptes ne sont pas configurés (tout le module reste alors inactif)."""
    url, cle = (os.getenv("SUPABASE_URL") or "").strip(), (os.getenv("SUPABASE_SERVICE_KEY") or "").strip()
    if not (url and cle):
        jeton = (os.getenv("SUPABASE_ACCESS_TOKEN") or "").strip()
        conf = config_publique()
        if not jeton or not conf:
            return None
        g = Gestion(jeton, session)
        ref = re.match(r"https://([a-z0-9]+)\.supabase\.co", conf["url"])
        ref = ref.group(1) if ref else g.projet()["id"]
        _, cle = g.cles(ref)
        url = conf["url"]
        if not cle:
            raise RuntimeError("clé secrète du projet Supabase introuvable")
    _masquer(cle)
    return Supa(url, cle, session)


# ---------------------------------------------------------------- droits (miroir de public.droits en SQL, testé à l'identique)

def offres_sql(biz):
    """Lignes de la table offres, calculées depuis business.yaml (seule source des limites commerciales)."""
    rows = []
    for cle, o in (biz.get("offres") or {}).items():
        rapports = int(o.get("rapports_max") or 0)
        rows.append({"cle": cle, "nom": o["nom"], "departements_max": int(o.get("departements_max") or 1),
                     "mots_cles": cle in PLANS_PRO, "historique_complet": cle in PLANS_PRO,
                     "rapports_mois": rapports, "dossiers_mois": int(o.get("dossiers_inclus") or 0),
                     "rapports_en_essai": rapports > 0})
    return rows


def droits(biz, offre, statut):
    o = next((r for r in offres_sql(biz) if r["cle"] == offre), None)
    if not o or statut not in STATUTS_ACTIFS:
        return {"actif": False, "veille": False, "departements_max": 0, "mots_cles": False, "historique_complet": False,
                "rapports_mois": 0, "dossiers_mois": 0}
    return {"actif": True, "veille": True, "departements_max": o["departements_max"], "mots_cles": o["mots_cles"],
            "historique_complet": o["historique_complet"],
            "rapports_mois": 0 if statut == "trialing" and not o["rapports_en_essai"] else o["rapports_mois"],
            "dossiers_mois": 0 if statut == "trialing" else o["dossiers_mois"]}


# ---------------------------------------------------------------- synchronisation Stripe → comptes

def _iso(ts):
    return datetime.fromtimestamp(int(ts), timezone.utc).isoformat() if ts else None


def referentiels(supa, biz, metiers):
    supa.upsert("offres", offres_sql(biz), conflit="cle")
    supa.upsert("offres_prix", [{"price_id": p, "offre": cle} for cle, o in biz["offres"].items()
                                for p in o.get("stripe_price_ids") or []], conflit="price_id")
    supa.upsert("metiers", [{"cle": k, "libelle": m["libelle"]} for k, m in metiers.items()], conflit="cle")


def _meilleur(subs):
    """Un seul abonnement par adresse : actif > essai > impayé, puis le plus récent (évite les doubles comptes Stripe)."""
    rang = {"active": 0, "trialing": 1, "past_due": 2}
    par_email = {}
    for s in subs:
        e = (s.get("email") or "").lower()
        if not e:
            continue
        k = (rang.get(s.get("statut"), 9), -(s.get("debut") or 0))
        if e not in par_email or k < par_email[e][0]:
            par_email[e] = (k, s)
    return {e: s for e, (_, s) in par_email.items()}


def _criteres_de(s):
    return {"metier": (s.get("metiers") or [None])[0], "departements": sorted(s.get("departements") or []),
            "mots_cles": list(s.get("mots_cles") or [])}


def synchroniser(supa, subs, biz, metiers, maintenant=None):
    """Recopie l'état réel des abonnements (subs = subscribers.load_all) vers les comptes vérifiés. Idempotent."""
    maintenant = maintenant or datetime.now(timezone.utc)
    referentiels(supa, biz, metiers)
    par_email = _meilleur(subs)
    comptes = {c["user_id"]: c for c in supa.select("comptes", select="*")}
    criteres = {c["user_id"]: c for c in supa.select("criteres", select="*")}
    stats = {"comptes": 0, "relies": 0, "modifies": 0, "criteres_importes": 0}
    a_ecrire, crit_ecrire = [], []
    utilisateurs = supa.utilisateurs()
    stats["_utilisateurs"] = utilisateurs
    # Colonnes d'affichage « Mon abonnement » : écrites seulement quand la base les a (schema.sql appliqué),
    # pour qu'une base pas encore mise à jour ne fasse jamais échouer la synchronisation.
    affichage = any("renouvellement" in c for c in comptes.values())
    for u in utilisateurs:
        email = (u.get("email") or "").lower()
        if not email or not u.get("email_confirmed_at"):
            continue                                   # adresse non vérifiée : jamais reliée à un abonnement
        stats["comptes"] += 1
        s, avant = par_email.get(email), comptes.get(u["id"]) or {}
        if s:
            stats["relies"] += 1
            row = {"offre": s["plan"], "statut": s["statut"], "stripe_customer": s.get("customer"),
                   "stripe_subscription": s.get("stripe_subscription"), "fin_essai": _iso(s.get("fin_essai")),
                   "source": s.get("source")}
            if affichage:
                row.update(prix_cents=s.get("prix_mois"), periode=s.get("periode"), renouvellement=_iso(s.get("renouvellement")),
                           resilie=bool(s.get("resilie")), debut=_iso(s.get("debut")))
        else:
            row = {"offre": avant.get("offre"), "statut": "inactif" if avant.get("offre") else "aucun",
                   "stripe_customer": avant.get("stripe_customer"), "stripe_subscription": None, "fin_essai": None,
                   "source": avant.get("source")}
            if affichage:
                row.update(prix_cents=None, periode=None, renouvellement=None, resilie=False, debut=None)
        row.update(user_id=u["id"], email=email,
                   parrainage=parrainage.lien(biz, s) if s and s.get("statut") in ("active", "trialing") else None)
        if any(_norm_ts(row[k]) != _norm_ts(avant.get(k)) for k in row):
            a_ecrire.append({**row, "maj": maintenant.isoformat()})
        # Critères : à la 1re liaison, ou quand ils ont changé dans Stripe (support, ancien formulaire) sans demande en attente.
        c = criteres.get(u["id"])
        if s and s.get("source") == "interne":        # accès offert : critères initiaux, ensuite ceux de l'espace
            if not c and _criteres_de(s)["metier"] in metiers:
                crit_ecrire.append({"user_id": u["id"], **_criteres_de(s), "maj": maintenant.isoformat(),
                                    "pousse_stripe": maintenant.isoformat(), "erreur": None})
                stats["criteres_importes"] += 1
        elif s and s.get("stripe_subscription"):
            stripe_c = _criteres_de(s)
            en_attente = c and (not c.get("pousse_stripe") or _ts(c["pousse_stripe"]) < _ts(c["maj"]))
            if not c or (not en_attente and {k: (sorted(c.get(k) or []) if k == "departements" else c.get(k))
                                              for k in stripe_c} != stripe_c):
                if stripe_c["metier"] in metiers:
                    crit_ecrire.append({"user_id": u["id"], **stripe_c, "maj": maintenant.isoformat(),
                                        "pousse_stripe": maintenant.isoformat(), "erreur": None})
                    stats["criteres_importes"] += 1
    supa.upsert("comptes", a_ecrire, conflit="user_id")
    supa.upsert("criteres", crit_ecrire, conflit="user_id")
    stats["modifies"] = len(a_ecrire)
    return stats


def _ts(v):
    return datetime.fromisoformat(str(v).replace("Z", "+00:00")) if v else datetime.min.replace(tzinfo=timezone.utc)


def _norm_ts(v):
    """Compare des dates écrites par Python et relues de PostgreSQL (formats différents)."""
    if isinstance(v, str) and re.match(r"\d{4}-\d{2}-\d{2}T", v):
        return _ts(v).timestamp()
    return v


def inviter(subs, biz, utilisateurs, state):
    """Une seule fois par client existant sans compte : invitation à créer son mot de passe (même adresse)."""
    import hashlib
    deja = state.setdefault("invitation_compte", [])
    avec_compte = {(u.get("email") or "").lower() for u in utilisateurs}
    envoyes, erreurs = 0, []
    for s in subs:
        e = (s.get("email") or "").lower()
        h = hashlib.sha256(f"invitation|{e}".encode()).hexdigest()[:16]
        if not e or s.get("statut") not in STATUTS_ACTIFS or e in avec_compte or h in deja:
            continue
        try:
            res = mailer.send(e, f"{biz['nom']} : créez votre mot de passe", render("email_compte.html", biz=biz, ok=True,
                              invitation=True, email=e), biz["email_expediteur"], reply_to=biz["email_contact"])
            if not res.get("simulated") or os.getenv("RADAR_DEMO") == "1":
                deja.append(h)
            envoyes += 1
        except Exception as exc:
            erreurs.append(f"invitation au compte non envoyée : {str(exc)[:120]}")
    state["invitation_compte"] = deja[-2000:]
    return envoyes, erreurs


# ---------------------------------------------------------------- critères modifiés dans l'espace client → Stripe

def valider_criteres(biz, metiers, plan, c):
    """Revérifie côté robot ce que la fonction SQL a validé (défense en profondeur). Renvoie (meta Stripe, criteres)."""
    offre = biz["offres"][plan]
    if c.get("metier") not in metiers:
        raise ValueError("métier inconnu")
    deps = parse_departements(",".join(c.get("departements") or []))
    if len(deps) != len(c.get("departements") or []) or len(deps) > int(offre["departements_max"]):
        raise ValueError("départements hors offre")
    mots = [m for m in (c.get("mots_cles") or []) if m.strip()]
    if mots and plan not in PLANS_PRO:
        raise ValueError("mots-clés hors offre")
    meta = {"metier": c["metier"], "departements": ", ".join(deps) or ",", "motscles": ", ".join(mots) or ","}
    return meta, {"metiers": [c["metier"]], "departements": deps, "mots_cles": mots}


def pousser_criteres(supa, subs, biz, metiers, criteres_txt, stripe_update, maintenant=None):
    """Applique dans Stripe (lu par l'envoi du matin) les critères modifiés dans l'espace client, puis confirme par e-mail."""
    maintenant = maintenant or datetime.now(timezone.utc)
    comptes = {c["user_id"]: c for c in supa.select("comptes", select="user_id,email,offre,statut,stripe_subscription")}
    par_sub = {s.get("stripe_subscription"): s for s in subs if s.get("stripe_subscription")}
    internes = {s["email"].lower(): s for s in subs if s.get("source") == "interne"}
    faits, erreurs = 0, []
    for c in supa.select("criteres", select="*"):
        compte = comptes.get(c["user_id"]) or {}
        interne = internes.get((compte.get("email") or "").lower())
        if interne:                                    # accès offert, sans Stripe : critères de l'espace appliqués à chaque passage
            try:
                interne.update(valider_criteres(biz, metiers, interne["plan"], c)[1])
            except Exception:
                pass
            if not c.get("pousse_stripe") or _ts(c["pousse_stripe"]) < _ts(c["maj"]):
                supa.update("criteres", {"pousse_stripe": maintenant.isoformat(), "erreur": None},
                            user_id=f"eq.{c['user_id']}", maj=f"eq.{c['maj']}")
            continue
        if c.get("pousse_stripe") and _ts(c["pousse_stripe"]) >= _ts(c["maj"]):
            continue
        s = par_sub.get(compte.get("stripe_subscription"))
        try:
            if not s or s.get("statut") not in STATUTS_ACTIFS:
                raise ValueError("aucun abonnement actif relié")
            meta, appliques = valider_criteres(biz, metiers, s["plan"], c)
            stripe_update(s["stripe_subscription"], meta)
        except Exception as exc:
            supa.update("criteres", {"erreur": "Modification non appliquée : écrivez-nous, nous corrigeons."},
                        user_id=f"eq.{c['user_id']}", maj=f"eq.{c['maj']}")
            erreurs.append(f"critères de l'espace client non appliqués : {str(exc)[:120]}")
            continue
        # « maj » inchangé : si le client a encore modifié entre-temps, la nouvelle version sera poussée au passage suivant.
        supa.update("criteres", {"pousse_stripe": maintenant.isoformat(), "erreur": None},
                    user_id=f"eq.{c['user_id']}", maj=f"eq.{c['maj']}")
        s.update(appliques)
        faits += 1
        try:
            mailer.send(s["email"], f"{biz['nom']} : vos critères sont à jour",
                        render("email_criteres.html", biz=biz, ok=True, criteres=criteres_txt(s, metiers), espace=True),
                        biz["email_expediteur"], reply_to=biz["email_contact"])
        except Exception as exc:
            erreurs.append(f"confirmation de critères non envoyée : {str(exc)[:120]}")
    return faits, erreurs


# ---------------------------------------------------------------- historique des alertes, favoris et rappels

def enregistrer_alertes(supa, email, avis, aujourd_hui=None):
    """Garde dans l'historique privé du client les avis qu'on vient de lui envoyer (doublons ignorés)."""
    j = (aujourd_hui or date.today()).isoformat()
    rows = [{"email": email.lower(), "avis_id": a["id"], "recu": j, "objet": (a.get("objet") or "")[:400],
             "acheteur": (a.get("acheteur") or "")[:200] or None,
             "lieu": (a.get("ville") or ", ".join(a.get("departements") or []))[:120] or None,
             "date_limite": (a.get("date_limite") or "")[:10] or None, "metier": a.get("metier_libelle") or None,
             "url": a.get("url") or None, "resume": (a.get("resume") or "")[:400] or None} for a in avis]
    supa.upsert("alertes", rows, conflit="email,avis_id", ignorer=True)
    return len(rows)


def rappels_favoris(supa, biz, subs, aujourd_hui=None):
    """E-mail de rappel 3 jours avant la date limite d'un avis mis en favori (une seule fois par avis)."""
    j = aujourd_hui or date.today()
    actifs = {s["email"].lower() for s in subs if s.get("statut") in STATUTS_ACTIFS}
    lignes = supa.select("alertes", select="id,email,objet,acheteur,date_limite,url", favori="is.true", rappel_envoye="is.null",
                         **{"and": f"(date_limite.gte.{j.isoformat()},date_limite.lte.{(j + timedelta(days=3)).isoformat()})"})
    envoyes, erreurs = 0, []
    for a in lignes:
        if a["email"] not in actifs:
            continue
        jours = (date.fromisoformat(a["date_limite"]) - j).days
        try:
            mailer.send(a["email"], f"Rappel : date limite dans {jours} jour{'s' if jours > 1 else ''} · {a['objet'][:60]}",
                        render("email_rappel_favori.html", biz=biz, a=a, jours=jours), biz["email_expediteur"],
                        reply_to=biz["email_contact"])
            supa.update("alertes", {"rappel_envoye": j.isoformat()}, id=f"eq.{a['id']}")
            envoyes += 1
        except Exception as exc:
            erreurs.append(f"rappel de favori non envoyé : {str(exc)[:120]}")
    return envoyes, erreurs


def menage(supa, aujourd_hui=None):
    """Durées de conservation : historique d'alertes ~13 mois, rapports et dossiers générés 6 mois."""
    j = aujourd_hui or date.today()
    supa.delete("alertes", recu=f"lt.{(j - timedelta(days=GARDER_ALERTES_JOURS)).isoformat()}")
    vieux = supa.select("demandes", select="id,fichier", fichier="not.is.null",
                        termine=f"lt.{(j - timedelta(days=GARDER_RESULTATS_JOURS)).isoformat()}")
    supa.effacer([d["fichier"] for d in vieux])
    for d in vieux:
        supa.update("demandes", {"fichier": None, "message": "Document effacé (conservation 6 mois)."}, id=f"eq.{d['id']}")


# ---------------------------------------------------------------- demandes (rapports, dossiers inclus) depuis l'espace client

def _fin(supa, d, statut, message=None, fichier=None):
    supa.update("demandes", {"statut": statut, "message": message, "fichier": fichier,
                             "maj": datetime.now(timezone.utc).isoformat(),
                             "termine": datetime.now(timezone.utc).isoformat() if statut in ("terminee", "echec") else None},
                id=f"eq.{d['id']}")


def _pieces(supa, d):
    prefixe = f"{d['user_id']}/{d['id']}/dce"
    return [f"{prefixe}/{o['name']}" for o in supa.lister(prefixe) if o.get("name") and o.get("id")]


def traiter_demandes(supa, biz, metiers, faire_rapport, faire_dossier, maintenant=None, limite=5):
    """Traite les demandes « recue » (workflow dossier.yml, toutes les heures en journée).
    faire_rapport(demande, compte) / faire_dossier(demande, compte, pieces) font le travail et renvoient
    (chemin du résultat dans le stockage, message) ; une exception = échec, retenté 1 fois, quota jamais consommé par un échec."""
    maintenant = maintenant or datetime.now(timezone.utc)
    # Demandes restées « en traitement » après un arrêt brutal : remises dans la file.
    for d in supa.select("demandes", select="id,essais", statut="eq.en_traitement",
                         maj=f"lt.{(maintenant - timedelta(hours=2)).isoformat()}"):
        supa.update("demandes", {"statut": "recue", "essais": d["essais"] + 1}, id=f"eq.{d['id']}", statut="eq.en_traitement")
    comptes = {c["user_id"]: c for c in supa.select("comptes", select="user_id,email,offre,statut")}
    faits, erreurs = 0, []
    for d in supa.select("demandes", select="*", statut="eq.recue", order="cree.asc", limit=str(limite)):
        pris = supa.update("demandes", {"statut": "en_traitement", "maj": maintenant.isoformat()},
                           id=f"eq.{d['id']}", statut="eq.recue")
        if not pris:
            continue                                  # déjà pris par un autre passage
        c = comptes.get(d["user_id"]) or {}
        dr = droits(biz, c.get("offre"), c.get("statut"))
        if not dr[f"{d['type']}s_mois"]:
            _fin(supa, d, "echec", "Votre abonnement ne permet plus cette demande (offre ou statut modifié).")
            continue
        try:
            if d["type"] == "rapport":
                fichier, message = faire_rapport(d, c)
            else:
                pieces = _pieces(supa, d)
                fichier, message = faire_dossier(d, c, pieces)
                supa.effacer(pieces)                  # pièces du client effacées dès le dossier rédigé
            _fin(supa, d, "terminee" if fichier else "echec", message, fichier)
            faits += 1
        except Exception as exc:
            essais = d.get("essais", 0) + 1
            if essais < 2:
                supa.update("demandes", {"statut": "recue", "essais": essais, "message": "Nouvel essai en cours."},
                            id=f"eq.{d['id']}")
            else:
                _fin(supa, d, "echec", "Préparation impossible pour le moment. Ce n'est pas décompté : vous pouvez redemander.")
                if d["type"] == "dossier":
                    supa.effacer(_pieces(supa, d))
            erreurs.append(f"demande {d['type']} non traitée : {str(exc)[:120]}")
    return faits, erreurs


def nom_fichier(texte, ext):
    from .textutil import norm
    return re.sub(r"[^a-z0-9]+", "-", norm(texte or "document"))[:50].strip("-") + ext
