"""Posts LinkedIn de la semaine (lundi → vendredi), écrits à partir des vraies données du site.

Le site publie /linkedin/semaine.json et /linkedin/flux.xml ; un Zap Zapier gratuit (RSS → page LinkedIn)
publie chaque post le jour venu (aucune clé dans ce dépôt)."""
import re
from datetime import date, timedelta

from .render import date_fr, date_limite_fr

TAGS = "#marchéspublics #appelsdoffres #PME #BTP"


def _euros(n):
    return f"{n:,}".replace(",", " ") + " €"


def _court(s, n=90):
    s = re.sub(r"^[\w/.-]*\d{4,}[\w/.-]*\s*[-:]\s*", "", (s or "").strip())   # numéro de consultation en tête
    if s.isupper():
        s = s.capitalize()
    return s if len(s) <= n else s[:n].rsplit(" ", 1)[0] + "…"


def semaine(biz, metiers, by_metier_public, laureats=None, barometre=None, today=None, noms_dep=None):
    today = today or date.today()
    lundi = today - timedelta(days=today.weekday())
    base = biz["site_url"].rstrip("/")
    utm = "?utm_source=linkedin"
    noms_dep = noms_dep or {}
    counts = sorted(((len(v), k) for k, v in by_metier_public.items() if v), reverse=True)
    posts = []

    def ajouter(jour, texte):
        posts.append({"date": (lundi + timedelta(days=jour)).isoformat() + "T09:00:00", "fuseau": "Europe/Paris", "texte": texte.strip()})

    if counts:
        total = sum(n for n, _ in counts)
        lignes = "\n".join(f"• {metiers[k]['libelle']} : {n}" for n, k in counts[:6])
        ajouter(0, f"📋 Cette semaine, {total} appels d'offres publics sont ouverts dans nos métiers.\n\n{lignes}\n\n"
                   f"Tous les marchés, classés par métier et par département, mis à jour chaque matin :\n{base}/appels-offres/{utm}\n\n{TAGS}")
        # Focus métiers : rotation chaque semaine parmi les métiers les plus fournis.
        fournis = [k for n, k in counts if n >= 5] or [counts[0][1]]
        rang = lundi.isocalendar()[1]
        for jour, k in ((1, fournis[rang % len(fournis)]), (3, fournis[(rang + 1) % len(fournis)])):
            if jour == 3 and len(fournis) < 2:
                break
            lst = by_metier_public[k]
            ex = "\n".join(f"• {_court(a['objet'])}{' (' + a['departements'][0] + ')' if a.get('departements') else ''}"
                           f"{', réponse avant le ' + date_limite_fr(a['date_limite'][:10]) if a.get('date_limite') else ''}"
                           for a in lst[:3])
            pme = sum(bool(a.get("pme")) for a in lst)
            ajouter(jour, f"🔎 {len(lst)} appels d'offres « {metiers[k]['libelle'].lower()} » ouverts en ce moment"
                          f"{f', dont {pme} déclarés accessibles aux PME' if pme else ''}.\n\nQuelques exemples :\n{ex}\n\n"
                          f"La liste complète, par département :\n{base}/appels-offres/{k}/{utm}\n\n{TAGS}")
    if laureats and laureats.get("fiches"):
        recents = sorted(((r, f) for f in laureats["fiches"] for r in f["marches"][:1] if r.get("m")), key=lambda x: x[0]["d"], reverse=True)[:3]
        if recents:
            lignes = "\n".join(f"• {f['nom']}{' (' + f['commune'] + ')' if f['commune'] else ''} : {_court(r['o'], 70)}, {_euros(r['m'])}"
                               for r, f in recents)
            ajouter(2, f"🏆 Qui a gagné quoi ? Derniers marchés publics attribués dans nos métiers :\n\n{lignes}\n\n"
                       f"Bravo aux équipes ! Toutes les entreprises lauréates et leurs marchés (données publiques officielles) :\n"
                       f"{base}/entreprises/{utm}\n\n{TAGS}")
    reperes = (laureats or {}).get("reperes") or {}
    faible = sorted(((v["offres_median"], k) for k, v in reperes.items() if (v.get("offres_median") or 0) >= 2 and v["n"] >= 30))
    if faible:
        n, k = faible[0]
        txt = (f"💡 Le saviez-vous ? En {metiers[k]['libelle'].lower()}, un marché public reçoit en médiane {str(n).replace('.0', '').replace('.', ',')} offres "
               f"(données officielles des 12 derniers mois).\n\nAvec si peu de concurrents, répondre vaut souvent le coup, à condition de voir l'appel d'offres à temps.")
        if barometre:
            txt += f"\n\nLe baromètre complet des appels d'offres :\n{base}/barometre/{utm}"
        else:
            txt += f"\n\nNos guides pour répondre pas à pas :\n{base}/guides/{utm}"
        ajouter(4, f"{txt}\n\n{TAGS}")
    posts.sort(key=lambda p: p["date"])
    return {"semaine_du": lundi.isoformat(), "genere_le": date_fr(today), "posts": posts}


def flux_rss(data, biz, today=None):
    """Flux RSS : un article par post déjà « échu » (date ≤ aujourd'hui). Zapier (gratuit) publie chaque nouvel article
    sur la page LinkedIn ; l'identifiant unique (guid) par jour évite toute publication en double."""
    from xml.sax.saxutils import escape
    today = (today or date.today()).isoformat()
    base = biz["site_url"].rstrip("/")
    items = []
    for p in sorted(data["posts"], key=lambda p: p["date"], reverse=True):
        jour = p["date"][:10]
        if jour > today:
            continue
        d = date.fromisoformat(jour)
        pub = f"{['Mon','Tue','Wed','Thu','Fri','Sat','Sun'][d.weekday()]}, {d.day:02d} {['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'][d.month - 1]} {d.year} 07:00:00 +0000"
        titre = p["texte"].split("\n", 1)[0]
        items.append(f"<item><title>{escape(titre)}</title><link>{base}/</link><guid isPermaLink=\"false\">radar-linkedin-{jour}</guid>"
                     f"<pubDate>{pub}</pubDate><description>{escape(p['texte'])}</description></item>")
    return ('<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel>'
            f"<title>{escape(biz['nom'])} · posts LinkedIn</title><link>{base}/</link><description>Posts du jour</description>"
            + "".join(items) + "</channel></rss>")
