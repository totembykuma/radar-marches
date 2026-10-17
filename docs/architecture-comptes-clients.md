# Comptes clients : architecture, état réel et mise en service

Document à jour au 17 octobre 2026. Il dit ce qui est en place, ce qui est testé, ce qui ne l'est pas encore et
ce qu'il reste à faire. Rien n'est présenté comme opérationnel s'il n'a pas tourné en réel.

## 1. En bref

- **Avant** : l'espace client s'ouvrait sans mot de passe, par un lien personnel reçu par e-mail (`/compte/#…`).
  Les critères se changeaient en envoyant un e-mail prérempli. Rapports et dossiers se demandaient par e-mail.
- **Maintenant (dans le code, pas encore en service)** : vrai compte avec adresse e-mail + mot de passe, adresse vérifiée,
  mot de passe oublié, changement d'adresse confirmé. Dans l'espace : offre et statut réels, critères modifiables en
  direct, historique des alertes, favoris avec rappel 3 jours avant la date limite, demandes de rapports et de dossiers
  avec dépôt des pièces, suivi et téléchargement. Sur l'accueil, un outil public « combien de marchés pour vous ».
- **Ce qui manque pour l'allumer** : une seule action du propriétaire (étape 1 ci-dessous), puis lancer un workflow.
  Tant que ce n'est pas fait, **le site reste exactement comme avant** (l'ancien lien continue de marcher).
- L'ancien accès par lien **n'est pas supprimé**. Il reste en service, avec un bandeau « créez votre mot de passe ».

## 2. Architecture (simple)

```
Navigateur (site statique GitHub Pages)
   │  clé PUBLIQUE seulement ; mots de passe envoyés directement à Supabase Auth (HTTPS)
   ▼
Supabase (projet gratuit « radar-marches », déjà créé)
   ├─ Auth : mots de passe hachés (bcrypt), e-mail vérifié, liens 1 h à usage unique, limites anti-abus
   ├─ Base PostgreSQL : comptes, critères, alertes, favoris, demandes ; RLS = chacun ne lit que ses lignes
   ├─ Stockage privé « documents » : pièces déposées et documents générés, par dossier client
   └─ Fonction « stripe-webhook » : signature Stripe vérifiée, droits mis à jour en temps réel
   ▲
GitHub Actions (le « robot », clé secrète, jamais dans le navigateur)
   ├─ bienvenue.yml (toutes les 10 min) : recopie l'état réel des abonnements Stripe vers les comptes,
   │    applique dans Stripe les critères modifiés dans l'espace, invite une fois les clients existants
   ├─ daily.yml (6 h 20 Paris, jours ouvrés) : alertes + historique « Mes alertes » + rappels des favoris
   └─ dossier.yml (toutes les heures, 7 h - 21 h, lun-sam) : rapports et dossiers demandés depuis l'espace
Stripe : seule référence des paiements. Resend : tous les e-mails (y compris ceux de Supabase, via SMTP).
```

