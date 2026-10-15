# Radar Marchés expliqué de A à Z

Ce document explique tout simplement, sans jargon, avant de lancer quoi que ce soit. Rien n'est encore en ligne et aucun euro n'a été dépensé.

## 1. L'idée en une phrase
Chaque jour, l'État publie des centaines d'**appels d'offres** : une mairie cherche une entreprise pour nettoyer ses écoles, un collège pour entretenir ses espaces verts, etc. Beaucoup de petites entreprises ne les voient jamais, car ils sont noyés dans la masse.
**Radar Marchés lit tout chaque matin et envoie à chaque client uniquement ceux de son métier et de sa région, résumés en une phrase.** Le client paie 29 € ou 49 € par mois pour ça.

## 2. Qui sont les clients
Des artisans et des petites entreprises (1 à 20 salariés) : nettoyage, espaces verts, peinture, électricité, plomberie, restauration, formation, informatique, sécurité, etc.
Ce qu'ils achètent, c'est **du temps gagné et des occasions qu'ils auraient ratées**. Un seul marché gagné peut leur rapporter des milliers d'euros ; 29 € par mois, c'est peu pour eux.

## 3. Comment ça marche, concrètement
1. **6h20, du lundi au vendredi** : un programme se lance tout seul sur les serveurs de GitHub (gratuit).
2. Il télécharge les nouveaux appels d'offres publiés par l'État (base publique et gratuite appelée **BOAMP**).
3. Il les trie par métier, en écartant les faux amis : par exemple, « peinture routière » n'est pas envoyée à un peintre en bâtiment.
4. Il regarde dans **Stripe** (le système de paiement) qui est abonné, et avec quels critères.
5. Il demande à l'IA **Claude** un résumé d'une phrase et une note « accessible à une petite entreprise ».
6. Il envoie un e-mail à chaque client concerné, via **Resend** (le service d'envoi d'e-mails).
7. Il met à jour le **site internet** avec la liste des marchés ouverts, page par métier et par département. Ces pages attirent des visiteurs depuis Google, et certains deviennent clients.
8. Si quelque chose ne va pas, il **t'envoie une alerte par e-mail**.
9. **Chaque lundi**, il t'envoie un rapport : nombre de clients, argent encaissé, dépenses, ce qu'il te reste après cotisations, et les décisions éventuelles à prendre.

Tu n'as pas besoin que ton ordinateur soit allumé : tout tourne dans le cloud.

## 4. Le parcours d'un client
1. Il trouve une page du site, par exemple « Appels d'offres nettoyage Gironde », via Google ou un post que tu as partagé.
2. Il clique sur « Essai gratuit 14 jours ».
3. Sur la page de paiement Stripe, il choisit son métier, ses départements et entre sa carte. **Rien n'est prélevé pendant 14 jours.**
4. Le lendemain matin, il reçoit un e-mail de bienvenue, puis ses premières alertes.
5. Au bout de 14 jours, Stripe le prélève automatiquement chaque mois, sauf s'il résilie (en un clic, sans t'écrire).
6. S'il veut changer ses critères, il répond à l'e-mail.

## 5. Le circuit de l'argent
Client → **Stripe** (prend environ 1,5 % + 0,25 € par paiement) → **ton compte bancaire**, automatiquement chaque semaine.
Tu déclares ensuite ton chiffre d'affaires à l'URSSAF chaque mois ou chaque trimestre. C'est un formulaire en ligne de 5 minutes, et le rapport du lundi te donne le chiffre exact.

| Exemple avec 37 clients | Par mois |
|---|---|
| Chiffre d'affaires (ce que les clients paient) | ≈ 1 295 € |
| − frais Stripe, IA, domaine | ≈ − 40 € |
| = Bénéfice | ≈ 1 255 € |
| − cotisations sociales et impôt (25,6 % du CA) | ≈ − 330 € |
| **= Ce qu'il te reste** | **≈ 925 € (≈ 214 €/semaine)** |

La première année, l'**ACRE** divise les cotisations par deux, ce qui fait environ 150 € de plus par mois.

## 6. Les outils, et pourquoi chacun

