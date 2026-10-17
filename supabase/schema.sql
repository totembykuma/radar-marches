-- Radar Marchés : base privée des comptes clients (Supabase / PostgreSQL).
-- Appliqué automatiquement par le workflow comptes-setup.yml (python -m radar.cli comptes-setup).
-- Idempotent : peut être rejoué sans perte de données.
--
-- Principes de sécurité :
-- * mots de passe, sessions, vérification d'e-mail, réinitialisation : Supabase Auth (jamais dans ces tables) ;
-- * RLS activé partout : un client ne lit que ses propres lignes ;
-- * offre, statut et quotas ne sont JAMAIS écrits par le navigateur : seulement par le robot (clé secrète, GitHub Actions)
--   ou le webhook Stripe ; les actions du client passent par des fonctions qui revérifient ses droits côté serveur.

create extension if not exists pgcrypto;

-- ---------------------------------------------------------------- référentiels (copiés depuis business.yaml / metiers.yaml)
create table if not exists public.offres (
  cle text primary key,
  nom text not null,
  departements_max int not null default 1,
  mots_cles boolean not null default false,
  historique_complet boolean not null default false,
  rapports_mois int not null default 0,
  dossiers_mois int not null default 0,
  rapports_en_essai boolean not null default false
);
create table if not exists public.offres_prix (
  price_id text primary key,
  offre text not null references public.offres(cle) on update cascade
);
create table if not exists public.metiers (
  cle text primary key,
  libelle text not null
);

-- ---------------------------------------------------------------- comptes (1 ligne par utilisateur Supabase vérifié)
create table if not exists public.comptes (
  user_id uuid primary key references auth.users(id) on delete cascade,
  email text not null,
  offre text references public.offres(cle) on update cascade,
  statut text not null default 'aucun',        -- trialing | active | past_due | inactif | aucun
  stripe_customer text,
  stripe_subscription text,
  fin_essai timestamptz,
  source text,                                 -- stripe | fichier | webhook
  parrainage text,                             -- lien de parrainage (abonnés en essai ou payants)
  maj timestamptz not null default now()
);
alter table public.comptes add column if not exists parrainage text;
create index if not exists comptes_email on public.comptes (lower(email));