Pourquoi ce choix : GitHub Pages ne peut ni garder un mot de passe ni protéger des données. Il fallait un service
d'authentification reconnu ; Supabase fait authentification + base + stockage + fonction serveur dans une seule offre
gratuite, et le projet existait déjà. Comparé : Firebase (plus de pièces à assembler, règles moins lisibles), Auth0 /
Clerk (authentification seule, il faudrait encore une base et un stockage), Cloudflare (tout à écrire soi-même, y compris
l'authentification : à éviter). Aucun serveur à administrer.

**Qui décide des droits ?** Jamais le navigateur. L'offre et le statut sont écrits uniquement par le robot (depuis Stripe)
ou par le webhook (qui relit l'abonnement chez Stripe). Les actions du client passent par des fonctions SQL qui
revérifient offre, statut, quotas et formats côté serveur (`supabase/schema.sql`). Le robot revérifie encore avant
d'écrire dans Stripe.

**Rattachement à Stripe** : un compte est relié à l'abonnement qui porte **la même adresse, une fois cette adresse
vérifiée** par le lien de confirmation. Une adresse non vérifiée n'est jamais reliée. Deux abonnements pour la même
adresse (nouvelle tentative de paiement) : on garde l'actif le plus récent. Les boutons de paiement de l'espace
préremplissent l'adresse du compte.

## 3. Matrice des droits (vérifiée dans le code et `config/business.yaml`)

| Fonctionnalité | Visiteur | Essentiel 29 €/290 € | Pro 49 €/490 € | Clé en main 149 €/1 490 € |
|---|---|---|---|---|
| Pages publiques, carte, guides, baromètre, « qui a gagné quoi » | Oui | Oui | Oui | Oui |
| Avis publiés depuis moins de 3 jours | Floutés | Par e-mail | Par e-mail | Par e-mail |
| Outil « combien de marchés pour vous » | Oui | Oui | Oui | Oui |
| Alertes e-mail jours ouvrés | Non | Oui | Oui | Oui |
| Départements | – | 1 | Illimités / France | Illimités / France |
| Mots-clés personnalisés | – | **Non** (voir divergences) | Oui | Oui |
| Fiche « avant de répondre » complète + bilan du lundi | – | Fiche partielle | Oui | Oui |
| Résumé IA, badge « Accessible PME » | – | Oui | Oui | Oui |
| Historique « Mes alertes », favoris + rappel J-3 | – | Oui* | Oui* | Oui* |
| Critères modifiables dans l'espace | – | Oui* | Oui* | Oui* |
| Rapports « comment gagner » | – | Non | Non | 20/mois, essai compris* |
| Dossiers de candidature | – | Achat 199 € | Achat 149 € | 2/mois dès le 1er paiement*, puis 99 € |
| Gestion Stripe (factures, carte, résiliation) | – | Oui | Oui | Oui |

\* une fois les comptes mis en service. « Statut actif » = essai, actif ou paiement en attente (past_due) ; résilié ou
impayé définitif = plus aucun droit. Les limites viennent de `business.yaml` (table `offres` recopiée à chaque synchro).

Badge « Accessible PME » : méthode réelle (`radar/boamp.py`, `_pme`) = procédure adaptée (MAPA, < 90 000 €), marché
national sous les seuils européens, ou lot déclaré adapté aux PME par l'acheteur.

## 4. Audit : ce qui existait et ce qui a été corrigé

Réel et fonctionnel avant cette refonte (déjà vérifié en production les jours précédents) : collecte BOAMP (3
tentatives, collecte anormale = alertes reportées), filtrage métier avec exclusions, dédoublonnage par client
(`state.json`), résumés IA mis en cache et plafonnés (3 $/mois), fiche DECP, bilan Pro du lundi, rappel de fin d'essai
J-3 (une fois, seulement si l'abonnement est encore en essai), bienvenue en 10 min, parrainage (une fois par filleul,
auto-parrainage par même adresse bloqué), promo Pro (`promo.py` + `promo.yml`, codes PROjjmm limités à 5/jour, 29 € 3 mois),
dossier à l'unité avec remboursement automatique si la date limite est à moins de 5 jours, pages publiques floutées.

Problèmes trouvés :

1. **Heure de la veille fausse en hiver** : le déclenchement à 4 h 20 UTC donnait 6 h 20 l'été mais 5 h 20 l'hiver (dès le
   25/10). Corrigé : deux horaires (4 h 20 et 5 h 20 UTC) et une « garde » qui ne laisse passer qu'un envoi par jour, pas
   avant 6 h à Paris. GitHub peut retarder un départ de quelques minutes à environ une heure ; `veille.yml` relance
   l'envoi s'il n'a pas eu lieu à 10 h.
2. **Pas de vrai compte** : lien sans mot de passe (pratique mais transférable), critères et demandes par e-mail. Corrigé
   par les comptes (en attente de mise en service).
3. **Empreintes d'adresses dans le dépôt public** (`data/state.json`, `data/dossiers.json`) : ce sont des empreintes
   SHA-256 tronquées, sans sel. Quelqu'un qui connaît déjà une adresse peut vérifier qu'elle est cliente. Risque faible,
   **non corrigé** ici (le changer referait partir des e-mails de bienvenue) ; à traiter en passant ces états dans Supabase.
4. Parrainage : l'auto-parrainage avec deux adresses différentes reste possible (limite connue, impact : 1 mois offert).

Divergences entre la demande et l'existant (rien n'a été changé sans votre accord) :

- **Mots-clés pour Essentiel** : la demande les cite, le code et la FAQ les réservent à Pro et Clé en main. Conservé tel quel.
- **Rapports Clé en main pendant l'essai** : le code les permet (« essai compris ») ; les dossiers inclus attendent le
  1er paiement. Conservé.
- **Promo Pro** : présente dans le code et dans Stripe (vérifiée le 15/10). Pas retestée dans cette refonte.
- **Rapport « dans l'heure »** : le traitement passe toutes les heures de 7 h à 21 h du lundi au samedi ; une demande à
  21 h 30 attend le lendemain matin.

## 5. Ce qui est opérationnel, ce qui ne l'est pas encore

Testé ici (voir § 7) mais **pas encore en service** (il manque l'étape 1) : tout ce qui touche aux comptes.

| Élément | État |
|---|---|
| Base privée, règles d'accès, quotas, double clic, webhook rejoué | Testé sur un vrai PostgreSQL (RLS réelle) |
| Pages connexion / inscription / oubli / confirmation / espace | Testées dans Chromium avec un Supabase simulé |
| Synchronisation Stripe → comptes, critères → Stripe, historique, favoris, demandes | Testés avec une base simulée |
| Envoi réel des e-mails de confirmation par Supabase via Resend | **Non testé** (après l'étape 1) |
| Webhook Stripe déployé | **Non testé** ; si la clé Stripe restreinte n'a pas le droit « webhooks », il n'est pas créé et les droits se mettent à jour toutes les 10 min (prévu, sans gêne) |
| Rapports / dossiers depuis l'espace avec la vraie IA | **Non testé en réel** (même code de rédaction que par e-mail, lui aussi jamais utilisé par un vrai client) |
| Outil de découverte public | Testé (vrais chiffres du jour, aucun chiffre inventé) ; en ligne au prochain passage du site après fusion |
| Correction de l'heure d'hiver | Active dès la fusion |

Volontairement non fait : connexion par lien magique (possible plus tard, désactivé comme méthode principale),
CAPTCHA (à ajouter si des inscriptions automatiques apparaissent), suppression du compte en libre-service (sur demande
par e-mail, sous 30 jours), dépôt automatique des candidatures (jamais : le client signe et dépose lui-même).

## 6. Mise en service : ce qu'il vous reste à faire

### Étape 1 (vous, 3 minutes, une seule fois) : le jeton Supabase

1. Ouvrez https://supabase.com/dashboard/account/tokens (connecté au compte qui contient le projet « radar-marches »).
2. Bouton **Generate new token**, nom `radar-github`, durée la plus longue proposée. Copiez le jeton (il commence par `sbp_`).
3. Ouvrez https://github.com/totembykuma/radar-marches/settings/secrets/actions → **New repository secret**.
   Nom : `SUPABASE_ACCESS_TOKEN` ; valeur : le jeton collé. **Add secret**.

Rien d'autre à copier : le robot lit lui-même les clés du projet avec ce jeton.

### Étape 2 (Claude ou vous, 1 clic) : lancer « Comptes clients (mise en service) »

Onglet Actions → « Comptes clients (mise en service) » → Run workflow. Il fait tout seul : tests, base, règles d'accès,
stockage privé, réglages de connexion (e-mail vérifié, mot de passe ≥ 10 caractères, liens 1 h, e-mails en français
envoyés par Resend depuis alertes@radar-marches.fr), secrets du webhook, webhook Stripe (si permis), première
synchronisation, puis publication du site avec la connexion par mot de passe.

### Étape 3 (vous, 5 minutes) : le vrai test

Créez un compte avec votre propre adresse sur /compte/inscription/, confirmez, connectez-vous, testez « mot de passe
oublié ». Avant cela, rien n'est annoncé comme fonctionnel.

### Stripe
Rien à faire si le webhook se crée tout seul. Sinon, rien non plus : la synchro toutes les 10 min suffit. Produits et prix
inchangés (aucun produit créé). Portail client inchangé.

### Resend
Rien à faire : la même clé envoie aussi les e-mails de connexion. Attention au quota gratuit (100/jour, 3 000/mois)
partagé avec la prospection (20/jour) : les e-mails de connexion restent rares (quelques-uns par client).

### Secrets et variables (noms seulement)
- Nouveau, obligatoire : `SUPABASE_ACCESS_TOKEN`.
- Facultatifs, plus restrictifs (si un jour vous préférez ne pas confier le jeton du compte) : `SUPABASE_URL` + `SUPABASE_SERVICE_KEY` (prioritaires s'ils existent), `SUPABASE_PROJECT_REF`.
- Déjà en place et réutilisés : `STRIPE_SECRET_KEY`, `RESEND_API_KEY`, `RESEND_INBOUND_KEY`, `ANTHROPIC_API_KEY`, `OWNER_EMAIL`.
- Posés automatiquement côté Supabase (fonction webhook) : `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`, `RADAR_SERVICE_KEY`.
- Public par conception, écrit dans le dépôt : `config/comptes.json` (adresse du projet + clé publique, protégées par les règles d'accès).

Sécurité du jeton : il donne accès à tout le compte Supabase (il ne contient que ce projet). Il reste dans les secrets
GitHub, masqué dans les journaux ; la clé secrète du projet lue avec lui est aussi masquée.

## 7. Tests exécutés

`python3 -m pytest -q tests` : **72 tests, tous OK** (49 existants + 23 nouveaux).

- `tests/test_comptes_sql.py` (vrai PostgreSQL 16, imitation minimale de Supabase) : droits identiques SQL/Python pour
  chaque offre et statut, isolation entre clients (lecture, favoris, demandes, fichiers), impossibilité de modifier son
  offre/statut/quotas depuis le navigateur, visiteur non connecté refusé, critères selon l'offre (1 département
  Essentiel, mots-clés Pro), double clic, quotas (20 rapports, un échec ne compte pas), dossiers inclus seulement après
  le 1er paiement, dépôt de pièces seulement dans son dossier, webhook reçu deux fois, compte non vérifié jamais relié,
  ancien abonnement résilié qui n'écrase pas l'actif.
- `tests/test_comptes.py` : synchro Stripe (adresse vérifiée, majuscules, doublons, résiliation), critères poussés vers
  Stripe et refusés hors offre, modification pendant la copie, historique sans doublon, rappels J-3 uniques et seulement
  pour les abonnés actifs, demandes (succès, Claude indisponible → nouvel essai puis échec non décompté, droits retirés,
  reprise après arrêt brutal, pièces illisibles, délai trop court, pièces effacées après usage), conservation (13 mois / 6
  mois), invitation unique, pages générées en `noindex` et hors sitemap, aucun secret ni fichier client dans le dépôt.
- Navigateur (Chromium, `tests/navigateur/espace.mjs`, Supabase simulé) : 22 vérifications OK (mauvais mot de passe
  sans révéler l'adresse, ouverture de l'espace, textes non interprétés comme HTML, lien `javascript:` écarté, favori,
  refus serveur affiché, double clic = 1 demande, déconnexion, inscription préremplie, jeton retiré de l'adresse,
  nouveau mot de passe, ancien lien toujours fonctionnel, aucune erreur JavaScript ni de politique de sécurité).

Non testable d'ici (réseau bloqué) : Supabase, Stripe et Resend réels. Ce sera l'objet de l'étape 3.

## 8. Coûts et limites (offres gratuites d'après supabase.com/pricing, à reconfirmer à la mise en service)

- Supabase gratuit : 50 000 utilisateurs actifs/mois, base 500 Mo, stockage 1 Go, 2 projets. **Un projet gratuit se met
  en pause après 7 jours sans activité** : la synchro toutes les 10 min l'en empêche ; s'il était en pause, la mise en
  service le réveille. Passage payant (25 $/mois) seulement si on dépasse ces limites (des centaines de clients).
- Resend gratuit : 100 e-mails/jour, 3 000/mois (inchangé).
- GitHub Actions : dépôt public = minutes gratuites.
- IA : inchangé (résumés plafonnés à 3 $/mois ; rapports et dossiers seulement à la demande d'un client autorisé, dans ses quotas).
- **Coût fixe ajouté : 0 €.**

## 9. Retour arrière

- Désactiver les mots de passe sans rien perdre : supprimer `config/comptes.json` (le site revient à l'ancien espace par
  lien au prochain passage de site.yml). Les données restent dans Supabase.
- Annuler toute la refonte : « Revert » de la PR sur GitHub. L'ancien espace par lien n'a jamais été coupé.
- Le webhook Stripe peut être supprimé dans Stripe → Développeurs → Webhooks, sans effet sur les paiements.

## 10. Checklist de mise en production

- [ ] PR relue et fusionnée (tests verts sur GitHub).
- [ ] Secret `SUPABASE_ACCESS_TOKEN` ajouté.
- [ ] Workflow « Comptes clients (mise en service) » vert ; noter si le webhook Stripe est créé.
- [ ] `config/comptes.json` présent sur `main`, site republié, /compte/ affiche « Se connecter ».
- [ ] Test réel : inscription, e-mail de confirmation reçu (expéditeur Radar Marchés), connexion, mot de passe oublié.
- [ ] Le client existant reçoit son invitation, crée son mot de passe, voit « Pro · essai gratuit en cours ».
- [ ] Une modification de critères dans l'espace arrive dans Stripe (métadonnées) et l'e-mail de confirmation part.
- [ ] Lendemain matin : « Mes alertes » se remplit.
- [ ] Plus tard (quand tout est confirmé et que les clients ont un mot de passe) : décider si l'ancien lien est retiré.

## 11. Fichiers

Créés : `supabase/schema.sql`, `supabase/functions/stripe-webhook/index.ts`, `radar/comptes.py`, `radar/comptes_setup.py`,
`static/compte.js`, `static/vendor/supabase-2.99.3.js` (bibliothèque officielle, licence MIT, servie par le site),
`templates/_compte_lien.html` (ancien espace, inchangé sur le fond), `templates/email_auth.html`,
`templates/email_rappel_favori.html`, `.github/workflows/comptes-setup.yml`, `tests/test_comptes.py`,
`tests/test_comptes_sql.py`, `tests/supabase_stub.sql`, `tests/navigateur/espace.mjs`, ce document.

Modifiés : `radar/cli.py` (synchro, historique, rappels, demandes, commandes `comptes` et `comptes-setup`),
`radar/site.py` (pages de compte, outil de découverte), `radar/config.py`, `templates/compte.html`, `templates/base.html`,
`templates/index.html`, `templates/email_bienvenue.html`, `templates/email_compte.html`, `templates/email_criteres.html`,
`templates/legal/confidentialite.html`, `static/style.css`, `.github/workflows/daily.yml` (heure d'hiver),
`bienvenue.yml`, `dossier.yml`, `site.yml`, `CLAUDE.md`. Supprimés : aucun.
