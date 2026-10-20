// Rapports et dossiers : parcours, choix du marché sans taper la référence, bouton Dossier depuis un marché. Même usage que espace.mjs.
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
const alertes = [
 { id: 1, avis_id: "26-104512", recu: "2026-10-20", objet: "Nettoyage des locaux de la mairie et de l'école élémentaire", acheteur: "Commune de Pessac", lieu: "Pessac", date_limite: "2026-11-11", metier: "nettoyage", url: "https://www.boamp.fr/x", resume: "Ménage quotidien de 2 400 m² de locaux, marché de 3 ans.", favori: false },
 { id: 2, avis_id: "26-103877", recu: "2026-10-20", objet: "Entretien et vitrerie des bâtiments communautaires", acheteur: "Bordeaux Métropole", lieu: "Bordeaux", date_limite: "2026-11-04", metier: "nettoyage", url: "https://www.boamp.fr/y", resume: "Vitrerie semestrielle et entretien courant de 12 sites.", favori: false },
 { id: 3, avis_id: "26-102961", recu: "2026-10-20", objet: "Nettoyage des locaux du centre hospitalier", acheteur: "CH d'Arcachon", lieu: "Arcachon", date_limite: "2026-11-18", metier: "nettoyage", url: "https://www.boamp.fr/z", resume: "Bionettoyage des services administratifs.", favori: false }];