create table if not exists public.criteres (
  user_id uuid primary key references auth.users(id) on delete cascade,
  metier text references public.metiers(cle) on update cascade,
  departements text[] not null default '{}',
  mots_cles text[] not null default '{}',
  maj timestamptz not null default now(),
  pousse_stripe timestamptz,                   -- dernière copie vers Stripe (lu par l'envoi du matin)
  erreur text
);

-- ---------------------------------------------------------------- historique des alertes envoyées + favoris
create table if not exists public.alertes (
  id bigserial primary key,
  email text not null,                         -- adresse d'envoi (minuscules) ; reliée au compte via comptes.email
  avis_id text not null,
  recu date not null default current_date,
  objet text not null,
  acheteur text,
  lieu text,
  date_limite date,
  metier text,
  url text,
  resume text,
  favori boolean not null default false,
  rappel_envoye date,
  unique (email, avis_id)
);
create index if not exists alertes_email on public.alertes (email, recu desc);
create index if not exists alertes_rappel on public.alertes (date_limite) where favori and rappel_envoye is null;

-- ---------------------------------------------------------------- demandes (rapports « comment gagner », dossiers inclus)
create table if not exists public.demandes (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  type text not null check (type in ('rapport', 'dossier')),
  avis_ref text not null,
  infos text not null default '',
  statut text not null check (statut in ('action_requise', 'recue', 'en_traitement', 'terminee', 'echec', 'annulee')),
  message text,
  cle_idem text not null,
  essais int not null default 0,
  fichier text,                                -- chemin du résultat dans le stockage privé « documents »
  cree timestamptz not null default now(),
  maj timestamptz not null default now(),
  termine timestamptz,
  unique (user_id, cle_idem)
);
create index if not exists demandes_file on public.demandes (statut, cree);

-- ---------------------------------------------------------------- événements Stripe déjà traités (webhook idempotent)
create table if not exists public.stripe_evenements (
  id text primary key,
  type text not null,
  recu timestamptz not null default now()
);

-- ---------------------------------------------------------------- RLS : lecture de ses propres lignes uniquement
alter table public.offres enable row level security;
alter table public.offres_prix enable row level security;
alter table public.metiers enable row level security;
alter table public.comptes enable row level security;
alter table public.criteres enable row level security;
alter table public.alertes enable row level security;
alter table public.demandes enable row level security;
alter table public.stripe_evenements enable row level security;

drop policy if exists offres_lecture on public.offres;
create policy offres_lecture on public.offres for select to anon, authenticated using (true);
drop policy if exists metiers_lecture on public.metiers;
create policy metiers_lecture on public.metiers for select to anon, authenticated using (true);
drop policy if exists comptes_soi on public.comptes;
create policy comptes_soi on public.comptes for select to authenticated using (user_id = auth.uid());
drop policy if exists criteres_soi on public.criteres;
create policy criteres_soi on public.criteres for select to authenticated using (user_id = auth.uid());
drop policy if exists alertes_soi on public.alertes;
create policy alertes_soi on public.alertes for select to authenticated
  using (email = (select lower(c.email) from public.comptes c where c.user_id = auth.uid()));
drop policy if exists demandes_soi on public.demandes;
create policy demandes_soi on public.demandes for select to authenticated using (user_id = auth.uid());
-- Aucune politique d'écriture : toute modification passe par les fonctions ci-dessous (ou par la clé secrète du robot).
-- offres_prix et stripe_evenements : aucune politique = illisibles par les clients.

revoke insert, update, delete on public.offres, public.offres_prix, public.metiers, public.comptes, public.criteres,
  public.alertes, public.demandes, public.stripe_evenements from anon, authenticated;

-- ---------------------------------------------------------------- droits calculés (source unique)
create or replace function public.droits(p_offre text, p_statut text)
returns jsonb language sql stable security definer set search_path = public as $$
  select case
    when p_statut not in ('trialing', 'active', 'past_due') or o.cle is null then
      jsonb_build_object('actif', false, 'veille', false, 'departements_max', 0, 'mots_cles', false,
                         'historique_complet', false, 'rapports_mois', 0, 'dossiers_mois', 0)
    else jsonb_build_object(
      'actif', true, 'veille', true, 'departements_max', o.departements_max, 'mots_cles', o.mots_cles,
      'historique_complet', o.historique_complet,
      'rapports_mois', case when p_statut = 'trialing' and not o.rapports_en_essai then 0 else o.rapports_mois end,
      -- dossiers inclus : à partir du premier paiement (pas pendant l'essai)
      'dossiers_mois', case when p_statut = 'trialing' then 0 else o.dossiers_mois end)
  end
  from (select 1) x left join public.offres o on o.cle = p_offre
$$;

create or replace function public._utilises(p_user uuid, p_type text)
returns int language sql stable security definer set search_path = public as $$
  select count(*)::int from public.demandes
  where user_id = p_user and type = p_type
    and statut not in ('echec', 'annulee')     -- un échec ou une annulation ne consomme pas le quota
    and cree >= date_trunc('month', now() at time zone 'Europe/Paris') at time zone 'Europe/Paris'
$$;

-- Tout l'espace client en un appel (offre, statut, droits, critères, quotas).
create or replace function public.mon_espace()
returns jsonb language plpgsql stable security definer set search_path = public as $$
declare c public.comptes; cr public.criteres; d jsonb; u uuid := auth.uid();
begin
  if u is null then raise exception 'non connecté' using errcode = '28000'; end if;
  select * into c from public.comptes where user_id = u;
  select * into cr from public.criteres where user_id = u;
  d := public.droits(c.offre, coalesce(c.statut, 'aucun'));
  return jsonb_build_object(
    'email', c.email, 'offre', c.offre, 'offre_nom', (select nom from public.offres where cle = c.offre),
    'statut', coalesce(c.statut, 'aucun'), 'fin_essai', c.fin_essai, 'parrainage', c.parrainage,
    'gere_par_stripe', c.stripe_subscription is not null, 'maj', c.maj, 'droits', d,
    'criteres', case when cr.user_id is null then null else jsonb_build_object(
       'metier', cr.metier, 'departements', cr.departements, 'mots_cles', cr.mots_cles, 'maj', cr.maj,
       'applique', cr.pousse_stripe is not null and cr.pousse_stripe >= cr.maj, 'erreur', cr.erreur) end,
    'quotas', jsonb_build_object(
       'rapports_utilises', public._utilises(u, 'rapport'), 'rapports_max', d->'rapports_mois',
       'dossiers_utilises', public._utilises(u, 'dossier'), 'dossiers_max', d->'dossiers_mois'));
end $$;

-- Modification des critères : validée ici (offre, statut, formats), copiée vers Stripe par le robot sous 10 min.
create or replace function public.modifier_criteres(p_metier text, p_departements text[], p_mots_cles text[])
returns jsonb language plpgsql security definer set search_path = public as $$
declare u uuid := auth.uid(); c public.comptes; d jsonb; deps text[]; mots text[]; derniere timestamptz;
begin
  if u is null then raise exception 'non connecté' using errcode = '28000'; end if;
  select * into c from public.comptes where user_id = u;
  d := public.droits(c.offre, coalesce(c.statut, 'aucun'));
  if not (d->>'veille')::boolean then raise exception 'Aucun abonnement actif sur ce compte.' using errcode = '42501'; end if;
  if c.stripe_subscription is null then raise exception 'Abonnement géré par le support : écrivez-nous pour modifier vos critères.' using errcode = '42501'; end if;
  if p_metier is null or not exists (select 1 from public.metiers where cle = p_metier) then
    raise exception 'Métier inconnu.' using errcode = '22023'; end if;
  select coalesce(array_agg(distinct upper(trim(x))), '{}') into deps from unnest(coalesce(p_departements, '{}')) x where trim(x) <> '';
  if exists (select 1 from unnest(deps) x where x !~ '^(0[1-9]|[1-8][0-9]|9[0-5]|2A|2B|97[1-6])$') then
    raise exception 'Département invalide.' using errcode = '22023'; end if;
  if cardinality(deps) > (d->>'departements_max')::int then
    raise exception 'Votre offre permet % département(s).', d->>'departements_max' using errcode = '42501'; end if;
  if (d->>'departements_max')::int = 1 and cardinality(deps) <> 1 then
    raise exception 'Choisissez votre département.' using errcode = '22023'; end if;
  select coalesce(array_agg(left(trim(x), 40)), '{}') into mots from unnest(coalesce(p_mots_cles, '{}')) x where trim(x) <> '';
  if cardinality(mots) > 0 and not (d->>'mots_cles')::boolean then
    raise exception 'Les mots-clés sont inclus dans les offres Pro et Clé en main.' using errcode = '42501'; end if;
  if cardinality(mots) > 20 then raise exception '20 mots-clés au maximum.' using errcode = '22023'; end if;
  if exists (select 1 from unnest(mots) x where x ~ '[,;<>]') then
    raise exception 'Un mot-clé ne peut pas contenir , ; < ou >.' using errcode = '22023'; end if;
  select maj into derniere from public.criteres where user_id = u;
  if derniere is not null and derniere > now() - interval '10 seconds' then
    raise exception 'Modification déjà enregistrée, patientez quelques secondes.' using errcode = '55000'; end if;
  insert into public.criteres (user_id, metier, departements, mots_cles, maj, erreur)
    values (u, p_metier, deps, mots, now(), null)
    on conflict (user_id) do update set metier = excluded.metier, departements = excluded.departements,
      mots_cles = excluded.mots_cles, maj = now(), erreur = null;
  return public.mon_espace();
end $$;

create or replace function public.basculer_favori(p_alerte bigint)
returns boolean language plpgsql security definer set search_path = public as $$
declare v boolean;
begin
  if auth.uid() is null then raise exception 'non connecté' using errcode = '28000'; end if;
  update public.alertes a set favori = not a.favori
    where a.id = p_alerte and a.email = (select lower(c.email) from public.comptes c where c.user_id = auth.uid())
    returning a.favori into v;
  if v is null then raise exception 'Alerte introuvable.' using errcode = '42501'; end if;
  return v;
end $$;

-- Demande de rapport ou de dossier inclus. Idempotente (p_cle générée par le navigateur à l'ouverture du formulaire :
-- un double clic renvoie la même demande) et protégée contre deux demandes simultanées (verrou par client).
create or replace function public.demander(p_type text, p_avis_ref text, p_infos text, p_cle text)
returns public.demandes language plpgsql security definer set search_path = public as $$
declare u uuid := auth.uid(); c public.comptes; d jsonb; r public.demandes; ref text; lim int;
begin
  if u is null then raise exception 'non connecté' using errcode = '28000'; end if;
  if p_type not in ('rapport', 'dossier') then raise exception 'Type inconnu.' using errcode = '22023'; end if;
  if p_cle is null or length(p_cle) not between 8 and 64 then raise exception 'Clé de demande invalide.' using errcode = '22023'; end if;
  select * into r from public.demandes where user_id = u and cle_idem = p_cle;
  if found then return r; end if;
  perform pg_advisory_xact_lock(hashtext(u::text || p_type));
  select * into c from public.comptes where user_id = u;
  d := public.droits(c.offre, coalesce(c.statut, 'aucun'));
  lim := coalesce((d->>(case p_type when 'rapport' then 'rapports_mois' else 'dossiers_mois' end))::int, 0);
  if lim = 0 then
    raise exception '%', case
      when p_type = 'dossier' and c.offre = 'cle' and c.statut = 'trialing' then 'Vos dossiers inclus démarrent à votre premier paiement.'
      else 'Cette demande est réservée à l''offre Clé en main.' end using errcode = '42501';
  end if;
  if public._utilises(u, p_type) >= lim then
    raise exception 'Quota du mois atteint (% sur %).', lim, lim using errcode = '53400'; end if;
  ref := substring(coalesce(p_avis_ref, '') from '(\d{2}-\d{3,7})');
  if ref is null then raise exception 'Indiquez la référence de l''avis (ex. 26-123456).' using errcode = '22023'; end if;
  insert into public.demandes (user_id, type, avis_ref, infos, statut, message, cle_idem)
    values (u, p_type, ref, left(coalesce(p_infos, ''), 4000),
            case p_type when 'rapport' then 'recue' else 'action_requise' end,
            case p_type when 'dossier' then 'Ajoutez le dossier de consultation (PDF, Word ou ZIP), puis validez.' end, p_cle)
    returning * into r;
  return r;
end $$;

-- Dossier : le client a déposé ses pièces dans le stockage privé, il valide l'envoi.
create or replace function public.soumettre_dossier(p_id uuid)
returns public.demandes language plpgsql security definer set search_path = public, storage as $$
declare r public.demandes;
begin
  if auth.uid() is null then raise exception 'non connecté' using errcode = '28000'; end if;
  select * into r from public.demandes where id = p_id and user_id = auth.uid() for update;
  if not found then raise exception 'Demande introuvable.' using errcode = '42501'; end if;
  if r.statut <> 'action_requise' then return r; end if;
  if not exists (select 1 from storage.objects o where o.bucket_id = 'documents'
                 and o.name like auth.uid()::text || '/' || p_id::text || '/dce/%') then
    raise exception 'Ajoutez au moins une pièce du dossier de consultation.' using errcode = '22023'; end if;
  update public.demandes set statut = 'recue', message = null, maj = now() where id = p_id returning * into r;
  return r;
end $$;

create or replace function public.annuler_demande(p_id uuid)
returns public.demandes language plpgsql security definer set search_path = public as $$
declare r public.demandes;
begin
  if auth.uid() is null then raise exception 'non connecté' using errcode = '28000'; end if;
  update public.demandes set statut = 'annulee', maj = now()
    where id = p_id and user_id = auth.uid() and statut = 'action_requise' returning * into r;
  if not found then raise exception 'Seule une demande en attente de pièces peut être annulée.' using errcode = '42501'; end if;
  return r;
end $$;

-- Webhook Stripe (clé secrète uniquement) : enregistre l'événement une seule fois, puis applique l'état réel de
-- l'abonnement (relu chez Stripe par le webhook) au compte vérifié qui porte la même adresse.
create or replace function public.appliquer_abonnement(p_evenement text, p_type text, p_email text, p_price text,
  p_statut text, p_customer text, p_subscription text, p_fin_essai timestamptz)
returns text language plpgsql security definer set search_path = public, auth as $$
declare u uuid; o text; actuel public.comptes;
begin
  insert into public.stripe_evenements (id, type) values (p_evenement, p_type) on conflict do nothing;
  if not found then return 'deja_traite'; end if;
  select id into u from auth.users where lower(email) = lower(p_email) and email_confirmed_at is not null limit 1;
  if u is null then return 'sans_compte'; end if;
  select offre into o from public.offres_prix where price_id = p_price;
  select * into actuel from public.comptes where user_id = u;
  -- un 2e abonnement résilié ne doit pas écraser un abonnement actif du même client
  if actuel.stripe_subscription is not null and actuel.stripe_subscription <> p_subscription
     and actuel.statut in ('trialing', 'active', 'past_due') and p_statut not in ('trialing', 'active', 'past_due') then
    return 'ignore';
  end if;
  insert into public.comptes (user_id, email, offre, statut, stripe_customer, stripe_subscription, fin_essai, source, maj)
    values (u, lower(p_email), o, case when p_statut in ('trialing', 'active', 'past_due') then p_statut else 'inactif' end,
            p_customer, p_subscription, p_fin_essai, 'webhook', now())
    on conflict (user_id) do update set offre = coalesce(excluded.offre, public.comptes.offre), statut = excluded.statut,
      stripe_customer = excluded.stripe_customer, stripe_subscription = excluded.stripe_subscription,
      fin_essai = excluded.fin_essai, source = 'webhook', maj = now();
  return 'applique';
end $$;

-- Les fonctions du client : réservées aux utilisateurs connectés. Celles du robot : clé secrète seulement.
revoke execute on function public.droits(text, text), public._utilises(uuid, text), public.mon_espace(),
  public.modifier_criteres(text, text[], text[]), public.basculer_favori(bigint), public.demander(text, text, text, text),
  public.soumettre_dossier(uuid), public.annuler_demande(uuid),
  public.appliquer_abonnement(text, text, text, text, text, text, text, timestamptz) from public, anon;
grant execute on function public.mon_espace(), public.modifier_criteres(text, text[], text[]), public.basculer_favori(bigint),
  public.demander(text, text, text, text), public.soumettre_dossier(uuid), public.annuler_demande(uuid) to authenticated;
revoke execute on function public.appliquer_abonnement(text, text, text, text, text, text, text, timestamptz) from authenticated;

-- ---------------------------------------------------------------- stockage privé des documents
-- Bucket « documents » (privé, créé par comptes-setup : 20 Mo max, PDF / Word / ZIP).
-- Chemins : <user_id>/<demande_id>/dce/<fichier> (déposé par le client) ; <user_id>/<demande_id>/resultat/<fichier> (robot).
drop policy if exists documents_lecture on storage.objects;
create policy documents_lecture on storage.objects for select to authenticated
  using (bucket_id = 'documents' and (storage.foldername(name))[1] = auth.uid()::text);
drop policy if exists documents_depot on storage.objects;
create policy documents_depot on storage.objects for insert to authenticated
  with check (bucket_id = 'documents' and (storage.foldername(name))[1] = auth.uid()::text
              and (storage.foldername(name))[3] = 'dce'
              and exists (select 1 from public.demandes d where d.id::text = (storage.foldername(name))[2]
                          and d.user_id = auth.uid() and d.type = 'dossier' and d.statut = 'action_requise'));
drop policy if exists documents_retrait on storage.objects;
create policy documents_retrait on storage.objects for delete to authenticated
  using (bucket_id = 'documents' and (storage.foldername(name))[1] = auth.uid()::text
         and (storage.foldername(name))[3] = 'dce'
         and exists (select 1 from public.demandes d where d.id::text = (storage.foldername(name))[2]
                     and d.user_id = auth.uid() and d.statut = 'action_requise'));
