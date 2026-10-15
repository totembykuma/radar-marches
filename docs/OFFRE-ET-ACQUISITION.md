# Offre, positionnement et acquisition

## Positionnement
**Radar Marchés : les appels d'offres publics de votre métier, triés par l'IA, chaque matin.**
Cible : TPE/PME (1–20 salariés) des métiers qui vendent beaucoup aux collectivités : nettoyage, espaces verts, second œuvre du bâtiment, CVC, restauration, formation, communication, informatique, sécurité, transport.
Promesse : *voir tôt les quelques marchés qui valent votre temps*, sans jargon ni 50 alertes hors sujet.
Ce qu'on ne promet pas : gagner des marchés, ni l'exhaustivité (source : BOAMP).

## Offres
| | Essentiel | Pro |
|---|---|---|
| Prix | 29 €/mois | 49 €/mois |
| Métiers | 1 | 1 + mots-clés personnalisés |
| Zone | jusqu'à 3 départements | toute la France ou départements au choix |
| Alerte quotidienne + résumé IA + note TPE | ✓ | ✓ |
| Essai gratuit | 14 jours, carte demandée | 14 jours, carte demandée |

Pourquoi carte demandée pendant l'essai : un essai sans carte convertit mal et demande des relances ; avec carte, Stripe gère tout (rappel avant la fin de l'essai, prélèvement, relances d'impayés).

Évolutions prévues quand ≥ 15 clients : offre annuelle (−2 mois), ajout des avis européens (TED), offre « Équipe » 99 €.

## Paramétrage Stripe (fait une fois, ≈ 20 min, décrit dans le README)
Deux **liens de paiement** (Payment Links), mode abonnement, essai 14 jours, avec ces **champs personnalisés** (le code les lit tels quels) :

| Clé du champ | Type | Libellé affiché | Contenu |
|---|---|---|---|
| `metier` | Liste déroulante | Votre métier | une option par métier ; valeur = clé sans tiret : `nettoyage`, `espacesverts`, `peinture`, `electricite`, `plomberiecvc`, `menuiserie`, `maconneriegrosoeuvre`, `couverture`, `restauration`, `formation`, `communicationweb`, `informatique`, `securite`, `transport` |
| `departements` | Texte | Départements (ex. 33, 47, 24) | Pro : « France » accepté |
| `motscles` | Texte, facultatif (lien Pro seulement) | Mots-clés en plus (séparés par des virgules) | |

Réglages Stripe à activer : *Billing → Abonnements et e-mails → envoyer un rappel avant la fin d'un essai*, *relances automatiques des paiements échoués (Smart Retries)*, *portail client* (résiliation en 1 clic).

## Système d'acquisition (du plus automatique au moins automatique)

### 1. Pages SEO auto-générées (100 % automatique, effet en 2–6 mois)
Chaque matin, le site régénère ~100 à 500 pages « Appels d'offres *métier* *département* » à partir des avis ouverts, avec le sitemap. Requêtes visées : « appel d'offre nettoyage gironde », « marché public espaces verts 47 »… Ces pages sont utiles même sans abonnement, ce qui est la condition pour être bien référencé. **Action unique** : déclarer le site dans Google Search Console (README, étape 7).

### 2. Kit de lancement (une seule fois, ≈ 1 h 30, à faire par toi car il faut un compte humain)
Publier les messages ci-dessous dans 5 à 10 groupes Facebook/LinkedIn d'artisans et d'entrepreneurs du bâtiment, de la propreté et du paysage (la plupart autorisent les posts utiles ; lire leurs règles).

**Post groupes (exemple nettoyage, à adapter au métier du groupe)** :
> Bonjour à tous. J'ai créé un petit outil qui lit chaque jour tous les appels d'offres publics (BOAMP) et envoie le matin uniquement ceux d'un métier et d'un secteur, résumés en une phrase. Cette semaine, il y avait par exemple [N] marchés de nettoyage ouverts en [région] : [lien vers la page du site]. La page est gratuite et mise à jour chaque jour. Si vous voulez les recevoir par mail, il y a un essai de 14 jours. Vos retours m'intéressent !

**Post LinkedIn** :
> Les mairies, collèges et hôpitaux publient chaque jour des centaines de marchés… et la plupart des TPE ne les voient jamais. J'ai lancé Radar Marchés : chaque matin, l'IA lit tout le BOAMP et n'envoie à chaque entreprise que les marchés de son métier et de son département, avec un résumé clair et une note « accessible à une petite entreprise ». Exemples en direct : [lien]. Essai gratuit 14 jours.

Annuaires gratuits où inscrire le site (une fois) : Google Business Profile (en « service en ligne »), annuaires de startups/outils (Product Hunt FR, Les-Outils-IA, etc.), et proposer un partenariat aux CMA/fédérations locales (lien vers les pages gratuites).

### 3. Prospection e-mail automatisée (prête à brancher, **désactivée**, nécessite ton accord)
En France, l'e-mail de prospection B2B est permis sans consentement préalable s'il concerne l'activité du destinataire, identifie l'expéditeur et propose un désabonnement (règles CNIL). Proposition : 20 e-mails/jour maximum, depuis un domaine secondaire, vers des entreprises du métier trouvées sur leur site public. **Risques** : réputation du domaine, plaintes. Je ne l'active pas sans ton feu vert.

> Objet : 3 marchés publics de [métier] ouverts dans le [département]
> Bonjour, en lisant le BOAMP ce matin, j'ai repéré [N] appels d'offres [métier] ouverts près de chez vous, dont « [objet] » (réponse avant le [date]). La liste complète est ici : [lien page]. Si vous voulez les recevoir chaque matin, Radar Marchés propose 14 jours d'essai gratuit. Bonne journée, [prénom]. *Pour ne plus recevoir de message, répondez STOP.*

### 4. Parrainage (à activer quand ≥ 10 clients)
1 mois offert au parrain et au filleul via un code promo Stripe.

## Objectifs de pilotage
| Indicateur | Cible à 3 mois | Cible à 6 mois | Cible à 12 mois |
|---|---|---|---|
| Visiteurs/mois sur le site | 300 | 1 500 | 4 000 |
| Essais démarrés/mois | 5 | 12 | 20 |
| Conversion essai → payant | 40 % | 45 % | 50 % |
| Abonnés payants | 4–8 | 12–20 | 30–45 |
Ce sont des cibles, pas des prévisions garanties. Le rapport hebdomadaire les suit automatiquement.
