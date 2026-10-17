# Radar Marchés : méthode de travail économe

But : avancer avec le moins de quota Claude possible. Une conversation (un « fil ») = un sujet.

## 1. Démarrer une nouvelle conversation
1. Ouvrir un **nouveau fil** pour chaque nouveau sujet. Un long fil coûte cher : chaque message renvoie tout l'historique.
2. Coller le modèle de prompt (section 6), en remplissant 3 lignes.
3. Ne pas rejoindre de pièces jointes déjà présentes dans le dépôt ou le dossier du projet : donner leur nom suffit.
4. Fil terminé = le laisser. Pour une suite, nouveau fil + une phrase de rappel.

## 2. Quels fichiers lire selon la tâche
Toujours : `RADAR_MARCHES_CONTEXT.md`. Puis **seulement** :

| Type de tâche | Fichiers à ouvrir |
|---|---|
| Bug / panne d'un envoi | Journal du workflow concerné (40 dernières lignes), puis le module nommé dans l'erreur |
| Alertes, tri des marchés | `radar/matching.py`, `config/metiers.yaml` |
| Collecte BOAMP / historique | `radar/boamp.py`, `radar/historique.py`, `radar/reprise.py` |
| Interface du site | `templates/<page>.html`, `static/style.css`, `radar/site.py` (zone utile) |
| E-mails | `templates/_email.html` + le modèle d'e-mail visé, `radar/mailer.py` |
| Référencement (SEO) | `radar/site.py`, `templates/` des pages publiques, `config/guides.yaml` |
| Paiements, offres, prix | `config/business.yaml`, `radar/subscribers.py`, `radar/stripe_setup.py` |
| Prospection | `radar/prospects.py`, `cli.py` (fonction `relancer`), section `prospection` de `business.yaml` |
| Automatisations (horaires) | le seul `.github/workflows/<nom>.yml` concerné |
| IA, dossiers, rapports | `radar/ai.py`, `radar/dossier.py`, `radar/rapport.py` |
| Comptes clients | `radar/comptes.py`, `docs/architecture-comptes-clients.md` (section utile) |
| Stratégie commerciale | ce fichier de contexte + `docs/OFFRE-ET-ACQUISITION.md` ; pas de code |

## 3. Limiter l'analyse
- Chercher avec `grep -n` puis lire la zone (`sed -n 'X,Yp'`) ; jamais un fichier entier « pour voir ».
- Pas d'exploration globale, pas de sous-agent ni de tâches parallèles, pas de recherche Web sauf besoin réel (fait récent, prix d'un service).
- Ne jamais relire un fichier déjà lu dans la même conversation.
- Vérifier un fonctionnement par **un** run de workflow ciblé, attendu une seule fois, pas par essais répétés.
- Toucher uniquement les fichiers de la tâche ; pas de « nettoyage » en passant.

## 4. Adapter l'effort à la tâche
- **Petite** (texte, prix, couleur, mot-clé) : 1 fichier, pas de plan, réponse en 2 lignes.
- **Moyenne** (nouvelle section, correction de bug) : lire 2-4 fichiers, tests, PR, réponse courte.
- **Grosse** (nouvelle offre, refonte) : demander d'abord un plan court à valider, puis exécuter en un fil dédié.
- Questions simples (« ça a marché ? ») : vérifier 1 run, répondre en une phrase.

## 5. Garder une trace concise
- Le détail est dans Git (message de commit + PR) : inutile de le recopier ailleurs.
- Après une tâche importante, ajouter 1 à 3 lignes datées dans `RADAR_MARCHES_CONTEXT.md` (section 3 « État » ou 5 « Priorités »), avec la preuve (run, PR, e-mail reçu). Remplacer les lignes périmées au lieu d'empiler.
- Une nouvelle règle de code durable va dans `CLAUDE.md` (1 ligne). Une préférence de William va dans la mémoire du projet (1 ligne dans le fichier du sujet).
- Comptes rendus : ce qui a changé, ce qui est vérifié, ce qu'il reste à faire. Pas de code collé, pas de récit de l'enquête.

## 6. Modèle de prompt (à copier dans un nouveau fil)
```
Radar Marchés. Lis RADAR_MARCHES_CONTEXT.md et RADAR_MARCHES_WORKFLOW.md (dépôt), rien d'autre au départ.
Type de tâche : [bug / fonctionnalité / site / SEO / automatisation / commercial]
Demande : [une ou deux phrases]
Fichiers probables : [si connus, sinon « à toi de cibler »]
Règles : analyse ciblée seulement, pas de sous-agent ni de recherche Web sauf nécessité, modifie uniquement les fichiers concernés, PR + tests, compte rendu en 5 lignes max, mets à jour le fichier de contexte si c'est important.
```
Variante très courte pour une petite retouche :
```
Radar Marchés, petite retouche, lis seulement RADAR_MARCHES_CONTEXT.md : [demande].
```

## 7. Ce qui consomme sans qu'on le voie (Cowork / projet)
- Chaque nouveau fil charge automatiquement : les consignes du projet, l'index de mémoire du projet, `CLAUDE.md` et **la liste des fichiers du dossier du projet**. Plus ces éléments sont gros, plus chaque fil coûte au démarrage.
- Les images jointes et les captures d'écran sont coûteuses : n'en joindre que si c'est indispensable.
- Les messages « actualise / où en est-on ? » dans un long fil relisent l'historique : préférer un nouveau fil court.
- Les automatismes (GitHub Actions) ne consomment **aucun** quota Claude : ils tournent seuls.
