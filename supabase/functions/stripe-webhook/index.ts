// Webhook Stripe → droits des comptes clients, en temps réel (la synchro toutes les 10 min reste le filet de sécurité).
// 1. Vérifie la signature Stripe (sinon 400). 2. Relit l'abonnement CHEZ Stripe (état réel, ordre des événements
// sans importance). 3. Appelle public.appliquer_abonnement, qui ignore un événement déjà traité (idempotent) et ne
// touche qu'un compte dont l'adresse est vérifiée. Une erreur renvoie 500 : Stripe renvoie l'événement plus tard.
// Secrets (posés par comptes-setup) : STRIPE_SECRET_KEY, STRIPE_WEBHOOK_SECRET, RADAR_SERVICE_KEY.
import Stripe from "npm:stripe@17.7.0";

const stripe = new Stripe(Deno.env.get("STRIPE_SECRET_KEY") ?? "", { httpClient: Stripe.createFetchHttpClient() });
const crypto = Stripe.createSubtleCryptoProvider();
const BASE = Deno.env.get("SUPABASE_URL") ?? "";
const CLE = Deno.env.get("RADAR_SERVICE_KEY") ?? Deno.env.get("SUPABASE_SERVICE_ROLE_KEY") ?? "";

function abonnementDe(ev: Stripe.Event): string | null {
  const o = ev.data.object as Record<string, any>;
  if (ev.type.startsWith("customer.subscription.")) return o.id;
  if (ev.type === "checkout.session.completed") return o.subscription ?? null;
  if (ev.type.startsWith("invoice.")) return o.subscription ?? o.parent?.subscription_details?.subscription ?? null;
  return null;   // charge.refunded (dossiers à l'unité) : traité par le workflow dossier.yml
}

Deno.serve(async (req) => {
  if (req.method !== "POST") return new Response("ok");
  const corps = await req.text();
  let ev: Stripe.Event;
  try {
    ev = await stripe.webhooks.constructEventAsync(corps, req.headers.get("stripe-signature") ?? "",
      Deno.env.get("STRIPE_WEBHOOK_SECRET") ?? "", undefined, crypto);
  } catch (_) {
    return new Response("signature invalide", { status: 400 });
  }
  const id = abonnementDe(ev);
  if (!id) return new Response("ignoré");
  try {
    const sub = await stripe.subscriptions.retrieve(id, { expand: ["customer"] });
    const client = sub.customer as Stripe.Customer;
    if (!client || client.deleted || !client.email) return new Response("sans e-mail");
    const r = await fetch(`${BASE}/rest/v1/rpc/appliquer_abonnement`, {
      method: "POST",
      headers: { apikey: CLE, "Content-Type": "application/json", ...(CLE.startsWith("ey") ? { Authorization: `Bearer ${CLE}` } : {}) },
      body: JSON.stringify({
        p_evenement: ev.id, p_type: ev.type, p_email: client.email, p_price: sub.items.data[0]?.price?.id ?? "",
        p_statut: sub.status, p_customer: client.id, p_subscription: sub.id,
        p_fin_essai: sub.status === "trialing" && sub.trial_end ? new Date(sub.trial_end * 1000).toISOString() : null,
      }),
    });
    if (!r.ok) throw new Error(`base ${r.status}`);
    return new Response(await r.text());
  } catch (e) {
    console.error("webhook", ev.type, String(e).slice(0, 200));   // jamais d'e-mail ni de clé dans le journal
    return new Response("erreur", { status: 500 });
  }
});
