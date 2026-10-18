"""Abonnements annuels : paiement unique pour 12 mois, prix fixe, renouvellement à la date anniversaire.

- Économie affichée = coût des 12 premières mensualités (grille dégressive si elle existe) − prix annuel.
- Texte affiché sur la page de paiement Stripe : montant prélevé aujourd'hui, engagement 12 mois, date de renouvellement
  (mis à jour chaque jour par bienvenue.yml, la date dépend du jour du paiement).
- Anti double prélèvement : si une même adresse a deux abonnements Radar en cours (ex. mensuel puis annuel),
  le plus récent est gardé, l'ancien est arrêté et sa part non utilisée remboursée (prorata du temps restant).
- Rappel e-mail 30 jours avant le renouvellement annuel, et e-mail si le paiement du renouvellement échoue.
"""
import hashlib
import logging
import os
from datetime import date, datetime, timezone

import requests

log = logging.getLogger(__name__)
API = "https://api.stripe.com/v1"
VERSION = "2024-06-20"          # forme des objets Stripe figée (current_period_end sur l'abonnement, invoice.payment_intent)
ACTIFS = ("active", "trialing", "past_due")
REMBOURSEMENTS_MAX = 5          # par passage : au-delà, on s'arrête et on prévient le propriétaire


# ---------------------------------------------------------------- prix et économies

def mensualites(offre, n=12):
    """Les n premières mensualités. Grille dégressive : offre['paliers'] = [{"des_mois": 1, "prix": 29}, {"des_mois": 3, …}]."""
    paliers = sorted(offre.get("paliers") or [], key=lambda p: p["des_mois"])
    out = []
    for mois in range(1, n + 1):
        prix = offre["prix_mensuel"]
        for p in paliers:
            if mois >= p["des_mois"]:
                prix = p["prix"]
        out.append(float(prix))
    return out


def cout_douze_mois(offre):
    return round(sum(mensualites(offre)), 2)


def economie(offre):
    if not offre.get("prix_annuel"):
        return 0
    return round(cout_douze_mois(offre) - offre["prix_annuel"], 2)


def enrichir(biz):
    """Ajoute aux offres les montants calculés (affichage du site, des e-mails et de l'espace client)."""
    for o in (biz.get("offres") or {}).values():
        if o.get("prix_annuel"):
            o["cout_12_mois"] = cout_douze_mois(o)
            o["economie_annuelle"] = economie(o)
    return biz


def euros(v):
    """199 → « 199 », 18.9 → « 18,90 »."""
    v = round(float(v), 2)
    return f"{v:.0f}" if v == int(v) else f"{v:.2f}".replace(".", ",")


def date_renouvellement(jour):
    try:
        return jour.replace(year=jour.year + 1)
    except ValueError:              # 29 février
        return jour.replace(year=jour.year + 1, day=28)


def texte_paiement(offre, jour=None):
    """Message affiché au-dessus du bouton de paiement Stripe de l'offre annuelle (≤ 1 200 caractères)."""
    jour = jour or date.today()
    renouv = date_renouvellement(jour).strftime("%d/%m/%Y")
    return (f"Vous payez aujourd'hui {euros(offre['prix_annuel'])} € TTC, en une seule fois, pour 12 mois d'accès "
            f"à l'offre {offre['nom']}. Engagement de 12 mois à prix fixe, sans autre prélèvement pendant cette période. "
            f"Renouvellement automatique le {renouv} au même prix, sauf résiliation avant cette date depuis votre espace "
            "client (rappel par e-mail 30 jours avant). Si vous aviez un abonnement mensuel, il est arrêté "
            "automatiquement et sa part non utilisée vous est remboursée.")


# ---------------------------------------------------------------- appels Stripe

def _call(method, path, key, data=None, params=None, idem=None, session=None):
    http = session or requests
    headers = {"Stripe-Version": VERSION}
    if idem:
        headers["Idempotency-Key"] = idem
    r = http.request(method, f"{API}/{path}", data=data, params=params, auth=(key, ""), headers=headers, timeout=60)
    if r.status_code >= 400:
        raise RuntimeError(f"Stripe {path} : {r.status_code} {r.text[:300]}")
    return r.json()