| Outil | À quoi il sert | Coût | Qui crée le compte |
|---|---|---|---|
| **Micro-entreprise** | Être en règle pour encaisser | 0 € | Toi (pièce d'identité) |
| **GitHub** | Faire tourner les programmes chaque jour et héberger le site | 0 € | Toi (5 min) |
| **Nom de domaine** (ex. radar-marches.fr) | Adresse du site + e-mails qui n'arrivent pas en spam | ≈ 10 €/an | Toi (achat) |
| **Resend** | Envoyer les e-mails aux clients | 0 € jusqu'à 3 000 e-mails/mois | Toi |
| **Stripe** | Encaisser, gérer essais, résiliations et impayés | ≈ 1,5 % + 0,25 € par paiement | Toi (identité + IBAN) |
| **Claude (API)** | Résumer et noter les annonces | ≈ 5 €/mois | Toi (carte bancaire) |
| **Google Search Console** | Dire à Google que le site existe | 0 € | Toi |

Pourquoi ces comptes doivent être créés par toi : ils demandent ton identité, ta carte ou ton IBAN. Je n'ai pas le droit de les créer à ta place, et je ne dois jamais voir tes clés secrètes. Tu les colles toi-même dans un coffre-fort de GitHub, prévu pour ça.

## 7. Qui fait quoi

**Ce que j'ai déjà fait :** l'étude de marché, l'offre et les prix, tout le programme, le site, les textes de vente, les CGV, les mentions légales et la page de confidentialité, les e-mails, le rapport financier et les tests.

**Ce que je ferai :** envoyer le code sur GitHub, activer le site, adapter les textes à ton nom et ton domaine, vérifier le premier lancement réel, corriger ce qui doit l'être, puis améliorer au fil des rapports.

**Ce que toi seul peux faire (une seule fois, ≈ 2 h 30) :**
1. Déclarer la micro-entreprise (20 min)
2. Créer le dépôt GitHub (10 min)
3. Acheter le nom de domaine (10 min)
4. Créer le compte Resend et coller 3–4 lignes dans les réglages du domaine (15 min)
5. Créer le compte Stripe et les 2 liens de paiement (40 min, je te guide écran par écran)
6. Créer la clé Claude (5 min)
7. Coller les clés dans GitHub (10 min)
8. Déclarer le site à Google (10 min)
9. Publier les messages de lancement dans quelques groupes Facebook/LinkedIn (1 h 30, une fois)

**Ensuite : ≈ 15 min par semaine.** Tu lis le rapport du lundi, tu réponds aux rares alertes et à quelques e-mails clients, puis tu déclares ton CA à l'URSSAF.

## 8. Le calendrier réaliste
| Quand | Ce qui se passe |
|---|---|
| Semaine 1 | Création des comptes, mise en ligne, premier envoi réel testé sur ton adresse |
| Semaines 2–4 | Lancement (posts), premiers essais gratuits possibles |
| Mois 1–3 | Premiers clients payants (objectif : 4 à 8) ; Google commence à indexer les pages |
| Mois 3–6 | Les pages Google apportent du trafic ; objectif 12 à 20 clients |
| Mois 6–12 | Objectif 30 à 45 clients, soit 200 à 300 € par semaine |

## 9. Les risques, honnêtement
- **Ça peut démarrer lentement** : Google met plusieurs mois à envoyer des visiteurs. Le lancement manuel du début compte beaucoup.
- **Aucun revenu n'est garanti** : un scénario prudent, c'est 5 à 10 clients au bout de 6 mois.
- **Des clients partiront** : un client qui ne gagne aucun marché peut résilier. On le compense par l'acquisition continue.
- **La base publique peut changer de format** : l'agent t'alerte le jour même et je corrige.
- **Concurrence** : de grosses plateformes existent. Notre avantage, c'est que c'est plus simple, ciblé par métier, résumé par l'IA et moins cher.
- **Ce que tu risques financièrement** : environ 10 € (domaine) + environ 5 €/mois (IA). Pas d'engagement, pas de stock, et tu peux arrêter à tout moment.

## 10. Côté légal
- Il faut être déclaré (micro-entreprise) **avant** d'encaisser le premier euro.
- Les mentions légales, CGV et la politique de confidentialité sont prêtes. Il manque ton nom, ton SIRET et ton adresse.
- Les données des appels d'offres sont publiques et réutilisables librement (Licence Ouverte).
- Pas de TVA à facturer tant que tu restes sous le seuil de la franchise en base (mention déjà sur le site).

## 11. Comment on lance
On avance **dans ce fil**, étape par étape : je te donne une étape, tu la fais, tu me dis « fait », je vérifie et je passe à la suivante. Tu peux t'arrêter ou changer d'avis à n'importe quel moment.
