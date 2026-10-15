# Radar Marchés

Abonnement d'alertes d'appels d'offres publics, triés et résumés par IA, livré automatiquement chaque matin.
Étude et choix : [docs/ETUDE-MARCHE.md](docs/ETUDE-MARCHE.md) · Offre et acquisition : [docs/OFFRE-ET-ACQUISITION.md](docs/OFFRE-ET-ACQUISITION.md) · Agents : [docs/AGENTS.md](docs/AGENTS.md)

## Ce qui est fait et vérifié
- Moteur complet en Python : récupération BOAMP, classement par métier (avec exclusions des faux positifs), filtrage par abonné, résumés IA, e-mails, anti-doublons, bienvenue, alertes d'anomalie, rapport hebdo financier.
- Site : page de vente, pages SEO par métier et département, CGV, mentions légales, confidentialité, sitemap.
- 3 tâches automatiques GitHub (envoi quotidien + publication du site, rapport hebdo, tests).
- **7 tests automatiques réussis** sur des données d'exemple (tri, exclusions, lecture Stripe simulée, calcul du net, envoi de bout en bout sans doublon).

## Ce qui n'est PAS encore vérifié
- L'appel réel à l'API BOAMP : le réseau de mon environnement la bloque. Le premier lancement sur GitHub le vérifie (commande `check-api`), et l'agent t'alerte si le format diffère.
- Les envois réels (Resend), la lecture réelle de Stripe et les résumés Claude : il faut tes comptes et clés.

## Tes actions (≈ 2 h 30 en tout, une seule fois)

| # | Action | Pourquoi | Coût |
|---|---|---|---|
| 1 | Créer la micro-entreprise | Obligatoire pour encaisser | 0 € |
| 2 | Créer le dépôt GitHub et me donner l'accès | Là où le code tourne et où le site est hébergé | 0 € |
| 3 | Acheter le nom de domaine | Indispensable pour envoyer des e-mails qui n'arrivent pas en spam | ≈ 10 €/an **(ton accord)** |
| 4 | Ouvrir Resend | Envoi des e-mails | 0 € (≤ 3 000/mois) |
| 5 | Ouvrir Stripe + 2 liens de paiement | Encaisser les abonnements | 1,5 % + 0,25 € par paiement |
| 6 | Clé API Claude | Résumés IA (facultatif au début) | ≈ 5 €/mois **(ton accord)** |
| 7 | Coller les clés dans GitHub | Que les agents puissent travailler | 0 € |
| 8 | Google Search Console | Que Google trouve les pages | 0 € |
| 9 | Publier le kit de lancement | Premiers clients | 0 € |

### 1. Micro-entreprise (20 min)
1. Va sur **formalites.entreprise.gouv.fr** → « Créer une entreprise » → entreprise individuelle, régime micro.
2. Activité : « Service en ligne de veille et de diffusion d'informations sur les marchés publics » (activité libérale, BNC).
3. Coche **l'ACRE** (cotisations divisées par deux la 1re année) et, si ton revenu fiscal le permet, le **versement libératoire**.
4. Tu recevras ton SIRET sous 1 à 4 semaines : envoie-le-moi avec l'adresse à afficher, je remplis les mentions légales.

### 2. GitHub (10 min)
1. Crée un compte sur github.com si besoin (identifiant : `totembykuma`).
2. Clique « New repository », nom **radar-marches**, **Public**, sans README → Create.
3. Installe l'app Claude sur ce dépôt : https://github.com/apps/claude/installations/select_target → choisis `radar-marches`.
4. Dis-moi « dépôt prêt » : j'y envoie le code et j'active le site.

### 3. Nom de domaine (10 min, ≈ 10 €/an)
Vérifie la disponibilité de `radar-marches.fr` (ou un autre nom) chez OVHcloud, Gandi ou Cloudflare et achète-le. Dis-moi lequel ; je mets le site à jour.

### 4. Resend (15 min)
1. Compte sur resend.com → « Domains » → ajoute ton domaine → copie les 3–4 enregistrements DNS affichés dans la zone DNS de ton registrar → « Verify ».
2. « API Keys » → « Create » (permission *Sending access*) → garde la clé pour l'étape 7.

### 5. Stripe (40 min)
1. Compte sur stripe.com (identité + IBAN : à faire par toi, accès sensible).
2. Catalogue → 2 produits : « Radar Marchés Essentiel » 29 €/mois et « Radar Marchés Pro » 49 €/mois.
3. Pour chacun : « Créer un lien de paiement » → cocher l'essai gratuit 14 jours → « Ajouter des champs personnalisés » exactement comme dans [OFFRE-ET-ACQUISITION.md](docs/OFFRE-ET-ACQUISITION.md#paramétrage-stripe-fait-une-fois--20-min-décrit-dans-le-readme) → page de confirmation : « Merci, votre première alerte arrive demain matin ».
4. Paramètres → Billing : active le rappel de fin d'essai, les relances automatiques et le portail client.
5. Développeurs → Clés API → **Clé restreinte** : *Lecture* sur Checkout Sessions, Customers, Balance transactions ; *Écriture* sur Subscriptions. Garde-la pour l'étape 7. **Ne me l'envoie jamais dans le chat.**
6. Envoie-moi les 2 liens de paiement et les identifiants de prix (`price_…`), ce ne sont pas des secrets.

### 6. Clé Claude (5 min, ≈ 5 €/mois)
console.anthropic.com → Billing → ajoute 5–10 € de crédit → API Keys → Create Key.

### 7. Coller les clés dans GitHub (10 min)
Dépôt → Settings → Secrets and variables → Actions :
- onglet **Secrets** : `STRIPE_SECRET_KEY`, `RESEND_API_KEY`, `ANTHROPIC_API_KEY`, `OWNER_EMAIL` (ton e-mail pour les rapports).
- onglet **Variables** : `STRIPE_LINK_ESSENTIEL`, `STRIPE_LINK_PRO`.
Puis onglet Actions → « Envoi quotidien et site » → « Run workflow » : je vérifie le résultat avec toi.

### 8. Google Search Console (10 min)
search.google.com/search-console → ajouter le domaine → valider par DNS → « Sitemaps » → `sitemap.xml`.

### 9. Lancement (≈ 1 h 30, une fois)
Publie les messages prêts à l'emploi de [OFFRE-ET-ACQUISITION.md](docs/OFFRE-ET-ACQUISITION.md) dans 5 à 10 groupes. Abonne-toi toi-même en test via `data/abonnes_manuels.yaml` pour voir ce que reçoivent les clients.

## Ensuite : ≈ 15 min par semaine
Lire le rapport du lundi, répondre aux rares alertes, valider quelques réponses clients.

## Pour un développeur
```
pip install -r requirements.txt pytest
python -m pytest -q tests          # tests hors ligne
python -m radar.cli demo           # bout-en-bout avec données d'exemple (e-mails dans out/outbox)
python -m radar.cli check-api      # vérifie les champs réels du BOAMP
```
Dépenses à saisir dans `finance/depenses.csv` (date,libelle,montant_eur,categorie). Métiers et mots-clés : `config/metiers.yaml`. Prix et fiscalité : `config/business.yaml`.