def _lister(path, params, key, session=None):
    params = dict(params, limit=100)
    while True:
        body = _call("GET", path, key, params=params, session=session)
        yield from body["data"]
        if not body.get("has_more"):
            break
        params["starting_after"] = body["data"][-1]["id"]


def _cle():
    return (os.getenv("STRIPE_SECRET_KEY") or "").strip()


def _produits_radar(key, session=None):
    return {p["id"]: p["metadata"]["radar_offre"] for p in _lister("products", {}, key, session)
            if (p.get("metadata") or {}).get("radar_offre") in ("essentiel", "pro", "cle")}


def periode(sub):
    items = (sub.get("items") or {}).get("data") or [{}]
    return "an" if ((items[0].get("price") or {}).get("recurring") or {}).get("interval") == "year" else "mois"


def fin_periode(sub):
    items = (sub.get("items") or {}).get("data") or [{}]
    return sub.get("current_period_end") or items[0].get("current_period_end")


def debut_periode(sub):
    items = (sub.get("items") or {}).get("data") or [{}]
    return sub.get("current_period_start") or items[0].get("current_period_start")


# ---------------------------------------------------------------- anti double prélèvement

def a_remplacer(subs):
    """subs = abonnements Radar en cours (avec 'email'). Renvoie [(ancien, nouveau)] : par adresse, le plus récent reste."""
    par_email = {}
    for s in subs:
        par_email.setdefault((s.get("email") or "").lower(), []).append(s)
    out = []
    for email, liste in par_email.items():
        if not email or len(liste) < 2:
            continue
        liste.sort(key=lambda s: s.get("created") or 0, reverse=True)
        out += [(ancien, liste[0]) for ancien in liste[1:]]
    return out


def part_non_utilisee(montant_paye, debut, fin, maintenant):
    """Prorata du temps restant, en centimes (0 si la période est finie ou rien n'a été payé)."""
    if not montant_paye or not debut or not fin or fin <= debut or maintenant >= fin:
        return 0
    return int(montant_paye * (fin - max(maintenant, debut)) / (fin - debut))


def _rembourser(ancien, key, maintenant, session=None):
    """Rembourse la part non utilisée de la dernière facture payée de l'ancien abonnement. Renvoie les centimes."""
    inv_id = ancien.get("latest_invoice")
    if isinstance(inv_id, dict):
        inv_id = inv_id.get("id")
    if not inv_id:
        return 0
    inv = _call("GET", f"invoices/{inv_id}", key, session=session)
    if inv.get("status") != "paid" or not inv.get("amount_paid") or not (inv.get("charge") or inv.get("payment_intent")):
        return 0
    montant = part_non_utilisee(inv["amount_paid"], debut_periode(ancien), fin_periode(ancien), maintenant)
    cible = {"charge": inv["charge"]} if inv.get("charge") else {"payment_intent": inv["payment_intent"]}
    if any((r.get("metadata") or {}).get("radar_doublon") == ancien["id"] and r.get("status") != "failed"
           for r in _lister("refunds", cible, key, session)):
        return 0                    # déjà remboursé lors d'un passage précédent
    if inv.get("charge"):
        ch = _call("GET", f"charges/{inv['charge']}", key, session=session)
        montant = min(montant, ch["amount"] - ch.get("amount_refunded", 0))
    if montant < 50:                # moins de 0,50 € : Stripe refuse, et rien à rendre
        return 0
    _call("POST", "refunds", key, data={**cible, "amount": montant, "reason": "duplicate",
                                       "metadata[radar_doublon]": ancien["id"]},
          idem=f"radar-doublon-{ancien['id']}", session=session)
    return montant


