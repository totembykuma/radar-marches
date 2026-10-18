// Usage : construire le site avec un config/comptes.json de test {"url":"https://abcdefgh.supabase.co","cle":"sb_publishable_TEST"},
// le servir (python3 -m http.server 8765 dans out/site), puis : npm i playwright-core && node tests/navigateur/espace.mjs 8765
// Option : CHROME=/chemin/vers/chrome. Supabase est entièrement simulé (aucun réseau).
// Test navigateur de l'espace client, Supabase simulé (aucun réseau). Usage : node espace.mjs <port>
import { chromium } from "playwright-core";
const PORT = process.argv[2] || "8765", BASE = `http://127.0.0.1:${PORT}`;
const b64 = (o) => Buffer.from(JSON.stringify(o)).toString("base64url");
const exp = Math.floor(Date.now() / 1000) + 3600;
const jwt = `${b64({ alg: "HS256", typ: "JWT" })}.${b64({ sub: "u1", exp, role: "authenticated", email: "cle@exemple.fr" })}.sig`;
const user = { id: "u1", aud: "authenticated", role: "authenticated", email: "cle@exemple.fr", user_metadata: {}, app_metadata: {}, created_at: "2026-10-17T00:00:00Z" };
const session = { access_token: jwt, token_type: "bearer", expires_in: 3600, expires_at: exp, refresh_token: "r1", user };
const espace = { email: "cle@exemple.fr", offre: "cle", offre_nom: "Clé en main", statut: "active", fin_essai: null, parrainage: "https://www.radar-marches.fr/parrainage/?ami=ABCDEF",
  gere_par_stripe: true, droits: { actif: true, veille: true, departements_max: 101, mots_cles: true, historique_complet: true, rapports_mois: 20, dossiers_mois: 2 },
  criteres: { metier: "nettoyage", departements: ["33"], mots_cles: ["vitrerie"], applique: true, erreur: null },
  quotas: { rapports_utilises: 3, rapports_max: 20, dossiers_utilises: 0, dossiers_max: 2 } };
const alertes = [{ id: 1, avis_id: "26-1", recu: "2026-10-16", objet: "Nettoyage <b>mairie</b>", acheteur: "Ville", lieu: "Bordeaux", date_limite: "2099-01-01", metier: "nettoyage", url: "javascript:alert(1)", resume: "Ménage", favori: false },
  { id: 2, avis_id: "26-2", recu: "2026-10-15", objet: "Vitrerie école", acheteur: "Région", lieu: "33", date_limite: "2020-01-01", metier: "nettoyage", url: "https://www.boamp.fr/x", resume: null, favori: false }];
const demandes = [{ id: "11111111-aaaa", type: "rapport", avis_ref: "26-123456", statut: "terminee", message: null, cree: "2026-10-16T08:00:00Z", termine: "2026-10-16T09:00:00Z", fichier: "u1/d/resultat/r.html" }];
const log = [], erreurs = [];
let ok = 0, ko = 0;
const verifier = (cond, txt) => { if (cond) ok++; else { ko++; console.log("ÉCHEC :", txt); } };

