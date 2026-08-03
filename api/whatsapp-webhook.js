// api/whatsapp-webhook.js

// Un objet qui fait la correspondance entre le numéro WhatsApp de chaque
// commerçant (son phone_number_id) et son catalogue/prompt système.
// Ajoute une nouvelle entrée ici à chaque nouveau commerçant test.
const MERCHANTS = {
  "1235231613010112": {
    name: "Boutique test (numéro Meta)",
    systemPrompt: `Tu es l'assistant WhatsApp de [Nom boutique], boutique de vêtements à Dakar.

RÈGLE ABSOLUE : N'utilise QUE les informations du catalogue ci-dessous. Si un produit, une taille, un prix ou un service n'y figure pas, réponds : "Je n'ai pas cette info précise, on vérifie et je reviens vers vous vite !" Ne jamais inventer un prix, une taille ou une disponibilité.

PRODUITS :
- Robe Wax imprimée — Tailles S/M/L — 15 000 FCFA
- Ensemble Boubou homme — Tailles M/L/XL — 25 000 FCFA
- Chemise brodée — Tailles S/M/L/XL — 12 000 FCFA
- Sac à main tissu wax — Taille unique — 8 000 FCFA
- Turban assorti — Taille unique — 3 000 FCFA

LIVRAISON : Dakar centre 1 500 FCFA, banlieue 2 500 FCFA, gratuite dès 30 000 FCFA
HORAIRES : Lundi-Samedi 9h-19h, fermé dimanche

GESTION DE L'INTÉRÊT CLIENT :
Quand le client exprime un intérêt ou veut commander (ex : "je suis intéressé", 
"je le veux", "ça m'intéresse", "oui"), ne redemande JAMAIS ce qui l'intéresse 
s'il vient de le dire. Avance directement la conversation :
1. Confirme le produit et le prix
2. Demande sa zone de livraison
3. Indique que le paiement se fait par Wave (le lien sera envoyé séparément)
Reste concret et avance vers la commande, ne tourne jamais en rond.

TON : Chaleureux, direct, mélange naturel français/wolof simple ("Waaw", "am nañu ko", "jërejëf"). Réponses courtes, 2-3 phrases max.`,
  },

  // Exemple pour un futur commerçant réel — duplique ce bloc et remplis-le
  // "PHONE_NUMBER_ID_DU_COMMERCANT": {
  //   name: "Nom du commerce",
  //   systemPrompt: `... catalogue et infos réelles du commerçant ...`,
  // },
};

// Historique en mémoire par conversation (clé = numéro du client)
// Attention : se réinitialise à chaque redéploiement ou inactivité prolongée
// de la fonction serverless — suffisant pour une démo, pas pour la production.
const conversationHistory = {};

export default async function handler(req, res) {
  if (req.method === 'GET') {
    const VERIFY_TOKEN = process.env.WHATSAPP_VERIFY_TOKEN;
    const mode = req.query['hub.mode'];
    const token = req.query['hub.verify_token'];
    const challenge = req.query['hub.challenge'];

    if (mode === 'subscribe' && token === VERIFY_TOKEN) {
      return res.status(200).send(challenge);
    } else {
      return res.status(403).send('Verification failed');
    }
  }

  if (req.method === 'POST') {
    try {
      const body = req.body;
      const entry = body.entry?.[0];
      const change = entry?.changes?.[0];
      const message = change?.value?.messages?.[0];
      const phoneNumberId = change?.value?.metadata?.phone_number_id;

      if (!message) {
        return res.status(200).send('OK');
      }

      const from = message.from;
      const text = message.text?.body;

      // On identifie le commerçant à partir du numéro qui a reçu le message
      const merchant = MERCHANTS[phoneNumberId];
  if (!merchant) {
    return res.status(200).send('OK');
  }

  const conversationKey = `${phoneNumberId}_${from}`;
  if (!conversationHistory[conversationKey]) {
    conversationHistory[conversationKey] = [];
  }

  // Ajoute le message du client à l'historique
  conversationHistory[conversationKey].push({ role: 'user', content: text });

  // Garde seulement les 10 derniers messages pour éviter un prompt trop long
  const recentHistory = conversationHistory[conversationKey].slice(-10);

  const aiResponse = await fetch('https://api.openai.com/v1/chat/completions', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'Authorization': `Bearer ${process.env.OPENAI_API_KEY}`,
    },
    body: JSON.stringify({
      model: 'gpt-4o-mini',
      messages: [
        { role: 'system', content: merchant.systemPrompt },
        ...recentHistory,
      ],
    }),
  });

  const aiData = await aiResponse.json();
  const replyText = aiData.choices?.[0]?.message?.content || "Désolé, je n'ai pas pu traiter votre message.";

  // Ajoute la réponse du bot à l'historique aussi
  conversationHistory[conversationKey].push({ role: 'assistant', content: replyText });

      // On utilise le même phone_number_id pour répondre depuis le bon numéro
      await fetch(
        `https://graph.facebook.com/v21.0/${phoneNumberId}/messages`,
        {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'Authorization': `Bearer ${process.env.WHATSAPP_ACCESS_TOKEN}`,
          },
          body: JSON.stringify({
            messaging_product: 'whatsapp',
            to: from,
            text: { body: replyText },
          }),
        }
      );

      return res.status(200).send('OK');
    } catch (error) {
      console.error('Erreur webhook:', error);
      return res.status(200).send('OK');
    }
  }

  return res.status(405).send('Method Not Allowed');
}