def remplacer_doublons(maintenant=None, session=None, test_clock=None):
    """À lancer souvent (bienvenue.yml). Renvoie {'remplaces': [...], 'erreurs': [...]} (adresses masquées)."""
    key = _cle()
    res = {"remplaces": [], "erreurs": []}
    if not key:
        return res
    maintenant = maintenant or int(datetime.now(timezone.utc).timestamp())
    produits = _produits_radar(key, session)
    en_cours = []
    params = {"expand[]": "data.customer", **({"test_clock": test_clock} if test_clock else {})}   # horloge : tests seulement
    for statut in ACTIFS:
        for s in _lister("subscriptions", {**params, "status": statut}, key, session):
            items = (s.get("items") or {}).get("data") or []
            if not items or (items[0].get("price") or {}).get("product") not in produits:
                continue
            cust = s.get("customer") or {}
            s["email"] = cust.get("email") if isinstance(cust, dict) else None
            en_cours.append(s)
    rembourses = 0
    for ancien, nouveau in a_remplacer(en_cours):
        if rembourses >= REMBOURSEMENTS_MAX:
            res["erreurs"].append("plafond de remboursements atteint, le reste attend le prochain passage")
            break
        try:
            montant = _rembourser(ancien, key, maintenant, session) if ancien["status"] == "active" else 0
            if ancien["status"] == "past_due" and ancien.get("latest_invoice"):
                inv = ancien["latest_invoice"]
                inv = inv.get("id") if isinstance(inv, dict) else inv
                try:                # facture impayée de l'ancien abonnement : annulée, plus de nouvelle tentative
                    _call("POST", f"invoices/{inv}/void", key, session=session)
                except RuntimeError as exc:
                    log.warning("facture %s non annulée : %s", inv, exc)
            _call("DELETE", f"subscriptions/{ancien['id']}", key, session=session)
            rembourses += 1 if montant else 0
            res["remplaces"].append({"email": ancien["email"], "ancien": ancien["id"], "nouveau": nouveau["id"],
                                     "ancien_periode": periode(ancien), "nouveau_periode": periode(nouveau),
                                     "rembourse": montant / 100})
        except Exception as exc:
            res["erreurs"].append(f"remplacement {ancien['id']} : {str(exc)[:200]}")
    return res


# ---------------------------------------------------------------- page de paiement : date de renouvellement du jour

def liens_annuels(key, session=None):
    """[(lien Stripe, clé d'offre)] des liens de paiement annuels actifs."""
    out = []
    for pl in _lister("payment_links", {"active": "true"}, key, session):
        md = pl.get("metadata") or {}
        if md.get("radar_offre") in ("essentiel", "pro", "cle") and md.get("radar_periode") == "an":
            out.append((pl, md["radar_offre"]))
    return out


def maj_textes(biz, state, jour=None, session=None):
    """Une fois par jour : la date de renouvellement affichée avant paiement = aujourd'hui + 1 an. Renvoie le nb de liens mis à jour."""
    key = _cle()
    jour = jour or datetime.now(timezone.utc).date()
    if not key or state.get("texte_annuel_jour") == jour.isoformat():
        return 0
    n = 0
    for pl, offre_key in liens_annuels(key, session):
        offre = biz["offres"].get(offre_key)
        if not offre or not offre.get("prix_annuel"):
            continue
        texte = texte_paiement(offre, jour)
        if ((pl.get("custom_text") or {}).get("submit") or {}).get("message") != texte:
            _call("POST", f"payment_links/{pl['id']}", key, data={"custom_text[submit][message]": texte}, session=session)
            n += 1
    state["texte_annuel_jour"] = jour.isoformat()
    return n


# ---------------------------------------------------------------- e-mails : rappel de renouvellement, paiement refusé

def a_prevenir(subs, deja, maintenant):
    """subs = subscribers.load_all. Renvoie [(abonné, 'rappel'|'echec', clé)] à envoyer une seule fois."""
    out = []
    for s in subs:
        if s.get("periode") != "an" or not s.get("renouvellement") or s.get("source") != "stripe":
            continue
        fin = s["renouvellement"]
        if s.get("statut") == "past_due":
            genre = "echec"
        elif s.get("statut") == "active" and not s.get("resilie") and 0 < fin - maintenant <= 30.5 * 86400:
            genre = "rappel"
        else:
            continue
        cle = hashlib.sha256(f"{s['email'].lower()}|{genre}|{fin}".encode()).hexdigest()[:16]
        if cle not in deja:
            out.append((s, genre, cle))
    return out