const browser = await chromium.launch({ executablePath: process.env.CHROME || "/opt/pw-browsers/chromium-1194/chrome-linux/chrome" });
const page = await browser.newPage({ viewport: { width: 390, height: 844 } });
page.on("console", (m) => { if (m.type() === "error") erreurs.push(m.text()); });
page.on("pageerror", (e) => erreurs.push(String(e)));
let favori = false, confirme = false;
await page.route("https://abcdefgh.supabase.co/**", async (route) => {
  const req = route.request(), u = new URL(req.url()), p = u.pathname;
  log.push(`${req.method()} ${p}`);
  const json = (s, body) => route.fulfill({ status: s, contentType: "application/json", body: JSON.stringify(body), headers: { "access-control-allow-origin": "*" } });
  if (req.method() === "OPTIONS") return route.fulfill({ status: 200, headers: { "access-control-allow-origin": "*", "access-control-allow-headers": "*", "access-control-allow-methods": "*" } });
  if (p === "/auth/v1/token") {
    const body = JSON.parse(req.postData() || "{}");
    if (body.email === "nouveau@exemple.fr") return confirme ? json(200, session) : json(400, { code: 400, error_code: "email_not_confirmed", msg: "Email not confirmed" });
    return body.password === "bonmotdepasse1" ? json(200, session) : json(400, { code: 400, error_code: "invalid_credentials", msg: "Invalid login credentials" });
  }
  if (p === "/auth/v1/signup") return json(200, { ...user, id: "u2", email: "nouveau@exemple.fr", confirmed_at: null });
  if (p === "/auth/v1/user" && req.method() === "GET") return json(200, user);
  if (p === "/auth/v1/user" && req.method() === "PUT") return json(200, user);
  if (p === "/auth/v1/verify") return json(200, session);
  if (p === "/auth/v1/logout") return route.fulfill({ status: 204, headers: { "access-control-allow-origin": "*" } });
  if (p === "/rest/v1/rpc/mon_espace") return json(200, espace);
  if (p === "/rest/v1/alertes") return json(200, alertes);
  if (p === "/rest/v1/demandes") return json(200, demandes);
  if (p === "/rest/v1/rpc/basculer_favori") { favori = !favori; return json(200, favori); }
  if (p === "/rest/v1/rpc/modifier_criteres") return json(403, { code: "42501", message: "Votre offre permet 1 département(s).", details: null, hint: null });
  if (p === "/rest/v1/rpc/demander") return json(200, { id: "22222222-bbbb", statut: "recue" });
  return json(404, {});
});

// 1. connexion
await page.goto(`${BASE}/compte/?connexion`);
await page.waitForSelector("#v-connexion:not([hidden])");
await page.fill("#c-email", "cle@exemple.fr"); await page.fill("#c-mdp", "mauvais");
await page.click("#f-connexion button[type=submit]");
await page.waitForSelector("#m-connexion:not([hidden])");
verifier((await page.textContent("#m-connexion")).includes("incorrect"), "message mauvais mot de passe");
await page.fill("#c-mdp", "bonmotdepasse1");
await page.click("#f-connexion button[type=submit]");
await page.waitForSelector("#v-espace:not([hidden])", { timeout: 5000 });
verifier((await page.textContent("#e-offre")).includes("Clé en main"), "offre affichée");
verifier(await page.isHidden("#e-sans"), "pas de boutons de paiement pour un abonné actif");
verifier(await page.isVisible("#demandes"), "rubrique demandes visible (Clé en main)");
verifier((await page.textContent("#d-quotas")).includes("3 rapport(s) sur 20"), "quotas");
verifier((await page.$$("#l-alertes li")).length === 2, "2 alertes");
verifier((await page.innerHTML("#l-alertes")).includes("&lt;b&gt;mairie"), "texte non interprété comme HTML");
verifier(!(await page.innerHTML("#l-alertes")).includes("javascript:"), "lien javascript: écarté");
verifier(await page.isVisible("#k-mots-p"), "mots-clés visibles pour Clé en main");
verifier(await page.isVisible("#p-parrain"), "parrainage affiché");
// favori
await page.click("#l-alertes li:first-child .etoile");
await page.waitForSelector("#l-alertes li:first-child .etoile[aria-pressed=true]");
await page.click("#t-favoris");
verifier((await page.$$("#l-alertes li")).length === 1, "onglet favoris : 1");
// critères refusés par le serveur
await page.fill("#k-deps", "33, 47");
await page.click("#k-ok");
await page.waitForFunction(() => document.querySelector("#m-criteres").textContent.includes("1 département"));
ok++;
// rapport : double clic = une seule demande
await page.fill("#r-ref", "26-123456");
await page.click("#f-rapport button[type=submit]", { clickCount: 2 });
await page.waitForFunction(() => document.querySelector("#m-rapport").textContent.includes("Demande reçue"));
verifier(log.filter((l) => l.endsWith("/rpc/demander")).length === 1, "double clic : 1 seul appel");
await page.screenshot({ path: "/tmp/espace-mobile.png", fullPage: true });
// déconnexion
await page.click("#e-sortir");
await page.waitForSelector("#v-connexion:not([hidden])");
ok++;

