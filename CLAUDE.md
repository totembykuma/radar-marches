# Radar Marchés : mémo pour Claude (lire ceci au lieu d'explorer le code)

Service d'alertes d'appels d'offres BOAMP par e-mail. Site statique sur GitHub Pages (branche gh-pages),
tout tourne dans GitHub Actions. Propriétaire non technique, en français, une étape à la fois.

## Où est quoi
- `radar/cli.py` : commandes daily / weekly / prospection / audit / demo / essai / abonnes.
- `radar/boamp.py` (avis + SIRET acheteur), `matching.py` (métiers), `historique.py` (fiche « avant de répondre », DECP),
  `prospects.py` (gagnants de marchés), `subscribers.py` (clients = Stripe), `stripe_setup.py`, `mailer.py` (Resend), `site.py`.
- `config/business.yaml` (offres, liens Stripe, prospection), `config/metiers.yaml` (mots, exclusions, cpv).
- `templates/` (site + e-mails), `static/style.css`, `tests/test_radar.py` (`python3 -m pytest -q tests`).

## Règles pour économiser les tokens
- Le réseau BOAMP/DECP/Stripe/Resend est bloqué en local : tester via un workflow, jamais par essais répétés.
- Lire les journaux GitHub avec `get_job_logs` et `tail_lines` ≤ 40. Les scripts de test impriment un résumé court (compteurs) à la fin.
- Lancer un workflow : `gh api -X POST repos/totembykuma/radar-marches/actions/workflows/<f>.yml/dispatches -f ref=main`.
- Attendre avec un seul Monitor (boucle `until`), pas de vérifications répétées.
- Ne pas relire un fichier déjà lu ; utiliser `grep -n` / `sed -n` sur la zone utile.
- Toujours `git pull -q --rebase origin main` avant `git push` (le robot commit data/ souvent).
- Ne jamais écrire d'e-mail, clé ou adresse personnelle dans le dépôt (public).
