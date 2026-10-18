# Radar Marchés : contexte de référence

> À lire en premier dans toute nouvelle conversation. Court par choix : le détail technique est dans `CLAUDE.md` (mémo du code), la méthode de travail dans `RADAR_MARCHES_WORKFLOW.md`.
> Règle : une fonctionnalité n'est notée « vérifiée » qu'avec une date et une preuve (run GitHub, e-mail reçu, test réel).
> Dernière mise à jour : 2026-10-17.

## 1. Objectif et offre
- But du propriétaire (William, non technique, en français) : 200 à 300 €/semaine, moins de 30 min/semaine de travail, tout automatisé.
- Produit : alertes e-mail quotidiennes d'appels d'offres publics (BOAMP) triés par métier et département, résumés par IA, + fiche « avant de répondre » (historique DECP).
- Prix : Essentiel 29 €/mois (1 métier, 1 dép.), Pro 49 €/mois (dép. illimités, mots-clés, bilan concurrentiel), Clé en main 149 €/mois (Pro + rapports « comment gagner » + 2 dossiers/mois). Prix dégressifs (choix du 2026-10-18) : 29 → 19,99 (3e mois) → 9,99 (6e) ; 49 → 39,99 → 29,99 ; 149 → 119,99 → 99,99 ; réabonnement = prix de départ. Annuel 169 / 399 / 1 199 € (choix du 2026-10-18), payé en une fois, sans essai, prix fixe 12 mois ; testé en mode test Stripe le 2026-10-18 (test-annuel.yml, 22/22). Essai **7 j avec carte**, une seule fois par client (essai sans carte refusé par William). Le 1er client garde ses 14 j puis suit la grille.
- Options : « Dossier prêt à signer » 199 € (149 € Pro, 99 € en plus pour Clé en main), parrainage 1 mois offert, promo Pro mensuelle (codes PROjjmm).
- Acquisition : pages SEO générées, prospection e-mail auto 20/jour, posts LinkedIn auto (Zapier), Bing IndexNow. Pas de pub payante.

## 2. Architecture (résumé)
- Python (`radar/`), tout tourne dans **GitHub Actions** (22 workflows, `.github/workflows/`), site statique sur **GitHub Pages** (branche `gh-pages`), domaine www.radar-marches.fr (DNS OVH).
- Services : Stripe (= seule base clients, mode réel), Resend (envois + réception de formulaires), Claude API (Haiku, plafond 3 $/mois), Supabase (comptes), GoatCounter (visites), Zapier (LinkedIn).
- État : `data/state.json`, `data/*.json` (commités par le robot, d'où `git pull --rebase` avant tout push).
- Réglages : `config/business.yaml` (offres, liens, prospection, IA), `config/metiers.yaml` (17 métiers).
- Tests : `python3 -m pytest -q tests` (≈ 72 tests). Le réseau BOAMP/Stripe/Resend est bloqué en local : tester via un workflow.
- Carte « où est quoi » fichier par fichier : `CLAUDE.md`.

## 3. État au 2026-10-17 (avec preuves)
| Élément | État | Preuve |
|---|---|---|
| Collecte BOAMP + alertes (daily, lun-ven ~6 h 20 Paris) | OK | run réussi 2026-10-16 ; e-mails reçus le 2026-10-15 |
| Site + pages SEO, nouveau design | En ligne | publié 2026-10-17 (PR #27), run « Publier le site » OK 2026-10-17 |
| Paiement Stripe réel | OK | 1er client (Pro, nettoyage, 75) en essai depuis 2026-10-16 |
| Bienvenue après paiement (toutes les 10 min) | OK | runs réussis 2026-10-17 |
| Prospection 20/jour mar-ven | OK | 1er envoi 2026-10-16, 20 e-mails, 0 erreur |
| IA (résumés) | Active | test API réel HTTP 200 le 2026-10-16, 5 $ de crédit |
| Comptes e-mail + mot de passe (Supabase) | OK | testés en réel par William le 2026-10-17 |
| Posts LinkedIn (Zapier) | OK | 1er post publié 2026-10-16 |
| Formulaire critères, avis, parrainage | Construits | critères vérifiés en réel 2026-10-15 ; avis/parrainage : jamais déclenchés en réel |
| Dossier prêt à signer / rapports Clé en main | Construits, **jamais testés avec un vrai client** | le 1er dossier réel sera le test |
| Bilan du dimanche (weekly) | OK | testé en réel 2026-10-15 |
| Relance prospects J+5 | Construite | active dès 2026-10-21, pas encore observée |
| Alerte gratuite | **Retirée** (2026-10-16, « c'est nul ») | code dormant, ne pas reproposer |

## 4. Décisions et règles à respecter
- Jamais de dépense, engagement ou action irréversible sans l'accord de William.
- Tout e-mail : signé « L'équipe Radar Marchés », logo, jamais le nom ou l'adresse perso de William. Rien de personnel dans le dépôt (public).
- Design : moderne, coloré, animé, titres courts ; refus du style « journal » sobre ; **aperçu ordinateur + mobile avant toute publication**.
- Prospection : 1 seul e-mail par entreprise + 1 relance unique si clic sans inscription.
- Réseaux : LinkedIn seulement (pas Facebook), Metricool écarté.
- Fiscalité : EI micro existante (activité secondaire à ajouter), pas de SIRET ni d'adresse publiés sans son choix.

## 5. Bugs connus, en cours, priorités
- Aucun bug ouvert connu au 2026-10-17 (derniers runs tous « success »).
- `README.md` : sections « pas encore vérifié » et « tes actions » datent du 2026-10-15 et sont dépassées ; se fier à ce fichier.
- Priorité semaine du 19 au 23/10 : **fiabilité** (William note le projet sur 20 le vendredi 2026-10-23, sous 15 = arrêt ; critère : pas de panne, visites et essais). Contrôle prévu lundi 2026-10-19 ~10 h 45.
- À surveiller : 1er paiement réel du client vers le 2026-10-30 ; 1er dossier réel ; 1res relances le 2026-10-21.
- Fil « Nouveau logo » lancé le 2026-10-17 (pistes dans `apercus/logo/` du dossier du projet) : voir ce fil pour son état.

## 6. Reprendre le développement
1. Lire ce fichier, puis seulement la ligne de `CLAUDE.md` qui concerne le sujet.
2. Ouvrir uniquement les fichiers cités pour la tâche (voir le tableau de `RADAR_MARCHES_WORKFLOW.md`).
3. Travailler sur une branche, PR, `pytest` avant push, `git pull -q --rebase origin main` avant push.
4. Après une tâche importante : mettre à jour la section 3 ou 5 ici (1 à 3 lignes, avec date et preuve).
