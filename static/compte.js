/* Espace client Radar Marchés : connexion par e-mail + mot de passe (Supabase Auth), espace commun aux 3 offres.
   Sécurité : le navigateur n'a que la clé PUBLIQUE ; chaque lecture est filtrée par la base (RLS : ses propres lignes)
   et chaque action passe par une fonction serveur qui revérifie offre, statut et quotas. Aucun texte reçu n'est
   inséré comme HTML (textContent uniquement). */
(function () {
  "use strict";
  var C = JSON.parse(document.getElementById("compte-conf").textContent);
  var racine = document.getElementById("compte");
  var sb = window.supabase.createClient(C.url, C.cle, { auth: { persistSession: true, autoRefreshToken: true, detectSessionInUrl: false, flowType: "pkce" } });
  var $ = function (id) { return document.getElementById(id); };
  var VUES = ["v-chargement", "v-connexion", "v-inscription", "v-oubli", "v-confirmation", "v-espace"];
  var STATUTS = { trialing: "essai gratuit en cours", active: "actif", past_due: "paiement en attente", inactif: "terminé", aucun: "aucun abonnement" };
  var ETATS = { action_requise: "Pièces à ajouter", recue: "Demande reçue", en_traitement: "En préparation", terminee: "Prêt", echec: "Non réalisé", annulee: "Annulée" };

  function vue(id) { VUES.forEach(function (v) { $(v).hidden = v !== id; }); window.scrollTo(0, 0); }
  function msg(id, texte, ok) { var m = $(id); m.textContent = texte; m.className = "msg " + (ok ? "msg-ok" : "msg-err"); m.hidden = !texte; }
  function attente(form, oui) { Array.prototype.forEach.call(form.querySelectorAll("button,input,select,textarea"), function (e) { e.disabled = oui; }); }
  function el(tag, texte, cls) { var e = document.createElement(tag); if (texte != null) e.textContent = texte; if (cls) e.className = cls; return e; }
  function dateFr(d) { if (!d) return ""; var p = String(d).slice(0, 10).split("-"); return p[2] + "/" + p[1] + "/" + p[0]; }
  function aujourdhui() { return new Date().toISOString().slice(0, 10); }
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

  // ------------------------------------------------------------ espace client
  var E = null, ALERTES = [], FILTRE = "toutes";

  function ouvrirEspace() {
    vue("v-chargement");
    Promise.all([
      sb.rpc("mon_espace"),
      sb.from("alertes").select("id,avis_id,recu,objet,acheteur,lieu,date_limite,metier,url,resume,favori").order("recu", { ascending: false }).limit(300),
      sb.from("demandes").select("id,type,avis_ref,statut,message,cree,termine,fichier").order("cree", { ascending: false }).limit(60),
      sb.auth.getUser()
    ]).then(function (r) {
      if (r[0].error || !r[3].data.user) { $("v-chargement").textContent = erreurServeur(r[0].error); return; }
      E = r[0].data; E_uid = r[3].data.user.id; E.email = E.email || r[3].data.user.email; ALERTES = r[1].data || [];
      afficherEspace(r[3].data.user); afficherAlertes(); afficherDemandes(r[2].data || []);
      vue("v-espace");
      if (location.hash) { var cible = $(location.hash.slice(1)); if (cible) cible.scrollIntoView(); }
    }, function (e) { $("v-chargement").textContent = erreurServeur(e); });
  }

  function afficherEspace(user) {
    var d = E.droits || {}, actif = d.veille, meta = user.user_metadata || {};
    $("e-qui").textContent = "Compte : " + user.email;
    $("e-offre").textContent = E.offre_nom ? "Offre " + E.offre_nom + " · " + (STATUTS[E.statut] || E.statut) +
      (E.statut === "trialing" && E.fin_essai ? " jusqu'au " + dateFr(E.fin_essai) + " (aucun débit avant cette date)" : "") : "Aucun abonnement actif.";
    $("e-impaye").hidden = E.statut !== "past_due";
    $("e-sans").hidden = actif; $("e-portail-p").hidden = !E.gere_par_stripe && !E.offre; $("e-portail-aide").hidden = $("e-portail-p").hidden;
    var p = $("e-portail"); p.href = lienPaiement(C.portail, user.email);
    if (!actif) {
      $("e-sans-txt").textContent = E.statut === "inactif" ? "Votre abonnement est terminé. Vous pouvez le reprendre à tout moment :"
        : "Aucun abonnement n'est relié à " + user.email + ". Démarrez votre essai gratuit de " + C.essai + " jours :";
      var box = $("e-offres"); box.textContent = "";
      Object.keys(C.offres).forEach(function (k) {
        var o = C.offres[k], div = el("div", null, "offre-mini" + (meta.souhait_offre === k ? " choisie" : ""));
        div.appendChild(el("b", o.nom)); div.appendChild(el("span", o.grille || (o.prix + " €/mois" + (o.annuel ? " ou " + o.annuel + " €/an" : ""))));
        var a = el("a", "Essayer " + C.essai + " jours", "btn btn-sm"); a.href = lienPaiement(o.lien, user.email); div.appendChild(a);
        if (o.lien_annuel) { var b = el("a", "Annuel", "btn btn-sm btn-ghost-dark"); b.href = lienPaiement(o.lien_annuel, user.email); div.appendChild(b); }
        box.appendChild(div);
      });
      if (meta.souhait_metier) box.appendChild(el("p", "Votre choix sur le site : " + (C.metiers_nom[meta.souhait_metier] || meta.souhait_metier) +
        (meta.souhait_dep ? ", département " + meta.souhait_dep : "") + ". Vous le saisirez sur la page de paiement.", "muted"));
    }
    // Critères (modifiables dans les limites de l'offre ; la base refuse tout dépassement)
    var sel = $("k-metier"); sel.textContent = "";
    C.metiers.forEach(function (m) { var o = el("option", m[1]); o.value = m[0]; sel.appendChild(o); });
    var cr = E.criteres || {}, max = d.departements_max || 0;
    if (cr.metier) sel.value = cr.metier;
    $("k-deps").value = (cr.departements || []).join(", ");
    $("k-mots").value = (cr.mots_cles || []).join(", ");
    $("k-mots-p").hidden = !d.mots_cles;
    $("k-deps-label").textContent = max === 1 ? "Département (1 inclus dans votre offre)" : "Départements (séparés par des virgules)";
    $("k-deps-aide").textContent = max === 1 ? "Ex. 33. Plusieurs départements ou toute la France : offre Pro." : "Laissez vide pour toute la France.";
    attente($("f-criteres"), !actif || !E.gere_par_stripe);
    if (!actif) msg("m-criteres", "Les critères se modifient pendant un abonnement actif.");
    else if (!E.gere_par_stripe) msg("m-criteres", "Votre accès est géré par le support : écrivez-nous pour changer vos critères.");
    else if (cr.erreur) msg("m-criteres", cr.erreur);
    else if (cr.metier && !cr.applique) msg("m-criteres", "Modification enregistrée : appliquée dans les 10 minutes (confirmation par e-mail).", true);
    // Demandes : rapports / dossiers inclus (Clé en main) ou achat d'un dossier à l'unité
    var q = E.quotas || {}, cle = (d.rapports_mois || 0) + (d.dossiers_mois || 0) > 0 || E.offre === "cle";
    $("demandes").hidden = !(cle || (actif && C.dossier)); $("nav-demandes").hidden = $("demandes").hidden;
    $("d-cle").hidden = !cle;
    $("d-quotas").textContent = cle ? "Ce mois-ci : " + q.rapports_utilises + " rapport(s) sur " + q.rapports_max + " · " +
      q.dossiers_utilises + " dossier(s) inclus sur " + q.dossiers_max + (E.statut === "trialing" ? " (dossiers inclus à partir du 1er paiement)" : "") +
      ". Une demande qui échoue n'est pas décomptée." : "";
    if (!cle && actif && C.dossier) {
      var prix = E.offre === "pro" ? C.dossier.prix_pro : C.dossier.prix, lien = E.offre === "pro" ? C.dossier.lien_pro : C.dossier.lien;
      $("d-achat").hidden = false;
      $("d-achat-txt").textContent = "Un dossier de candidature rédigé pour un marché, à relire et signer : " + prix + " € (remboursé automatiquement si la date limite est à moins de " + C.dossier.delai + " jours).";
      $("d-achat-lien").href = lienPaiement(lien, user.email);
    }
    if (E.offre === "cle" && C.dossier && C.dossier.lien_cle) {
      $("d-achat").hidden = false;
      $("d-achat-txt").textContent = "Besoin d'un dossier de plus ce mois-ci : " + C.dossier.prix_cle + " €.";
      $("d-achat-lien").href = lienPaiement(C.dossier.lien_cle, user.email);
    }
    $("p-email").value = user.new_email || "";
    if (E.parrainage) { $("p-parrain").hidden = false; $("p-parrain-lien").textContent = E.parrainage; $("p-parrain-lien").href = E.parrainage; }
  }

  function afficherAlertes() {
    var ul = $("l-alertes"), j = aujourdhui(), liste = ALERTES.filter(function (a) { return FILTRE === "toutes" || a.favori; });
    ul.textContent = "";
    $("t-toutes").setAttribute("aria-selected", FILTRE === "toutes"); $("t-favoris").setAttribute("aria-selected", FILTRE === "favoris");
    $("l-vide").hidden = liste.length > 0;
    $("l-vide").textContent = FILTRE === "favoris" ? "Aucun favori. Cliquez sur l'étoile d'une alerte pour la garder et recevoir un rappel." :
      "Pas encore d'alerte enregistrée. Les prochains appels d'offres envoyés par e-mail s'afficheront ici.";
    liste.forEach(function (a) {
      var li = el("li", null, a.date_limite && a.date_limite < j ? "expire" : "");
      var etoile = el("button", a.favori ? "★" : "☆", "etoile"); etoile.type = "button";
      etoile.setAttribute("aria-pressed", a.favori); etoile.setAttribute("aria-label", a.favori ? "Retirer des favoris" : "Ajouter aux favoris");
      etoile.addEventListener("click", function () {
        etoile.disabled = true;
        sb.rpc("basculer_favori", { p_alerte: a.id }).then(function (r) {
          etoile.disabled = false; if (r.error) return alert(erreurServeur(r.error));
          a.favori = r.data; afficherAlertes();
        });
      });
      li.appendChild(etoile);
      var corps = el("div"), url = lienSur(a.url), titre = url ? el("a", a.objet) : el("b", a.objet);
      if (url) { titre.href = url; titre.target = "_blank"; titre.rel = "noopener"; }
      corps.appendChild(titre);
      corps.appendChild(el("small", [a.acheteur, a.lieu, a.metier].filter(Boolean).join(" · ")));
      if (a.resume) corps.appendChild(el("p", a.resume));
      corps.appendChild(el("small", "Reçu le " + dateFr(a.recu) + (a.date_limite ? " · date limite " + dateFr(a.date_limite) + (a.date_limite < j ? " (clos)" : "") : "") +
        " · réf. " + a.avis_id));
      li.appendChild(corps); ul.appendChild(li);
    });
  }
  $("t-toutes").addEventListener("click", function () { FILTRE = "toutes"; afficherAlertes(); });
  $("t-favoris").addEventListener("click", function () { FILTRE = "favoris"; afficherAlertes(); });

  function afficherDemandes(liste) {
    var ul = $("l-demandes"); ul.textContent = "";
    liste.forEach(function (d) {
      var li = el("li"), etat = el("span", ETATS[d.statut] || d.statut, "badge badge-" + d.statut);
      li.appendChild(etat);
      li.appendChild(el("b", (d.type === "rapport" ? "Rapport" : "Dossier") + " · avis " + d.avis_ref));
      li.appendChild(el("small", "Demandé le " + dateFr(d.cree) + (d.termine ? " · terminé le " + dateFr(d.termine) : "") + " · n° " + d.id.slice(0, 8)));
      if (d.message) li.appendChild(el("p", d.message));
      if (d.statut === "terminee" && d.fichier) {
        var b = el("button", "Télécharger", "btn btn-sm"); b.type = "button";
        b.addEventListener("click", function () {
          sb.storage.from("documents").createSignedUrl(d.fichier, 120, { download: true }).then(function (r) {
            if (r.error) return alert("Fichier indisponible (conservé 6 mois). Écrivez-nous si besoin.");
            location.href = r.data.signedUrl;
          });
        });
        li.appendChild(b);
      }
      if (d.statut === "action_requise") {
        var x = el("button", "Annuler", "btn btn-sm btn-ghost-dark"); x.type = "button";
        x.addEventListener("click", function () { sb.rpc("annuler_demande", { p_id: d.id }).then(function () { ouvrirEspace(); }); });
        li.appendChild(x);
      }
      ul.appendChild(li);
    });
  }

  $("f-criteres").addEventListener("submit", function (ev) {
    ev.preventDefault();
    var f = this, decoupe = function (v) { return v.split(/[,;\s]+/).map(function (x) { return x.trim(); }).filter(Boolean); };
    var mots = $("k-mots").value.split(",").map(function (x) { return x.trim(); }).filter(Boolean);
    attente(f, true);
    sb.rpc("modifier_criteres", { p_metier: $("k-metier").value, p_departements: decoupe($("k-deps").value), p_mots_cles: $("k-mots-p").hidden ? [] : mots })
      .then(function (r) {
        attente(f, false);
        if (r.error) return msg("m-criteres", erreurServeur(r.error));
        E = r.data; msg("m-criteres", "Enregistré. Appliqué à vos alertes dans les 10 minutes ; vous recevrez une confirmation par e-mail.", true);
      });
  });

  $("f-rapport").addEventListener("submit", function (ev) {
    ev.preventDefault();
    var f = this, ref = $("r-ref").value.trim();
    if (!/\d{2}-\d{3,7}/.test(ref)) return msg("m-rapport", "Indiquez la référence de l'avis, par ex. 26-123456 (elle figure dans vos alertes).");
    f.dataset.cle = f.dataset.cle || cleUnique();   // même clé si double clic : une seule demande
    attente(f, true);
    sb.rpc("demander", { p_type: "rapport", p_avis_ref: ref, p_infos: "", p_cle: f.dataset.cle }).then(function (r) {
      attente(f, false);
      if (r.error) return msg("m-rapport", erreurServeur(r.error));
      delete f.dataset.cle; f.reset();
      msg("m-rapport", "Demande reçue. Vous serez prévenu par e-mail ; le rapport sera aussi téléchargeable ici.", true);
      ouvrirEspace();
    });
  });

  $("f-dossier").addEventListener("submit", function (ev) {
    ev.preventDefault();
    var f = this, ref = $("dd-ref").value.trim(), fichiers = Array.prototype.slice.call($("dd-fichiers").files || []);
    if (!/\d{2}-\d{3,7}/.test(ref)) return msg("m-dossier", "Indiquez la référence de l'avis, par ex. 26-123456.");
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
        delete f.dataset.cle; f.reset();
        msg("m-dossier", "Dossier reçu. Préparation dans les prochaines heures (jours ouvrés) ; vous serez prévenu par e-mail.", true);
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
  $("e-sortir").addEventListener("click", function () {
    sb.auth.signOut().then(function () { try { localStorage.removeItem("radar-compte"); } catch (e) {} location.href = C.root + "compte/?connexion"; });
  });

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
