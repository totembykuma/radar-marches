# Les agents qui font tourner le business

Tout tourne **dans le cloud, gratuitement, même ordinateur éteint**, grâce aux tâches planifiées de GitHub Actions (2 000 min/mois offertes, illimitées pour un dépôt public ; un envoi quotidien prend ≈ 1–2 min).

| Agent | Quand | Ce qu'il fait | État |
|---|---|---|---|
| **1. Livreur** (`radar daily`) | Lun–ven ≈ 6h20 | Récupère les nouveaux avis BOAMP, les classe par métier, lit les abonnés dans Stripe, demande à Claude un résumé + une note TPE, envoie un e-mail à chaque abonné concerné, envoie le message de bienvenue aux nouveaux | Codé, testé hors ligne (7 tests) ; à tester en réel après branchement des clés |
| **2. Éditeur SEO** (dans `daily`) | Lun–ven | Régénère le site : page de vente, ~100–500 pages métier × département, sitemap ; publie sur GitHub Pages | Codé, testé hors ligne |
| **3. Vigie** (dans `daily` + en cas de plantage) | Chaque exécution | Te prévient par e-mail si : 0 avis un jour ouvré, envoi échoué, abonné sans métier reconnu, tâche plantée | Codé, testé hors ligne |
| **4. Directeur financier** (`radar weekly`) | Lundi ≈ 8h40 | MRR, abonnés payants/en essai/impayés, nouveaux/résiliés, CA du mois, frais, dépenses, cotisations, **net estimé**, décisions à prendre + analyse de Claude en 5 puces | Codé, calculs testés ; à tester en réel |
| **5. Support & croissance** (tâche planifiée Claude) | 1×/jour | Lit les e-mails reçus sur l'adresse de contact, prépare des brouillons de réponse, applique les changements de critères (métadonnées Stripe), te signale les demandes inhabituelles | **Conçu, pas encore créé** : il faut la boîte mail du business et ton accord |

## Pourquoi cette architecture
- **Aucun serveur à louer ni à maintenir** : GitHub exécute le code et héberge le site.
- **Aucune base de données** : Stripe est la seule source de vérité des clients (formulaire, statut, paiement).
- **L'IA n'est jamais bloquante** : si Claude ne répond pas, l'alerte part quand même avec l'objet brut.
- **Pas de doublon** : chaque avis envoyé est mémorisé (identifiants anonymisés) dans `data/state.json`.

## Agent 5 : fonctionnement prévu (à valider)
Une tâche planifiée Claude, quotidienne, avec accès à la boîte du business :
1. Lire les e-mails non traités de `contact@…`.
2. Classer : changement de critères / question / résiliation / autre.
3. Changement de critères → mettre à jour les métadonnées de l'abonnement Stripe (`metier`, `departements`, `motscles`) → effectif dès le lendemain.
4. Question courante → brouillon de réponse (envoi automatique seulement si tu l'autorises pour les questions de la FAQ).
5. Tout le reste (litige, remboursement, partenariat) → résumé dans le rapport du lundi.

Ta part après lancement : lire le rapport du lundi et les éventuelles alertes, valider quelques brouillons : **≈ 10–20 min/semaine**.
