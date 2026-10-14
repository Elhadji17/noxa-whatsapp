// api/inscription.js
// Reçoit les demandes d'inscription de la landing page.
//
// Principe : ne jamais perdre un prospect. La demande est d'abord enregistrée
// dans un canal durable (Redis et/ou email, si configurés), puis la notification
// WhatsApp est tentée en « meilleur effort » : une réponse HTTP 200 de Meta
// signifie « accepté », pas « livré » (un message libre hors fenêtre de 24 h
// échoue ensuite, erreur 131047). On ne répond « success » au visiteur que si
// au moins un canal a réellement accepté la demande.

const MAX_LEN = 120;

function clean(value) {
  return String(value ?? '').replace(/[\r\n\t]+/g, ' ').trim().slice(0, MAX_LEN);
}

async function fetchWithTimeout(url, options = {}, timeoutMs = 8000) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    return await fetch(url, { ...options, signal: controller.signal });
  } finally {
    clearTimeout(timer);
  }
}

// Canal 1 — Redis (Upstash / Vercel KV), optionnel.
async function saveToRedis(lead) {
  const url = process.env.UPSTASH_REDIS_REST_URL || process.env.KV_REST_API_URL;
  const token = process.env.UPSTASH_REDIS_REST_TOKEN || process.env.KV_REST_API_TOKEN;
  if (!url || !token) return { ok: false, skipped: true };
  try {
    const res = await fetchWithTimeout(url, {
      method: 'POST',
      headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
      body: JSON.stringify(['LPUSH', 'noxa:leads', JSON.stringify(lead)]),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok || data.error) {
      console.error('Inscription - échec Redis', res.status, JSON.stringify(data));
      return { ok: false };
    }
    return { ok: true };
  } catch (err) {
    console.error('Inscription - erreur Redis', err);
    return { ok: false };
  }
}

// Canal 2 — Email via Resend, optionnel.
async function sendEmail(lead) {
  const apiKey = process.env.RESEND_API_KEY;
  const to = process.env.LEAD_EMAIL_TO;
  if (!apiKey || !to) return { ok: false, skipped: true };
  try {
    const res = await fetchWithTimeout('https://api.resend.com/emails', {
      method: 'POST',
      headers: { Authorization: `Bearer ${apiKey}`, 'Content-Type': 'application/json' },
      body: JSON.stringify({
        from: process.env.LEAD_EMAIL_FROM || 'NOXA <onboarding@resend.dev>',
        to: [to],
        subject: `Nouvelle inscription NOXA — ${lead.businessName}`,
        text: `Commerce : ${lead.businessName}\nSecteur : ${lead.sector}\nContact : ${lead.contactName}\nWhatsApp : ${lead.whatsapp}\nReçu le : ${lead.receivedAt}`,
      }),
    });
    if (!res.ok) {
      console.error('Inscription - échec email', res.status, await res.text());
      return { ok: false };
    }
    console.log('Inscription - email accepté par Resend, destinataire :', to);
    return { ok: true };
  } catch (err) {
    console.error('Inscription - erreur email', err);
    return { ok: false };
  }
}

// Canal 3 — WhatsApp, meilleur effort.
// Sans WHATSAPP_LEAD_TEMPLATE : message libre (échoue si tu n'as pas écrit au
// numéro émetteur dans les 24 dernières heures).
// Avec WHATSAPP_LEAD_TEMPLATE : modèle approuvé à 4 variables, dans l'ordre :
// {{1}} commerce, {{2}} secteur, {{3}} contact, {{4}} WhatsApp.
async function notifyWhatsApp(lead) {
  const phoneId = process.env.WHATSAPP_PHONE_NUMBER_ID;
  const to = process.env.ELHADJI_WHATSAPP_NUMBER; // format international, sans +
  const token = process.env.WHATSAPP_ACCESS_TOKEN;
  if (!phoneId || !to || !token) {
    console.error('Inscription - variables WhatsApp manquantes');
    return { ok: false };
  }

  const template = process.env.WHATSAPP_LEAD_TEMPLATE;
  const payload = template
    ? {
        messaging_product: 'whatsapp',
        to,
        type: 'template',
        template: {
          name: template,
          language: { code: process.env.WHATSAPP_LEAD_TEMPLATE_LANG || 'fr' },
          components: [{
            type: 'body',
            parameters: [lead.businessName, lead.sector, lead.contactName, lead.whatsapp]
              .map(text => ({ type: 'text', text })),
          }],
        },
      }
    : {
        messaging_product: 'whatsapp',
        to,
        text: {
          body: `🔔 Nouvelle inscription NOXA\nCommerce : ${lead.businessName}\nSecteur : ${lead.sector}\nContact : ${lead.contactName}\nWhatsApp : ${lead.whatsapp}`,
        },
      };

  try {
    const res = await fetchWithTimeout(`https://graph.facebook.com/v21.0/${phoneId}/messages`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
      body: JSON.stringify(payload),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      console.error('Inscription - échec WhatsApp', res.status, 'phone_number_id:', phoneId, JSON.stringify(data));
      if (data?.error?.code === 133010) {
        console.error("Inscription - le numéro émetteur n'est plus enregistré sur l'API Cloud : mets à jour WHATSAPP_PHONE_NUMBER_ID dans Vercel.");
      }
      return { ok: false };
    }
    console.log('Inscription - WhatsApp accepté par Meta (livraison non garantie) :', JSON.stringify(data.messages || data));
    return { ok: true };
  } catch (err) {
    console.error('Inscription - erreur WhatsApp', err);
    return { ok: false };
  }
}

export default async function handler(req, res) {
  if (req.method !== 'POST') {
    return res.status(405).send('Method Not Allowed');
  }

  try {
    const body = typeof req.body === 'string' ? JSON.parse(req.body) : (req.body || {});

    // Champ piège : un humain ne le remplit pas. On répond « ok » sans rien faire.
    if (clean(body.website)) {
      return res.status(200).json({ success: true });
    }

    const lead = {
      businessName: clean(body.businessName),
      sector: clean(body.sector),
      whatsapp: clean(body.whatsapp),
      contactName: clean(body.contactName),
      receivedAt: new Date().toISOString(),
    };

    if (!lead.businessName || !lead.sector || !lead.whatsapp || !lead.contactName) {
      return res.status(400).json({ error: 'Champs manquants' });
    }

    console.log('Inscription - nouvelle demande :', JSON.stringify(lead));

    const [redis, email, whatsapp] = await Promise.all([
      saveToRedis(lead),
      sendEmail(lead),
      notifyWhatsApp(lead),
    ]);

    const state = r => (r.ok ? 'ok' : r.skipped ? 'non configuré' : 'échec');
    console.log('Inscription - canaux :', JSON.stringify({
      redis: state(redis), email: state(email), whatsapp: state(whatsapp),
    }));

    const durable = redis.ok || email.ok;
    if (!durable) {
      console.warn('Inscription - aucun canal durable (Redis/email) : configure-en un pour ne perdre aucune demande.');
    }

    if (!durable && !whatsapp.ok) {
      // Aucun canal n'a accepté la demande : on le dit au visiteur plutôt que de
      // lui afficher un faux succès.
      return res.status(500).json({ error: 'Demande non enregistrée' });
    }

    return res.status(200).json({ success: true });
  } catch (error) {
    console.error('Erreur inscription:', error);
    return res.status(500).json({ error: 'Erreur serveur' });
  }
}