const demandes = [{ id: "11111111-aaaa", type: "rapport", avis_ref: "26-103877", statut: "terminee", message: null, cree: "2026-10-20T08:00:00Z", termine: "2026-10-20T09:00:00Z", fichier: "u1/d/resultat/r.html" }];
espace.quotas.rapports_utilises = 1;
const log = []; let ok = 0, ko = 0; const erreurs = [];
const verifier = (c, t) => { if (c) ok++; else { ko++; console.log("ÉCHEC :", t); } };
const browser = await chromium.launch({ executablePath: process.env.CHROME || "/opt/pw-browsers/chromium-1194/chrome-linux/chrome" });
for (const [nom, vp] of [["mobile", { width: 390, height: 844 }], ["ordinateur", { width: 1366, height: 900 }]]) {
const page = await browser.newPage({ viewport: vp });
page.on("pageerror", (e) => erreurs.push(String(e)));
await page.route("https://abcdefgh.supabase.co/**", async (route) => {
  const req = route.request(), p = new URL(req.url()).pathname; log.push(`${req.method()} ${p} ${req.postData() || ""}`);
  const json = (s, body) => route.fulfill({ status: s, contentType: "application/json", body: JSON.stringify(body), headers: { "access-control-allow-origin": "*" } });
  if (req.method() === "OPTIONS") return route.fulfill({ status: 200, headers: { "access-control-allow-origin": "*", "access-control-allow-headers": "*", "access-control-allow-methods": "*" } });
  if (p === "/auth/v1/token") return json(200, session);
  if (p === "/auth/v1/user") return json(200, user);
  if (p === "/rest/v1/rpc/mon_espace") return json(200, espace);
  if (p === "/rest/v1/alertes") return json(200, alertes);
  if (p === "/rest/v1/demandes") return json(200, demandes);
  if (p === "/rest/v1/rpc/demander") return json(200, { id: "22222222-bbbb", statut: "recue" });
  if (p === "/rest/v1/rpc/soumettre_dossier") return json(200, { id: "22222222-bbbb", statut: "recue" });
  if (p.startsWith("/storage/v1/object/documents/")) return json(200, { Key: p });
  return json(404, {});
});
await page.goto(`${BASE}/compte/?connexion`);
await page.waitForSelector("#v-connexion:not([hidden])");
await page.fill("#c-email", "cle@exemple.fr"); await page.fill("#c-mdp", "bonmotdepasse1");
await page.click("#f-connexion button[type=submit]");
await page.waitForSelector("#v-espace:not([hidden])");
await page.goto(`${BASE}/compte/#demandes`); await page.waitForSelector("#p-demandes:not([hidden])");
verifier(await page.isVisible("#d-parcours"), "parcours visible");
verifier((await page.$$("#r-choix option")).length === 5, "3 marchés + choisir + autre");
verifier(await page.isHidden("#r-ref-p"), "saisie cachée par défaut");
verifier((await page.textContent("#l-demandes")).includes("Entretien et vitrerie"), "demande nommée par son objet");
await page.screenshot({ path: `${process.env.S || "/tmp"}/demandes-${nom}.png`, fullPage: true });
// depuis un marché : bouton Dossier → formulaire prérempli
await page.goto(`${BASE}/compte/#marches`); await page.waitForSelector("#p-marches:not([hidden])");
await page.waitForTimeout(800);
await page.screenshot({ path: `${process.env.S || "/tmp"}/marches-${nom}.png` });
await page.click("#l-marches li:nth-child(1) .marche-pied button:has-text('Dossier')");
await page.waitForSelector("#p-demandes:not([hidden])"); await page.waitForTimeout(700);
verifier((await page.inputValue("#dd-choix")) === alertes[0].avis_id, "dossier prérempli : " + await page.inputValue("#dd-choix"));
await page.screenshot({ path: `${process.env.S || "/tmp"}/dossier-prerempli-${nom}.png` });
// assistant dossier : 4 étapes, champs obligatoires, pièces repérées, envoi
const S = process.env.S || "/tmp";
verifier(await page.isVisible("#dd-marche"), "marché choisi affiché");
await page.click("#dd-suite");
verifier(await page.isVisible("fieldset[data-pas='2']"), "étape 2 entreprise");
await page.click("#dd-suite");
verifier(await page.isVisible("#m-dossier") && await page.isVisible("fieldset[data-pas='2']"), "champs obligatoires bloquent");
await page.fill("#dd-nom", "Net Pro SARL"); await page.fill("#dd-siret", "123 456 789 00012"); await page.fill("#dd-dirigeant", "M. Martin, gérant");
await page.fill("#dd-effectif", "12 salariés"); await page.fill("#dd-activite", "Nettoyage de locaux publics en Gironde.");
await page.fill("#dd-refs", "Mairie de Talence, nettoyage école, 2025, 40 000 €/an");
await page.screenshot({ path: `${S}/dossier-etape2-${nom}.png`, fullPage: true });
await page.click("#dd-suite");
verifier(await page.isVisible("fieldset[data-pas='3']"), "étape 3 documents");
verifier(!(await page.isVisible("#dd-fichiers")), "dossier de consultation replié (facultatif)");
await page.setInputFiles("#dd-miennes li[data-type=kbis] input", { name: "kbis.pdf", mimeType: "application/pdf", buffer: Buffer.from("%PDF-1.4") });
await page.setInputFiles("#dd-miennes li[data-type=urssaf] input", { name: "urssaf.pdf", mimeType: "application/pdf", buffer: Buffer.from("%PDF-1.4") });
verifier((await page.$$("#dd-miennes li.ok")).length === 2, "Kbis et URSSAF cochés");
await page.screenshot({ path: `${S}/dossier-etape3-${nom}.png`, fullPage: true });
await page.click("#dd-suite");
verifier((await page.textContent("#dd-recap")).includes("Net Pro SARL"), "récapitulatif");
verifier((await page.textContent("#dd-reste")).includes("assurance") && !(await page.textContent("#dd-reste")).includes("Kbis"), "reste à faire = pièces manquantes");
await page.screenshot({ path: `${S}/dossier-etape4-${nom}.png`, fullPage: true });
await page.click("#dd-envoyer");
await page.waitForFunction(() => document.querySelector("#toast") && !document.querySelector("#toast").hidden);
verifier(log.some((l) => l.includes("/rpc/demander") && l.includes("SIRET : 123 456 789 00012")), "infos structurées envoyées");
verifier(log.some((l) => l.includes("/rpc/soumettre_dossier")), "dossier soumis");
verifier(log.some((l) => l.includes("/dce/piece-kbis-kbis.pdf")), "pièce rangée par type");
await page.waitForSelector("#p-demandes:not([hidden])"); await page.waitForTimeout(500);
verifier(await page.isVisible("fieldset[data-pas='1']"), "retour à l'étape 1");
await page.evaluate(() => { document.querySelector("#toast").hidden = true; });
// rapport via la liste
await page.selectOption("#r-choix", "26-102961");
await page.click("#f-rapport button[type=submit]");
await page.waitForFunction(() => document.querySelector("#toast") && !document.querySelector("#toast").hidden);
verifier(log.some((l) => l.includes("/rpc/demander") && l.includes("26-102961")), "rapport demandé pour le bon avis");
// autre marché : saisie manuelle
await page.selectOption("#r-choix", "");
verifier(await page.isVisible("#r-ref-p"), "saisie visible pour autre marché");
await page.close();
}
console.log(`${ok} OK, ${ko} échec(s), erreurs JS : ${erreurs.length ? erreurs.join(" | ") : 0}`);
await browser.close();