// 2. inscription avec choix venus de l'outil de découverte
await page.goto(`${BASE}/compte/inscription/?metier=nettoyage&dep=33#email=nouveau%40exemple.fr`);
await page.waitForSelector("#v-inscription:not([hidden])");
verifier((await page.inputValue("#i-email")) === "nouveau@exemple.fr", "e-mail prérempli");
verifier(/département 33/.test(await page.textContent("#i-choix")), "choix affiché : " + await page.textContent("#i-choix"));
await page.fill("#i-mdp", "abcdefghij"); await page.fill("#i-mdp2", "abcdefghik");
await page.click("#f-inscription button[type=submit]");
verifier((await page.textContent("#m-inscription")).includes("pas identiques"), "mots de passe différents");
// inscription réussie, confirmation faite ailleurs (téléphone) : la page s'ouvre toute seule au retour sur l'onglet
await page.fill("#i-mdp2", "abcdefghij"); await page.check("#i-cgv");
// anti-robots : champ piège rempli = refus, sans appel à Supabase
await page.evaluate(() => { document.querySelector("#i-site").value = "http://spam"; });
await page.click("#f-inscription button[type=submit]");
verifier((await page.textContent("#m-inscription")).includes("patienter"), "champ piège rempli : inscription refusée");
await page.evaluate(() => { document.querySelector("#i-site").value = ""; });
verifier(await page.evaluate(() => document.querySelector("#i-site").getBoundingClientRect().right < 0), "champ piège hors écran");
await page.waitForTimeout(3100);
await page.click("#f-inscription button[type=submit]");
await page.waitForFunction(() => document.querySelector("#m-inscription").textContent.includes("toute seule"));
await page.evaluate(() => window.dispatchEvent(new Event("focus")));
await page.waitForTimeout(300);
verifier(await page.isVisible("#v-inscription"), "pas encore confirmé : reste sur l'inscription");
confirme = true;
await page.evaluate(() => window.dispatchEvent(new Event("focus")));
await page.waitForSelector("#v-espace:not([hidden])", { timeout: 5000 });
ok++;

// 3. lien de réinitialisation
await page.goto(`${BASE}/compte/confirmation/?token_hash=abc&type=recovery`);
await page.waitForSelector("#f-nouveau:not([hidden])");
verifier(!page.url().includes("token_hash"), "jeton retiré de l'adresse");
await page.fill("#n-mdp", "nouveaumotdepasse"); await page.fill("#n-mdp2", "nouveaumotdepasse");
await page.click("#f-nouveau button[type=submit]");
await page.waitForFunction(() => document.querySelector("#m-nouveau").textContent.includes("enregistré"));
ok++;

// 4. ancien lien personnel, sans session : ancien espace + invitation à créer un mot de passe
const ctx2 = await browser.newContext(); const p2 = await ctx2.newPage();
p2.on("pageerror", (e) => erreurs.push(String(e)));
const ancien = Buffer.from(JSON.stringify({ e: "ancien@exemple.fr", o: "Pro", s: "active", fe: "", db: "01/10/2026", m: [["nettoyage", "Nettoyage"]], d: ["75"], k: [], p: "", pro: true })).toString("base64url");
await p2.route("https://abcdefgh.supabase.co/**", (r) => r.fulfill({ status: 401, body: "{}", headers: { "access-control-allow-origin": "*" } }));
await p2.goto(`${BASE}/compte/#${ancien}`);
await p2.waitForSelector("#c-invite:not([hidden])");
verifier((await p2.getAttribute("#c-invite-lien", "href")).includes("ancien%40exemple.fr"), "invitation avec e-mail");
verifier((await p2.textContent("#c-qui")).includes("ancien@exemple.fr"), "ancien espace toujours fonctionnel");

await browser.close();
const vraies = erreurs.filter((e) => !/Failed to load resource/.test(e));
verifier(!vraies.length, "aucune erreur JS / CSP : " + vraies.join(" | "));
console.log(`Navigateur : ${ok} vérifications OK, ${ko} en échec`);
process.exit(ko ? 1 : 0);
