# Radar Marchés : mémo pour Claude (lire ceci au lieu d'explorer le code)

Service d'alertes d'appels d'offres BOAMP par e-mail. Site statique sur GitHub Pages (branche gh-pages),
tout tourne dans GitHub Actions. Propriétaire non technique, en français, une étape à la fois.

## Où est quoi
- `radar/cli.py` : commandes daily / weekly / prospection / audit / demo / essai / abonnes.
- `radar/boamp.py` (avis + SIRET acheteur), `matching.py` (métiers), `historique.py` (fiche « avant de répondre », DECP),
  `prospects.py` (gagnants de marchés), `avis.py` (avis clients : demande à 1 mois, formulaire /avis/ lu via Resend, data/avis.json), `dossier.py` (option « Dossier prêt à signer » 199 €/149 € Pro : paiement Stripe à l'unité, pièces reçues sur dossier@…resend.app, Claude rédige, Word envoyé ; actif seulement si IA active, workflow dossier.yml horaire, état data/dossiers.json), `parrainage.py` (?ami=CODE → client_reference_id Stripe, 1 mois crédité filleul + parrain), `subscribers.py` (clients = Stripe), `stripe_setup.py`, `mailer.py` (Resend), `site.py`.
- `config/business.yaml` (offres, liens Stripe, prospection), `config/metiers.yaml` (mots, exclusions, cpv).
- `templates/` (site + e-mails), `static/style.css`, `tests/test_radar.py` (`python3 -m pytest -q tests`).

- Bilan du dimanche (weekly.yml, dim. 17 h UTC) : visites GoatCounter (compteur public, sinon « non disponible »), prospection, essais, ventes.
- Prospection : par_jour 20, plafonnée par `quota_prospection` (Resend gratuit 100/jour, 3 000/mois, alertes clients prioritaires).
- Pages ville `/appels-offres/villes/<ville>-<dep>.html` (ville de l'acheteur lue dans `donnees`, ≥ 5 avis sinon pas de page).
- Pages publiques : les avis publiés depuis < 3 jours (`RECENTS_JOURS`, site.py) sont masqués et remplacés par un encart « réservé aux abonnés ». Carte de France cliquable (`templates/_carte.html`, tracés `config/carte_france.json` faits par `scripts/carte_france.py`) sur le hub (→ `/appels-offres/departements/<d>.html`, tous métiers) et chaque page métier. `site.yml` republie le site sans e-mail dès qu'un template change.
- Pages « qui a gagné quoi » `/entreprises/` (`laureats.py`) : marchés attribués 12 mois (DECP, CPV de metiers.yaml), 1 page par société lauréate (entrepreneurs individuels exclus), noms via annuaire en cache `data/societes.json` (daily : 1 500 appels, entreprises.yml dim. : 6 000), max 6 000 pages.
- Baromètre `/barometre/` (`barometre.py`) : barometre.yml le 1er du mois → `data/barometre/AAAA-MM.json`, puis relance site.yml.
- Posts LinkedIn : le site publie `/linkedin/semaine.json` et `/linkedin/flux.xml` (`linkedin.py`, 1 article RSS par jour lun-ven dès sa date) ; un Zap Zapier gratuit (RSS → LinkedIn « Create Company Update ») publie sur la page Radar Marchés. Metricool écarté : LinkedIn payant.
- Alerte gratuite (RETIRÉE le 2026-10-16, code dormant tant que `alerte_gratuite` absent de business.yaml ; `gratuit.py`, page /alerte-gratuite/, mailto → alerte@…resend.app lu par daily) : 3 marchés/semaine, adresses chiffrées (Fernet, clé tirée de RESEND_INBOUND_KEY) dans data/alerte_gratuite.json, sortie auto si client, STOP = désinscription.
- Relance prospects (`cli.relancer`) : 1 seule, 5 à 12 j après, si Resend dit « clicked » et pas client/inscrit gratuit ; seulement les envois avec champ `suivi` (pied de page annonçant le rappel). IndexNow (Bing) le lundi.
- Après paiement : liens Stripe → `/merci/` (stripe.yml) ; bienvenue.yml toutes les 10 min envoie la bienvenue aux nouveaux abonnés (`cli bienvenue`).
- Résumés IA : actifs seulement si secret ANTHROPIC_API_KEY ET `premier_client_payant` dans data/state.json (posé par daily, qui prévient le propriétaire).

## Règles pour économiser les tokens
- Le réseau BOAMP/DECP/Stripe/Resend est bloqué en local : tester via un workflow, jamais par essais répétés.
- Lire les journaux GitHub avec `get_job_logs` et `tail_lines` ≤ 40. Les scripts de test impriment un résumé court (compteurs) à la fin.
- Lancer un workflow : `gh api -X POST repos/totembykuma/radar-marches/actions/workflows/<f>.yml/dispatches -f ref=main`.
- Attendre avec un seul Monitor (boucle `until`), pas de vérifications répétées.
- Ne pas relire un fichier déjà lu ; utiliser `grep -n` / `sed -n` sur la zone utile.
- Toujours `git pull -q --rebase origin main` avant `git push` (le robot commit data/ souvent).
- Ne jamais écrire d'e-mail, clé ou adresse personnelle dans le dépôt (public).
