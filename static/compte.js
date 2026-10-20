/* Espace client Radar Marchés : connexion par e-mail + mot de passe (Supabase Auth), espace commun aux 3 offres.
   Sécurité : le navigateur n'a que la clé PUBLIQUE ; chaque lecture est filtrée par la base (RLS : ses propres lignes)
   et chaque action passe par une fonction serveur qui revérifie offre, statut et quotas. Aucun texte reçu n'est
   inséré comme HTML (textContent uniquement). */
(function () {
  "use strict";
  var C = JSON.parse(document.getElementById("compte-conf").textContent);
  C.nom = (document.querySelector("meta[name=apple-mobile-web-app-title]") || {}).content || "Radar Marchés";
  var racine = document.getElementById("compte");
  var sb = window.supabase.createClient(C.url, C.cle, { auth: { persistSession: true, autoRefreshToken: true, detectSessionInUrl: false, flowType: "pkce" } });
  var $ = function (id) { return document.getElementById(id); };
  var VUES = ["v-chargement", "v-connexion", "v-inscription", "v-oubli", "v-confirmation", "v-espace"];
  var STATUTS = { trialing: "essai gratuit en cours", active: "actif", past_due: "paiement en attente", inactif: "terminé", aucun: "aucun abonnement" };
  var ETATS = { action_requise: "Pièces à ajouter", recue: "Demande reçue", en_traitement: "En préparation", terminee: "Prêt", echec: "Non réalisé", annulee: "Annulée" };

  function vue(id) { VUES.forEach(function (v) { $(v).hidden = v !== id; }); document.body.classList.toggle("app-on", id === "v-espace"); window.scrollTo(0, 0); }
  function msg(id, texte, ok) { var m = $(id); m.textContent = texte; m.className = "msg " + (ok ? "msg-ok" : "msg-err"); m.hidden = !texte; }
  function attente(form, oui) { Array.prototype.forEach.call(form.querySelectorAll("button,input,select,textarea"), function (e) { e.disabled = oui; }); }
  function el(tag, texte, cls) { var e = document.createElement(tag); if (texte != null) e.textContent = texte; if (cls) e.className = cls; return e; }
  function dateFr(d) { if (!d) return ""; var p = String(d).slice(0, 10).split("-"); return p[2] + "/" + p[1] + "/" + p[0]; }
  function aujourdhui() { return new Date().toLocaleDateString("en-CA", { timeZone: "Europe/Paris" }); }   // AAAA-MM-JJ, heure de Paris
  function lienSur(u) { return /^https:\/\//.test(u || "") ? u : null; }
  function cleUnique() { return (window.crypto && crypto.randomUUID) ? crypto.randomUUID() : String(Date.now()) + Math.random().toString(36).slice(2); }
  function erreurServeur(e) { return (e && e.message && !/JWT|fetch|network/i.test(e.message)) ? e.message : "Service momentanément indisponible. Réessayez dans un instant."; }
  function lienPaiement(base, email) {
    var u = new URL(base); u.searchParams.set("prefilled_email", email); return u.toString();
  }

  // ------------------------------------------------------------ connexion / inscription / oubli
  $("f-connexion").addEventListener("submit", function (ev) {
    ev.preventDefault();
    var f = this, email = $("c-email").value.trim(), mdp = $("c-mdp").value;
    if (!email || !mdp) return msg("m-connexion", "Saisissez votre adresse e-mail et votre mot de passe.");
    attente(f, true);
    sb.auth.signInWithPassword({ email: email, password: mdp }).then(function (r) {
      attente(f, false);
      if (!r.error) return ouvrirEspace();
      if (r.error.code === "email_not_confirmed") {
        msg("m-connexion", "Confirmez d'abord votre adresse : cliquez sur le lien reçu par e-mail. Un nouveau lien vient d'être envoyé.");
        sb.auth.resend({ type: "signup", email: email, options: { emailRedirectTo: C.site + "/compte/confirmation/" } });
      } else if (r.error.status === 429) {
        msg("m-connexion", "Trop de tentatives. Patientez quelques minutes avant de réessayer.");
      } else {
        msg("m-connexion", "Adresse e-mail ou mot de passe incorrect.");   // sans dire si l'adresse existe
      }
    });
  });

  var OUVERTURE = Date.now();
  $("f-inscription").addEventListener("submit", function (ev) {
    ev.preventDefault();
    var f = this, email = $("i-email").value.trim(), mdp = $("i-mdp").value;
    if (!/^[^@\s]+@[^@\s]+\.[a-z]{2,}$/i.test(email)) return msg("m-inscription", "Adresse e-mail invalide.");
    if (mdp.length < 10) return msg("m-inscription", "Le mot de passe doit faire au moins 10 caractères.");
    if (mdp !== $("i-mdp2").value) return msg("m-inscription", "Les deux mots de passe ne sont pas identiques.");
    if (!$("i-cgv").checked) return msg("m-inscription", "Merci d'accepter les conditions de vente et la politique de confidentialité.");
    // Anti-robots sans service payant : champ piège invisible rempli, ou formulaire envoyé moins de 3 s après l'ouverture.
    if ($("i-site").value || Date.now() - OUVERTURE < 3000) return msg("m-inscription", "Merci de patienter quelques secondes puis de réessayer.");
    attente(f, true);
    sb.auth.signUp({ email: email, password: mdp, options: { emailRedirectTo: C.site + "/compte/confirmation/", data: souhaits() } })
      .then(function (r) {
        attente(f, false);
        if (r.error && r.error.status === 429) return msg("m-inscription", "Trop de demandes. Réessayez dans quelques minutes.");
        if (r.error && /password/i.test(r.error.code || r.error.message)) return msg("m-inscription", "Mot de passe trop faible : allongez-le ou mélangez lettres et chiffres.");
        if (r.error) return msg("m-inscription", erreurServeur(r.error));
        f.reset();
        msg("m-inscription", "C'est presque fini : ouvrez l'e-mail envoyé à " + email + " et cliquez sur « Confirmer mon adresse » (même depuis votre téléphone). " +
            "Cette page s'ouvrira alors toute seule. Rien reçu ? Regardez dans les courriers indésirables. Si un compte existe déjà avec cette adresse, connectez-vous ou utilisez « Mot de passe oublié ».", true);
        attendreConfirmation(email, mdp);
      });
  });

  // Après l'inscription, la confirmation se fait souvent sur un autre appareil (téléphone) :
  // cette page retente la connexion en silence (toutes les 15 s et au retour sur l'onglet), 30 min au plus.
  // Le mot de passe reste seulement en mémoire de cet onglet et est oublié dès la fin.
  function attendreConfirmation(email, mdp) {
    var fin = Date.now() + 30 * 60 * 1000, encours = false, minuterie;
    function arreter() { mdp = null; clearInterval(minuterie); document.removeEventListener("visibilitychange", essayer); window.removeEventListener("focus", essayer); }
    function essayer() {
      if (encours || !mdp || document.hidden || $("v-inscription").hidden) return;
      if (Date.now() > fin) return arreter();
      encours = true;
      sb.auth.signInWithPassword({ email: email, password: mdp }).then(function (r) {
        encours = false;
        if (!r.error) { arreter(); return ouvrirEspace(); }
        if (r.error.code !== "email_not_confirmed" && r.error.status !== 429) arreter();   // compte déjà existant, autre mot de passe…
      }, function () { encours = false; });
    }
    minuterie = setInterval(essayer, 15000);
    document.addEventListener("visibilitychange", essayer);
    window.addEventListener("focus", essayer);
  }

  $("f-oubli").addEventListener("submit", function (ev) {
    ev.preventDefault();
    var f = this, email = $("o-email").value.trim();
    if (!email) return msg("m-oubli", "Saisissez votre adresse e-mail.");
    attente(f, true);
    sb.auth.resetPasswordForEmail(email, { redirectTo: C.site + "/compte/confirmation/" }).then(function (r) {
      attente(f, false);
      if (r.error && r.error.status === 429) return msg("m-oubli", "Trop de demandes. Réessayez dans quelques minutes.");
      msg("m-oubli", "Si un compte existe pour cette adresse, un lien vient d'être envoyé (valable 1 heure).", true);
    });
  });

  function souhaits() {   // choix faits sur le site (outil de découverte) : simple préférence, jamais un droit
    var q = new URLSearchParams(location.search), d = {};
    ["offre", "metier", "dep"].forEach(function (k) { var v = (q.get(k) || "").slice(0, 40); if (/^[a-z0-9-]*$/i.test(v) && v) d["souhait_" + k] = v; });
    return d;
  }

  // ------------------------------------------------------------ arrivée depuis un lien d'e-mail
  function confirmation() {
    vue("v-confirmation");
    var q = new URLSearchParams(location.search), th = q.get("token_hash"), type = q.get("type");
    history.replaceState(null, "", location.pathname);   // le jeton ne reste ni dans la barre d'adresse ni dans l'historique
    if (!th || ["signup", "recovery", "email_change", "email", "magiclink"].indexOf(type) < 0) {
      $("cf-titre").textContent = "Lien incomplet";
      msg("m-confirmation", "Ce lien est incomplet. Copiez le lien entier depuis l'e-mail, ou demandez-en un nouveau.");
      $("cf-suite").hidden = false; return;
    }
    sb.auth.verifyOtp({ token_hash: th, type: type }).then(function (r) {
      if (r.error) {
        $("cf-titre").textContent = "Lien expiré ou déjà utilisé";
        msg("m-confirmation", "Ce lien ne fonctionne plus (il est valable 1 heure, une seule fois). Connectez-vous, ou demandez un nouveau lien.");
        $("cf-suite").hidden = false; return;
      }
      if (type === "recovery") {
        $("cf-titre").textContent = "Choisissez votre nouveau mot de passe";
        $("f-nouveau").hidden = false; return;
      }
      $("cf-titre").textContent = type === "email_change" ? "Adresse confirmée" : "Adresse confirmée, bienvenue !";
      msg("m-confirmation", type === "email_change" ? "Si vous changiez d'adresse, confirmez aussi depuis l'autre boîte mail si un second e-mail vous est parvenu." : "Votre compte est actif. Ouverture de votre espace…", true);
      setTimeout(function () { location.href = C.root + "compte/"; }, 1800);
    });
  }
  $("f-nouveau").addEventListener("submit", function (ev) {
    ev.preventDefault();
    var f = this, mdp = $("n-mdp").value;
    if (mdp.length < 10) return msg("m-nouveau", "Au moins 10 caractères.");
    if (mdp !== $("n-mdp2").value) return msg("m-nouveau", "Les deux mots de passe ne sont pas identiques.");
    attente(f, true);
    sb.auth.updateUser({ password: mdp }).then(function (r) {
      attente(f, false);
      if (r.error) return msg("m-nouveau", r.error.code === "same_password" ? "Choisissez un mot de passe différent de l'ancien." : "Mot de passe refusé : allongez-le ou mélangez lettres et chiffres.");
      msg("m-nouveau", "Mot de passe enregistré. Ouverture de votre espace…", true);
      setTimeout(function () { location.href = C.root + "compte/"; }, 1500);
    });
  });

  // ------------------------------------------------------------ espace client : tableau de bord
  // Une rubrique = une « page » (#apercu, #marches…) ; tous les chiffres viennent de la base (aucune valeur inventée).
  var E = null, ALERTES = [], DEMANDES = [], USER = null, RECUS30 = null, H_FILTRE = "toutes", H_MAX = 30, M_FILTRE = "tous", PREMIERE = true;
  var PAGES = ["apercu", "marches", "echeances", "favoris", "veille", "alertes", "demandes", "abonnement", "profil", "parrainage", "aide"];
  var ALIAS = { criteres: "veille", parametres: "profil" };   // anciens liens (e-mails déjà envoyés)
  var JOURS = 86400000;

  function jour(d) { var p = String(d).slice(0, 10).split("-"); return Date.UTC(+p[0], p[1] - 1, +p[2]); }
  function ecart(d) { return Math.round((jour(d) - jour(aujourdhui())) / JOURS); }   // jours entre aujourd'hui et d
  function moinsJours(n) { return new Date(jour(aujourdhui()) - n * JOURS).toISOString().slice(0, 10); }
  function ouvert(a) { return !a.date_limite || a.date_limite >= aujourdhui(); }
  function euros(c) { var v = c / 100; return (v % 1 ? v.toFixed(2).replace(".", ",") : String(v)) + " €"; }
  function dateLongue(d) { return new Date(jour(d)).toLocaleDateString("fr-FR", { weekday: "long", day: "numeric", month: "long", timeZone: "UTC" }); }
  function svg(id) { var s = document.createElementNS("http://www.w3.org/2000/svg", "svg"), u = document.createElementNS("http://www.w3.org/2000/svg", "use"); s.setAttribute("class", "ic"); s.setAttribute("aria-hidden", "true"); u.setAttribute("href", "#i-" + id); s.appendChild(u); return s; }
  function vider(id) { var n = $(id); n.textContent = ""; return n; }
  var minuteToast;
  function toast(t) { var n = $("toast"); n.textContent = t; n.hidden = false; clearTimeout(minuteToast); minuteToast = setTimeout(function () { n.hidden = true; }, 3200); }
  function compter(n, v) {   // petite animation des indicateurs
    var e = $(n), debut = null, dep = 0;
    if (!window.requestAnimationFrame || matchMedia("(prefers-reduced-motion: reduce)").matches || v < 2) { e.textContent = v; return; }
    requestAnimationFrame(function pas(t) { debut = debut || t; var x = Math.min(1, (t - debut) / 600); e.textContent = Math.round(dep + (v - dep) * (1 - Math.pow(1 - x, 3))); if (x < 1) requestAnimationFrame(pas); });
  }
  function heureParis() { var p = {}; new Intl.DateTimeFormat("en-GB", { timeZone: "Europe/Paris", weekday: "short", hour: "2-digit", minute: "2-digit", hour12: false }).formatToParts(new Date()).forEach(function (x) { p[x.type] = x.value; }); return p; }
  function prochainEnvoi() {   // alertes chaque matin ouvré vers 6 h 20 (heure de Paris)
    var p = heureParis(), j = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"].indexOf(p.weekday), avant = +p.hour * 60 + +p.minute < 6 * 60 + 20;
    if (j < 5 && avant) return "ce matin vers 6\u00a0h\u00a020";
    if (j < 4 || j === 6) return "demain matin vers 6\u00a0h\u00a020";
    return "lundi matin vers 6\u00a0h\u00a020";
  }

  // ---- navigation
  function pageCourante() { var h = location.hash.slice(1); h = ALIAS[h] || h; return PAGES.indexOf(h) >= 0 && !(h === "demandes" && $("nav-demandes").hidden) ? h : "apercu"; }
  function router() {
    if (!E) return;
    var p = pageCourante();
    PAGES.forEach(function (x) { $("p-" + x).hidden = x !== p; });
    Array.prototype.forEach.call(document.querySelectorAll("#side-nav a, .app-tabs a"), function (a) {
      if (a.getAttribute("href") === "#" + p) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current");
    });
    $("page-titre").textContent = $("p-" + p).getAttribute("data-titre");
    document.title = $("p-" + p).getAttribute("data-titre") + " · Mon compte · " + C.nom;
    $("top-veille").hidden = p === "veille" || !(E.droits || {}).veille;
    menu(false);
    if (!PREMIERE) { window.scrollTo(0, 0); $("page-titre").focus({ preventScroll: true }); }
    PREMIERE = false;
    if (p === "alertes") afficherHistorique();
  }
  window.addEventListener("hashchange", router);
  function menu(ouvrir) {
    $("v-espace").classList.toggle("menu-ouvert", ouvrir); $("app-voile").hidden = !ouvrir;
    $("menu-btn").setAttribute("aria-expanded", ouvrir);
    if (ouvrir) $("side-nav").querySelector("a").focus();
  }
  $("menu-btn").addEventListener("click", function () { menu(true); });
  $("tab-menu").addEventListener("click", function () { menu(!$("v-espace").classList.contains("menu-ouvert")); });
  $("app-voile").addEventListener("click", function () { menu(false); });
  document.addEventListener("keydown", function (ev) { if (ev.key === "Escape" && $("v-espace").classList.contains("menu-ouvert")) { menu(false); $("menu-btn").focus(); } });

  // ---- chargement
  function ouvrirEspace() {
    vue("v-chargement"); $("v-chargement-txt").textContent = "Chargement de votre espace…"; $("v-reessayer").hidden = true;
    Promise.all([
      sb.rpc("mon_espace"),
      sb.from("alertes").select("id,avis_id,recu,objet,acheteur,lieu,date_limite,metier,url,resume,favori,rappel_envoye").order("recu", { ascending: false }).limit(300),
      sb.from("demandes").select("id,type,avis_ref,statut,message,cree,termine,fichier").order("cree", { ascending: false }).limit(60),
      sb.auth.getUser(),
      sb.from("alertes").select("id", { count: "exact", head: true }).gte("recu", moinsJours(30))
    ]).then(function (r) {
      if (r[0].error || r[1].error || !r[3].data.user) return echecChargement(r[0].error || r[1].error);
      E = r[0].data; USER = r[3].data.user; E_uid = USER.id; E.email = E.email || USER.email;
      ALERTES = r[1].data || []; DEMANDES = r[2].data || []; RECUS30 = r[4].error ? null : r[4].count;
      toutAfficher();
      vue("v-espace");
      router();
    }, echecChargement);
  }
  function echecChargement(e) {
    vue("v-chargement");
    $("v-chargement-txt").textContent = erreurServeur(e); $("v-reessayer").hidden = false;
  }
  $("v-reessayer").addEventListener("click", ouvrirEspace);

  function toutAfficher() {
    afficherIdentite(); afficherAbonnement(); afficherCriteres(); afficherDemandes(); afficherParrainage();
    afficherMarches(); afficherApercu();
  }

  // ---- identité (barre latérale, profil)
  function afficherIdentite() {
    var mail = USER.email || "";
    $("u-init").textContent = (mail[0] || "?").toUpperCase(); $("u-email").textContent = mail;
    var u = $("u-offre"); u.textContent = E.offre_nom ? E.offre_nom + " · " + ({ trialing: "essai", active: "actif", past_due: "impayé", inactif: "terminé" }[E.statut] || E.statut) : "Sans abonnement"; u.className = "pastille st-" + E.statut;
    $("p-email-actuel").textContent = mail; $("p-email-alertes").textContent = E.email || mail;
    $("p-email").value = USER.new_email || "";
  }

  // ---- abonnement
  function offresDans(box) {
    var meta = USER.user_metadata || {};
    box.textContent = "";
    Object.keys(C.offres).forEach(function (k) {
      var o = C.offres[k], div = el("div", null, "offre-mini" + (meta.souhait_offre === k ? " choisie" : ""));
      div.appendChild(el("b", o.nom)); div.appendChild(el("span", o.grille || (o.prix + " €/mois" + (o.annuel ? " ou " + o.annuel + " €/an" : ""))));
      var a = el("a", "Essayer " + C.essai + " jours", "btn btn-sm"); a.href = lienPaiement(o.lien, USER.email); div.appendChild(a);
      if (o.lien_annuel) { var b = el("a", "Payer " + o.annuel + " € pour 12 mois", "btn btn-sm btn-clair"); b.href = lienPaiement(o.lien_annuel, USER.email); div.appendChild(b); }
      box.appendChild(div);
    });
    if (meta.souhait_metier) box.appendChild(el("p", "Votre choix sur le site : " + (C.metiers_nom[meta.souhait_metier] || meta.souhait_metier) +
      (meta.souhait_dep ? ", département " + meta.souhait_dep : "") + ". Vous le saisirez sur la page de paiement.", "muted petit"));
  }
  function afficherAbonnement() {
    var d = E.droits || {}, actif = d.veille, ab = E.abonnement || {}, portail = lienPaiement(C.portail, USER.email);
    $("e-offre").textContent = E.offre_nom || "Aucune offre";
    var st = $("e-statut"); st.textContent = STATUTS[E.statut] || E.statut; st.className = "pastille pastille-grande st-" + E.statut;
    var dl = vider("e-details");
    function ligne(t, v) { if (!v) return; var x = el("div"); x.appendChild(el("dt", t)); x.appendChild(el("dd", v)); dl.appendChild(x); }
    if (ab.prix_cents != null) ligne("Prix", euros(ab.prix_cents) + (ab.periode === "an" ? " par an" : " par mois"));
    if (E.statut === "trialing") { ligne("Essai gratuit jusqu'au", dateFr(E.fin_essai)); ligne("Premier prélèvement", dateFr(E.fin_essai)); }
    else if (ab.renouvellement && actif) ligne(ab.resilie ? "Fin de l'abonnement" : "Prochaine échéance", dateFr(ab.renouvellement));
    if (ab.periode && actif) ligne("Renouvellement", ab.resilie ? "Arrêt programmé" : (ab.periode === "an" ? "Annuel, automatique" : "Mensuel, automatique"));
    if (ab.debut && actif) ligne("Client depuis", dateFr(ab.debut));
    if (E.criteres && E.criteres.metier && actif) ligne("Métier suivi", C.metiers_nom[E.criteres.metier] || E.criteres.metier);
    dl.hidden = !dl.children.length;
    $("e-sans-abo").hidden = actif;
    $("e-impaye").hidden = E.statut !== "past_due"; $("e-impaye-lien").href = portail;
    var res = $("e-resilie");
    res.hidden = !(ab.resilie && ab.renouvellement && actif);
    if (!res.hidden) { res.textContent = "Votre abonnement s'arrête le " + dateFr(ab.renouvellement) + ". Vous pouvez le reprendre à tout moment d'ici là. "; var a = el("a", "Reprendre", "btn btn-sm"); a.href = portail; res.appendChild(a); }
    $("e-portail-p").hidden = !E.gere_par_stripe && !E.offre; $("e-portail-aide").hidden = $("e-portail-p").hidden;
    Array.prototype.forEach.call(document.querySelectorAll("[data-portail]"), function (a) { a.href = portail; });
    $("e-renouv-titre").textContent = ab.resilie ? "Reprendre l'abonnement" : "Résilier";
    $("e-renouv-txt").textContent = ab.resilie ? "Annuler l'arrêt programmé" : "Sans frais, en un clic";
    $("e-sans").hidden = actif;
    if (!actif) {
      $("e-sans-txt").textContent = E.statut === "inactif" ? "Votre abonnement est terminé. Vous pouvez le reprendre à tout moment :"
        : "Aucun abonnement n'est relié à " + USER.email + ". Démarrez votre essai gratuit de " + C.essai + " jours :";
      $("a-sans-titre").textContent = E.statut === "inactif" ? "Reprendre ma veille" : "Démarrez votre veille";
      offresDans($("e-offres")); offresDans($("e-offres2"));
    }
  }

  // ---- critères (modifiables dans les limites de l'offre ; la base refuse tout dépassement)
  function afficherCriteres() {
    var d = E.droits || {}, actif = d.veille, cr = E.criteres || {}, max = d.departements_max || 0;
    var sel = $("k-metier"); sel.textContent = "";
    C.metiers.forEach(function (m) { var o = el("option", m[1]); o.value = m[0]; sel.appendChild(o); });
    if (cr.metier) sel.value = cr.metier;
    $("k-deps").value = (cr.departements || []).join(", ");
    $("k-mots").value = (cr.mots_cles || []).join(", ");
    $("k-mots-p").hidden = !d.mots_cles;
    $("k-deps-label").textContent = max === 1 ? "Département (1 inclus dans votre offre)" : "Départements (séparés par des virgules)";
    $("k-deps-aide").textContent = max === 1 ? "Ex. 33. Plusieurs départements ou toute la France : offre Pro." : "Laissez vide pour toute la France.";
    attente($("f-criteres"), !actif || !E.gere_par_stripe);
    $("m-criteres").hidden = true;
    if (!actif) msg("m-criteres", "Les critères se modifient pendant un abonnement actif.");
    else if (!E.gere_par_stripe) msg("m-criteres", "Votre accès est géré par le support : écrivez-nous pour changer vos critères.");
    else if (cr.erreur) msg("m-criteres", cr.erreur);
    else if (cr.metier && !cr.applique) msg("m-criteres", "Modification enregistrée : appliquée dans les 10 minutes (confirmation par e-mail).", true);
    var r = vider("v-resume");
    function ligne(t, v) { r.appendChild(el("dt", t)); r.appendChild(el("dd", v)); }
    ligne("Métier", cr.metier ? (C.metiers_nom[cr.metier] || cr.metier) : "Non choisi");
    ligne(max === 1 ? "Département" : "Zones", (cr.departements || []).length ? cr.departements.join(", ") : (cr.metier && max !== 1 ? "Toute la France" : "Non choisi"));
    if (d.mots_cles) ligne("Mots-clés", (cr.mots_cles || []).length ? cr.mots_cles.join(", ") : "Aucun");
    ligne("État", !actif ? "Veille en pause (pas d'abonnement actif)" : cr.metier && !cr.applique ? "Modification en cours d'application" : "Active");
    $("v-limite").textContent = !actif ? "" : max === 1 ? "Votre offre : 1 métier et 1 département. Plusieurs départements, toute la France et les mots-clés : offre Pro."
      : "Votre offre : départements illimités ou toute la France" + (d.mots_cles ? ", mots-clés inclus." : ".");
  }

  // ---- marchés (alertes reçues)
  function puceEcheance(a) {
    if (!a.date_limite) return null;
    var j = ecart(a.date_limite), t, c;
    if (j < 0) { t = "Clos"; c = "ech-clos"; }
    else if (j === 0) { t = "Aujourd'hui"; c = "ech-urg"; }
    else if (j <= 3) { t = "J-" + j; c = "ech-urg"; }
    else if (j <= 7) { t = "J-" + j; c = "ech-proche"; }
    else { t = dateFr(a.date_limite).slice(0, 5); c = ""; }
    var s = el("span", t, "echeance " + c); s.title = "Date limite : " + dateFr(a.date_limite); return s;
  }
  var DERNIER = null;   // date du dernier envoi reçu
  function estNouveau(a) { return DERNIER && a.recu === DERNIER && ecart(DERNIER) >= -3; }
  function ligneMarche(a, court) {
    var li = el("li", null, "marche" + (ouvert(a) ? "" : " clos"));
    var etoile = el("button", null, "etoile"); etoile.type = "button"; etoile.appendChild(svg("favoris"));
    etoile.setAttribute("aria-pressed", a.favori); etoile.setAttribute("aria-label", a.favori ? "Retirer des favoris" : "Ajouter aux favoris");
    etoile.addEventListener("click", function () { basculer(a, etoile); });
    li.appendChild(etoile);
    var corps = el("div"), url = lienSur(a.url), titre = el(url ? "a" : "span", null, "marche-titre");
    if (estNouveau(a)) titre.appendChild(el("span", "Nouveau", "nouveau"));
    titre.appendChild(document.createTextNode(a.objet));
    if (url) { titre.href = url; titre.target = "_blank"; titre.rel = "noopener"; titre.appendChild(svg("lien")); titre.setAttribute("aria-label", a.objet + " (avis officiel, nouvel onglet)"); }
    corps.appendChild(titre);
    corps.appendChild(el("span", [a.acheteur, a.lieu, C.metiers_nom[a.metier] || a.metier].filter(Boolean).join(" · "), "marche-meta"));
    if (a.resume && !court) corps.appendChild(el("p", a.resume, "marche-resume"));
    var pied = el("div", null, "marche-pied");
    pied.appendChild(el("span", "Reçu le " + dateFr(a.recu)));
    if (a.date_limite) pied.appendChild(el("span", "Date limite " + dateFr(a.date_limite)));
    pied.appendChild(el("span", "Réf. " + a.avis_id));
    if (a.favori && a.rappel_envoye) pied.appendChild(el("span", "Rappel envoyé le " + dateFr(a.rappel_envoye)));
    if (!court && ouvert(a) && !$("d-cle").hidden) {
      [["Rapport « comment gagner »", "r"], ["Dossier de candidature", "dd"]].forEach(function (x) {
        var b = el("button", x[0]); b.type = "button";
        b.addEventListener("click", function () { choisir(x[1], a.avis_id); });
        pied.appendChild(b);
      });
    }
    if (!court) corps.appendChild(pied);
    li.appendChild(corps);
    var p = puceEcheance(a); if (p) li.appendChild(p);
    return li;
  }
  function basculer(a, bouton) {
    bouton.disabled = true;
    sb.rpc("basculer_favori", { p_alerte: a.id }).then(function (r) {
      bouton.disabled = false;
      if (r.error) return toast(erreurServeur(r.error));
      a.favori = r.data;
      toast(a.favori ? "Ajouté aux favoris : rappel par e-mail 3 jours avant la date limite." : "Retiré des favoris.");
      afficherMarches(); afficherApercu(); if (pageCourante() === "alertes") afficherHistorique();
    });
  }
  function groupes(cible, liste, cle) {   // listes groupées (par date de réception ou par échéance)
    var box = vider(cible), courant = null, ul;
    liste.forEach(function (a) {
      var g = cle(a);
      if (g !== courant) {
        courant = g; var div = el("div", null, "groupe"); div.appendChild(el("h2", g));
        var c = el("div", null, "carte"); ul = el("ul", null, "marches"); c.appendChild(ul); div.appendChild(c); box.appendChild(div);
      }
      ul.appendChild(ligneMarche(a));
    });
  }
  function nomJour(d) { var j = ecart(d); return j === 0 ? "Aujourd'hui" : j === -1 ? "Hier" : dateLongue(d).replace(/^./, function (x) { return x.toUpperCase(); }); }
  function listes() {
    var j = aujourdhui(), quinze = moinsJours(14);
    DERNIER = ALERTES.length ? ALERTES[0].recu : null;
    return {
      nouveaux: ALERTES.filter(function (a) { return a.recu >= quinze; }),
      echeances: ALERTES.filter(function (a) { return a.date_limite && a.date_limite >= j; }).sort(function (x, y) { return x.date_limite < y.date_limite ? -1 : x.date_limite > y.date_limite ? 1 : 0; }),
      favoris: ALERTES.filter(function (a) { return a.favori; })
    };
  }
  function afficherMarches() {
    var L = listes(), nv = L.nouveaux.filter(function (a) { return M_FILTRE === "tous" || ouvert(a); });
    groupes("l-marches", nv, function (a) { return nomJour(a.recu); });
    $("l-marches-vide").hidden = nv.length > 0;
    groupes("l-echeances", L.echeances, function (a) { var e = ecart(a.date_limite); return e <= 7 ? "Sous 7 jours" : e <= 30 ? "Dans le mois" : "Plus tard"; });
    $("l-echeances-vide").hidden = L.echeances.length > 0;
    var fav = L.favoris.slice().sort(function (x, y) { return (ouvert(y) - ouvert(x)) || String(x.date_limite || "9").localeCompare(String(y.date_limite || "9")); });
    groupes("l-favoris", fav, function (a) { return ouvert(a) ? "Encore ouverts" : "Clos"; });
    $("l-favoris-vide").hidden = fav.length > 0;
    // compteurs de navigation
    var nouveaux = ALERTES.filter(estNouveau).length, urgents = L.echeances.filter(function (a) { return ecart(a.date_limite) <= 7; }).length,
        favOuverts = L.favoris.filter(ouvert).length;
    function cpt(id, n) { $(id).hidden = !n; $(id).textContent = n; }
    cpt("n-marches", nouveaux); cpt("n-echeances", urgents); cpt("n-favoris", favOuverts);
    $("t-marches").hidden = !nouveaux; $("t-echeances").hidden = !L.echeances.some(function (a) { return ecart(a.date_limite) <= 3; });
    return L;
  }
  Array.prototype.forEach.call(document.querySelectorAll("#p-marches .filtre"), function (b) {
    b.addEventListener("click", function () {
      M_FILTRE = b.getAttribute("data-f");
      Array.prototype.forEach.call(document.querySelectorAll("#p-marches .filtre"), function (x) { x.setAttribute("aria-pressed", x === b); });
      afficherMarches();
    });
  });

  // ---- historique complet, recherche
  function sansAccent(t) { return String(t || "").normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase(); }
  function afficherHistorique() {
    var q = sansAccent($("h-q").value.trim()), j = aujourdhui();
    var liste = ALERTES.filter(function (a) {
      if (H_FILTRE === "favoris" && !a.favori || H_FILTRE === "ouvertes" && !ouvert(a) || H_FILTRE === "closes" && !(a.date_limite && a.date_limite < j)) return false;
      return !q || sansAccent([a.objet, a.acheteur, a.lieu, a.avis_id, a.resume].join(" ")).indexOf(q) >= 0;
    });
    var ul = vider("l-alertes");
    liste.slice(0, H_MAX).forEach(function (a) { ul.appendChild(ligneMarche(a)); });
    $("h-plus").hidden = liste.length <= H_MAX;
    $("h-compte").textContent = ALERTES.length ? liste.length + " marché" + (liste.length > 1 ? "s" : "") + (q || H_FILTRE !== "toutes" ? " trouvé" + (liste.length > 1 ? "s" : "") : " reçu" + (liste.length > 1 ? "s" : "")) + (ALERTES.length >= 300 ? " (300 derniers)" : "") : "";
    var vide = !liste.length; $("l-vide").hidden = !vide;
    if (vide) {
      $("l-vide-titre").textContent = !ALERTES.length ? "Pas encore d'alerte enregistrée" : "Aucun résultat";
      $("l-vide-txt").textContent = !ALERTES.length ? "Les appels d'offres envoyés par e-mail s'afficheront ici, avec leur date limite."
        : H_FILTRE === "favoris" && !q ? "Aucun favori. Cliquez sur l'étoile d'une alerte pour la garder et recevoir un rappel." : "Essayez un autre mot ou un autre filtre.";
    }
  }
  var minuteRecherche;
  $("h-q").addEventListener("input", function () { clearTimeout(minuteRecherche); minuteRecherche = setTimeout(function () { H_MAX = 30; afficherHistorique(); }, 150); });
  $("h-plus").addEventListener("click", function () { H_MAX += 30; afficherHistorique(); });
  Array.prototype.forEach.call(document.querySelectorAll("#p-alertes .filtre"), function (b) {
    b.addEventListener("click", function () {
      H_FILTRE = b.getAttribute("data-h"); H_MAX = 30;
      Array.prototype.forEach.call(document.querySelectorAll("#p-alertes .filtre"), function (x) { x.setAttribute("aria-pressed", x === b); });
      afficherHistorique();
    });
  });

  // ---- vue d'ensemble
  function afficherApercu() {
    var d = E.droits || {}, actif = d.veille, cr = E.criteres || {}, L = listes(), j = aujourdhui();
    var h = +heureParis().hour; $("a-bonjour").textContent = h >= 18 || h < 5 ? "Bonsoir" : "Bonjour";
    var semaine = ALERTES.filter(function (a) { return a.recu >= moinsJours(7); }).length,
        sous7 = L.echeances.filter(function (a) { return ecart(a.date_limite) <= 7; }), sous3 = sous7.filter(function (a) { return ecart(a.date_limite) <= 3; }).length;
    var res = vider("a-resume");
    function gras(t) { res.appendChild(el("b", t)); }
    function txt(t) { res.appendChild(document.createTextNode(t)); }
    if (!actif) txt(E.statut === "inactif" ? "Votre abonnement est terminé : vos alertes sont en pause. Votre historique reste consultable." : "Aucun abonnement actif : choisissez une offre pour recevoir les marchés de votre métier.");
    else if (!ALERTES.length) txt("Votre veille est active. Vos premiers marchés arriveront avec la prochaine alerte.");
    else {
      gras(String(semaine)); txt(semaine > 1 ? " marchés reçus ces 7 derniers jours" : " marché reçu ces 7 derniers jours");
      txt(" et "); gras(String(sous7.length)); txt(sous7.length > 1 ? " dates limites dans la semaine." : " date limite dans la semaine.");
    }
    var puces = vider("a-veille");
    function puce(ic, t) { var s = el("span", null, "puce"); s.appendChild(svg(ic)); s.appendChild(document.createTextNode(t)); puces.appendChild(s); }
    if (cr.metier) {
      puce("veille", C.metiers_nom[cr.metier] || cr.metier);
      puce("carte", (cr.departements || []).length ? (cr.departements.length > 4 ? cr.departements.length + " départements" : "Dép. " + cr.departements.join(", ")) : "Toute la France");
      if (d.mots_cles && (cr.mots_cles || []).length) puce("marches", cr.mots_cles.length + " mot" + (cr.mots_cles.length > 1 ? "s" : "") + "-clé" + (cr.mots_cles.length > 1 ? "s" : ""));
    }
    var st = $("a-statut"); st.textContent = E.offre_nom ? E.offre_nom + " · " + (STATUTS[E.statut] || E.statut) : "Sans abonnement"; st.className = "pastille pastille-grande st-" + E.statut;
    $("a-prochain").textContent = !actif ? "" : E.statut === "trialing" && E.fin_essai ? "Essai gratuit jusqu'au " + dateFr(E.fin_essai) + ", aucun débit avant." :
      E.statut === "past_due" ? "Paiement à régulariser." : cr.metier ? "Prochain envoi prévu " + prochainEnvoi() + "." : "";
    // indicateurs : seulement s'il y a des données réelles
    $("a-kpis").hidden = !ALERTES.length;
    if (ALERTES.length) {
      compter("k-30", RECUS30 != null ? RECUS30 : ALERTES.filter(function (a) { return a.recu >= moinsJours(30); }).length);
      $("k-30-pied").textContent = "dernier envoi le " + dateFr(ALERTES[0].recu);
      compter("k-ouverts", ALERTES.filter(ouvert).length);
      compter("k-7", sous7.length); $("k-7-pied").textContent = sous3 ? "dont " + sous3 + " sous 3 jours" : "aucune sous 3 jours";
      compter("k-fav", L.favoris.filter(ouvert).length);
    }
    $("a-vide").hidden = !(actif && !ALERTES.length);
    $("a-sans").hidden = actif;
    $("a-listes").hidden = !ALERTES.length;
    var u = vider("a-nouveaux"); ALERTES.slice(0, 5).forEach(function (a) { u.appendChild(ligneMarche(a, true)); });
    var e = vider("a-echeances"); L.echeances.slice(0, 5).forEach(function (a) { e.appendChild(ligneMarche(a, true)); });
    $("a-echeances-vide").hidden = L.echeances.slice(0, 5).some(function (a) { return ecart(a.date_limite) <= 14; });
    $("a-echeances-vide").textContent = L.echeances.length ? "Aucune date limite dans les 14 prochains jours." : "Aucune date limite à venir.";
    $("r-demandes").hidden = $("nav-demandes").hidden; $("r-parrain").hidden = !E.parrainage;
  }

  // ---- rapports et dossiers : inclus (Clé en main) ou achat d'un dossier à l'unité
  // Choix du marché dans les formulaires : la référence est remplie toute seule, la saisie reste possible (« Autre marché »).
  function remplirChoix(p) {
    var sel = $(p + "-choix"), garde = sel.value, ouverts = ALERTES.filter(function (a) { return a.date_limite && ouvert(a); });
    sel.textContent = "";
    var o0 = el("option", ouverts.length ? "Choisissez un appel d'offres…" : "Aucun marché ouvert dans vos alertes"); o0.value = "-"; sel.appendChild(o0);
    ouverts.sort(function (x, y) { return x.recu < y.recu ? 1 : x.recu > y.recu ? -1 : 0; }).forEach(function (a) {
      var t = a.objet.length > 70 ? a.objet.slice(0, 68) + "…" : a.objet, o = el("option", t + " · avant le " + dateFr(a.date_limite));
      o.value = a.avis_id; sel.appendChild(o);
    });
    var autre = el("option", "Autre marché : saisir la référence"); autre.value = ""; sel.appendChild(autre);
    sel.value = garde && sel.querySelector('option[value="' + garde.replace(/"/g, "") + '"]') ? garde : "-";
    $(p + "-ref-p").hidden = sel.value !== "";
  }
  function refChoisie(p) { var v = $(p + "-choix").value; return v === "" ? $(p + "-ref").value.trim() : v === "-" ? "" : v; }
  function choisir(p, ref) {
    var sel = $(p + "-choix");
    if (sel.querySelector('option[value="' + ref.replace(/"/g, "") + '"]')) sel.value = ref; else { sel.value = ""; $(p + "-ref").value = ref; }
    $(p + "-ref-p").hidden = sel.value !== "";
    location.hash = "#demandes";
    setTimeout(function () { var f = $(p === "r" ? "f-rapport" : "f-dossier"); f.scrollIntoView({ behavior: "smooth", block: "center" }); f.classList.add("eclair"); setTimeout(function () { f.classList.remove("eclair"); }, 1600); (p === "r" ? f.querySelector("button[type=submit]") : $("dd-infos")).focus({ preventScroll: true }); }, 80);
  }
  ["r", "dd"].forEach(function (p) {
    $(p + "-choix").addEventListener("change", function () { $(p + "-ref-p").hidden = this.value !== ""; if (!this.value) $(p + "-ref").focus(); });
  });

  // Rapport (page HTML) : affiché comme une page lisible dans un nouvel onglet. Le stockage le sert en texte brut
  // (sécurité), d'où l'affichage depuis une copie locale, scripts interdits.
  function ouvrirPage(chemin, fini) {
    var w = window.open("", "_blank");
    if (w) { try { w.document.title = "Rapport…"; w.document.body.textContent = "Chargement du rapport…"; } catch (e) {} }
    sb.storage.from("documents").download(chemin).then(function (r) {
      fini();
      if (r.error || !r.data) { if (w) w.close(); return toast("Fichier indisponible (conservé 6 mois). Écrivez-nous si besoin."); }
      return r.data.text().then(function (t) {
        var entete = '<meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="script-src \'none\'">' +
          '<meta name="viewport" content="width=device-width,initial-scale=1">';
        var html = /<head[^>]*>/i.test(t) ? t.replace(/<head[^>]*>/i, function (h) { return h + entete; }) : entete + t;
        var url = URL.createObjectURL(new Blob([html], { type: "text/html;charset=utf-8" }));
        if (w) w.location.href = url; else location.href = url;
      });
    }, function () { fini(); if (w) w.close(); toast("Service momentanément indisponible. Réessayez dans un instant."); });
  }

  function afficherDemandes() {
    var d = E.droits || {}, actif = d.veille, q = E.quotas || {}, cle = (d.rapports_mois || 0) + (d.dossiers_mois || 0) > 0 || E.offre === "cle";
    var visible = cle || (actif && !!C.dossier) || DEMANDES.length > 0;
    $("nav-demandes").hidden = !visible;
    $("d-cle").hidden = !cle; $("d-parcours").hidden = !cle;
    if (cle) { remplirChoix("r"); remplirChoix("dd"); }
    var qb = vider("d-quotas"); qb.hidden = !cle;
    function quota(t, u, m, note) {
      var c = el("div", null, "quota"); c.appendChild(el("span", t)); var b = el("div"); b.appendChild(el("b", u + " / " + m)); c.appendChild(b);
      var j = el("div", null, "jauge"), i = el("i"); i.style.width = (m ? Math.min(100, 100 * u / m) : 0) + "%"; j.appendChild(i); c.appendChild(j);
      c.appendChild(el("span", note)); qb.appendChild(c);
    }
    if (cle) {
      quota("Rapports ce mois-ci", q.rapports_utilises || 0, q.rapports_max || 0, "Une demande qui échoue n'est pas décomptée.");
      quota("Dossiers inclus ce mois-ci", q.dossiers_utilises || 0, q.dossiers_max || 0, E.statut === "trialing" ? "Dossiers inclus à partir du 1er paiement." : "Remis à zéro le 1er du mois.");
    }
    $("d-achat").hidden = true;
    if (!cle && actif && C.dossier) {
      var prix = E.offre === "pro" ? C.dossier.prix_pro : C.dossier.prix, lien = E.offre === "pro" ? C.dossier.lien_pro : C.dossier.lien;
      $("d-achat").hidden = !lien;
      $("d-achat-txt").textContent = "Un dossier de candidature rédigé pour un marché, à relire et signer : " + prix + " € (remboursé automatiquement si la date limite est à moins de " + C.dossier.delai + " jours).";
      if (lien) $("d-achat-lien").href = lienPaiement(lien, USER.email);
    }
    if (E.offre === "cle" && C.dossier && C.dossier.lien_cle) {
      $("d-achat").hidden = false;
      $("d-achat-txt").textContent = "Besoin d'un dossier de plus ce mois-ci : " + C.dossier.prix_cle + " €.";
      $("d-achat-lien").href = lienPaiement(C.dossier.lien_cle, USER.email);
    }
    $("r-demandes-txt").textContent = cle ? (q.rapports_utilises || 0) + " rapport(s) utilisé(s) sur " + (q.rapports_max || 0) : "Dossier prêt à signer";
    var ul = vider("l-demandes");
    $("l-demandes-vide").hidden = DEMANDES.length > 0;
    DEMANDES.forEach(function (x) {
      var li = el("li", null, "demande"), haut = el("div");
      haut.appendChild(el("span", ETATS[x.statut] || x.statut, "badge badge-" + x.statut));
      var av = ALERTES.filter(function (a) { return a.avis_id === x.avis_ref; })[0];
      haut.appendChild(el("b", (x.type === "rapport" ? "Rapport" : "Dossier") + " · " + (av ? av.objet.slice(0, 80) + " (réf. " + x.avis_ref + ")" : "avis " + x.avis_ref)));
      li.appendChild(haut);
      var act = el("div", null, "demande-act");
      if (x.statut === "terminee" && x.fichier) {
        var page = /\.html?$/i.test(x.fichier);
        var b = el("button", page ? "Ouvrir le rapport" : "Télécharger", "btn btn-sm"); b.type = "button";
        b.addEventListener("click", function () {
          b.disabled = true;
          if (page) return ouvrirPage(x.fichier, function () { b.disabled = false; });
          sb.storage.from("documents").createSignedUrl(x.fichier, 120, { download: true }).then(function (r) {
            b.disabled = false;
            if (r.error) return toast("Fichier indisponible (conservé 6 mois). Écrivez-nous si besoin.");
            location.href = r.data.signedUrl;
          });
        });
        act.appendChild(b);
      }
      if (x.statut === "action_requise") {
        var k = el("button", "Annuler", "btn btn-sm btn-clair"); k.type = "button";
        k.addEventListener("click", function () { k.disabled = true; sb.rpc("annuler_demande", { p_id: x.id }).then(function (r) { if (r.error) { k.disabled = false; return toast(erreurServeur(r.error)); } toast("Demande annulée."); ouvrirEspace(); }); });
        act.appendChild(k);
      }
      if (act.children.length) li.appendChild(act);
      li.appendChild(el("small", "Demandée le " + dateFr(x.cree) + (x.termine ? " · terminée le " + dateFr(x.termine) : "") + " · n° " + x.id.slice(0, 8)));
      if (x.message) li.appendChild(el("p", x.message));
      ul.appendChild(li);
    });
  }

  // ---- parrainage
  function afficherParrainage() {
    var p = E.parrainage;
    $("p-parrain").hidden = !p; $("p-parrain-sans").hidden = !!p;
    if (!p) return;
    $("p-parrain-lien").value = p;
    $("p-partager").href = "mailto:?subject=" + encodeURIComponent("Les appels d'offres de notre métier, chaque matin") +
      "&body=" + encodeURIComponent("Bonjour,\n\nJ'utilise " + C.nom + " pour recevoir chaque matin les appels d'offres publics de mon métier. Avec ce lien, ton premier mois est offert :\n" + p + "\n");
  }
  $("p-copier").addEventListener("click", function () {
    var i = $("p-parrain-lien"), fait = function () { toast("Lien copié."); };
    if (navigator.clipboard) navigator.clipboard.writeText(i.value).then(fait, function () { i.select(); });
    else { i.select(); try { document.execCommand("copy"); fait(); } catch (e) {} }
  });
  $("a-assistant").addEventListener("click", function () {
    document.body.classList.add("ra-on"); var b = document.querySelector(".ra-btn"); if (b && b.getAttribute("aria-expanded") !== "true") b.click();
  });

  // ---- formulaires
  $("f-criteres").addEventListener("submit", function (ev) {
    ev.preventDefault();
    var f = this, decoupe = function (v) { return v.split(/[,;\s]+/).map(function (x) { return x.trim(); }).filter(Boolean); };
    var mots = $("k-mots").value.split(",").map(function (x) { return x.trim(); }).filter(Boolean);
    attente(f, true);
    sb.rpc("modifier_criteres", { p_metier: $("k-metier").value, p_departements: decoupe($("k-deps").value), p_mots_cles: $("k-mots-p").hidden ? [] : mots })
      .then(function (r) {
        attente(f, false);
        if (r.error) return msg("m-criteres", erreurServeur(r.error));
        E = r.data; afficherCriteres(); afficherApercu(); afficherAbonnement();
        msg("m-criteres", "Enregistré. Appliqué à vos alertes dans les 10 minutes ; vous recevrez une confirmation par e-mail.", true);
      });
  });

  $("f-rapport").addEventListener("submit", function (ev) {
    ev.preventDefault();
    var f = this, ref = refChoisie("r");
    if (!/\d{2}-\d{3,7}/.test(ref)) return msg("m-rapport", "Choisissez l'appel d'offres dans la liste (ou saisissez sa référence, par ex. 26-123456).");
    f.dataset.cle = f.dataset.cle || cleUnique();   // même clé si double clic : une seule demande
    attente(f, true);
    sb.rpc("demander", { p_type: "rapport", p_avis_ref: ref, p_infos: "", p_cle: f.dataset.cle }).then(function (r) {
      attente(f, false);
      if (r.error) return msg("m-rapport", erreurServeur(r.error));
      delete f.dataset.cle; f.reset();
      toast("Demande de rapport reçue : vous serez prévenu par e-mail.");
      ouvrirEspace();
    });
  });

  $("f-dossier").addEventListener("submit", function (ev) {
    ev.preventDefault();
    var f = this, ref = refChoisie("dd"), fichiers = Array.prototype.slice.call($("dd-fichiers").files || []);
    if (!/\d{2}-\d{3,7}/.test(ref)) return msg("m-dossier", "Choisissez l'appel d'offres dans la liste (ou saisissez sa référence, par ex. 26-123456).");
    if (!fichiers.length) return msg("m-dossier", "Ajoutez le dossier de consultation (règlement, cahier des charges…).");
    var mauvais = fichiers.filter(function (x) { return x.size > C.max_octets || !/\.(pdf|docx?|odt|zip|txt)$/i.test(x.name); });
    if (mauvais.length) return msg("m-dossier", "Fichier refusé : " + mauvais[0].name + " (PDF, Word, ODT, ZIP ou TXT, 20 Mo maximum).");
    if (fichiers.length > 15) return msg("m-dossier", "15 fichiers au maximum : regroupez-les dans un ZIP.");
    f.dataset.cle = f.dataset.cle || cleUnique();
    attente(f, true); msg("m-dossier", "Envoi en cours…", true);
    sb.rpc("demander", { p_type: "dossier", p_avis_ref: ref, p_infos: $("dd-infos").value, p_cle: f.dataset.cle }).then(function (r) {
      if (r.error) { attente(f, false); return msg("m-dossier", erreurServeur(r.error)); }
      var dem = r.data, base = E_uid + "/" + dem.id + "/dce/";
      return Promise.all(fichiers.map(function (x) {
        var nom = x.name.normalize("NFD").replace(/[̀-ͯ]/g, "").replace(/[^A-Za-z0-9._-]+/g, "-").slice(-80);
        return sb.storage.from("documents").upload(base + nom, x, { upsert: true, contentType: x.type || "application/octet-stream" });
      })).then(function (res) {
        var ko = res.filter(function (x) { return x.error; });
        if (ko.length) throw new Error("Un fichier n'a pas pu être envoyé. Réessayez (votre demande est gardée, non décomptée si vous l'annulez).");
        return sb.rpc("soumettre_dossier", { p_id: dem.id });
      }).then(function (r2) {
        attente(f, false);
        if (r2.error) throw r2.error;
        delete f.dataset.cle; f.reset(); msg("m-dossier", "");
        toast("Dossier reçu : préparation dans les prochaines heures (jours ouvrés).");
        ouvrirEspace();
      });
    }).catch(function (e) { attente(f, false); msg("m-dossier", erreurServeur(e)); });
  });

  $("f-email").addEventListener("submit", function (ev) {
    ev.preventDefault();
    var f = this, email = $("p-email").value.trim();
    if (!/^[^@\s]+@[^@\s]+\.[a-z]{2,}$/i.test(email)) return msg("m-email", "Adresse e-mail invalide.");
    attente(f, true);
    sb.auth.updateUser({ email: email }, { emailRedirectTo: C.site + "/compte/confirmation/" }).then(function (r) {
      attente(f, false);
      if (r.error) return msg("m-email", r.error.status === 429 ? "Trop de demandes, réessayez plus tard." : "Changement impossible avec cette adresse.");
      msg("m-email", "Confirmez le changement depuis les liens envoyés par e-mail. Votre adresse actuelle reste valable d'ici là.", true);
    });
  });
  $("f-mdp").addEventListener("submit", function (ev) {
    ev.preventDefault();
    var f = this, mdp = $("p-mdp").value;
    if (mdp.length < 10) return msg("m-mdp", "Au moins 10 caractères.");
    attente(f, true);
    sb.auth.updateUser({ password: mdp }).then(function (r) {
      attente(f, false); f.reset();
      if (r.error && r.error.code === "reauthentication_needed") return msg("m-mdp", "Par sécurité, déconnectez-vous puis utilisez « Mot de passe oublié ».");
      msg("m-mdp", r.error ? "Mot de passe refusé : allongez-le ou mélangez lettres et chiffres." : "Mot de passe changé.", !r.error);
    });
  });
  function sortir() {
    sb.auth.signOut().then(function () { try { localStorage.removeItem("radar-compte"); } catch (e) {} location.href = C.root + "compte/?connexion"; });
  }
  $("e-sortir").addEventListener("click", sortir);
  $("p-sortir").addEventListener("click", sortir);

  // ------------------------------------------------------------ démarrage
  var E_uid = null;
  var v = racine.getAttribute("data-vue");
  if (v === "confirmation") return confirmation();
  if (v === "inscription" || v === "oubli") {
    vue("v-" + v);
    var em = (location.hash.match(/email=([^&]+)/) || [])[1];
    if (em) { $(v === "inscription" ? "i-email" : "o-email").value = decodeURIComponent(em); history.replaceState(null, "", location.pathname + location.search); }
    var s = souhaits();
    if (v === "inscription" && s.souhait_metier) {
      $("i-choix").hidden = false;
      $("i-choix").textContent = "Votre veille : " + (C.metiers_nom[s.souhait_metier] || s.souhait_metier) + (s.souhait_dep ? ", département " + s.souhait_dep : "") + ".";
    }
    return;
  }
  sb.auth.getSession().then(function (r) {
    var session = r.data && r.data.session;
    if (session) { E_uid = session.user.id; return ouvrirEspace(); }
    if (!/connexion/.test(location.search) && typeof ancienEspace === "function" && ancienEspace(true)) {
      vue(null); $("ancien").hidden = false; return;
    }
    vue("v-connexion");
  });
  sb.auth.onAuthStateChange(function (evt, session) { if (session) E_uid = session.user.id; });
})();
