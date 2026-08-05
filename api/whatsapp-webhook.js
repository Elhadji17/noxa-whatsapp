// api/whatsapp-webhook.js

// Un objet qui fait la correspondance entre le numéro WhatsApp de chaque
// commerçant (son phone_number_id) et son catalogue/prompt système.
// Ajoute une nouvelle entrée ici à chaque nouveau commerçant test.
const MERCHANTS = {
  "1235231613010112": {
    name: "Boutique test (numéro Meta)",
    products: [
      { name: "Robe Wax imprimée", price: "15 000 FCFA", imageUrl: "https://noxa-whatsapp.vercel.app/images/robe.jpg" },
      { name: "Ensemble Boubou homme", price: "25 000 FCFA", imageUrl: "https://noxa-whatsapp.vercel.app/images/ensemble.jpg" },
      { name: "Chemise brodée", price: "12 000 FCFA", imageUrl: "https://noxa-whatsapp.vercel.app/images/chemise.jpg" },
      { name: "Sac à main tissu wax", price: "8 000 FCFA", imageUrl: "https://noxa-whatsapp.vercel.app/images/sac.jpg" },
      { name: "Turban assorti", price: "3 000 FCFA", imageUrl: "https://noxa-whatsapp.vercel.app/images/turban.jpg" },
    ],
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

Avant de dire "je n'ai pas cette information", vérifie une dernière fois 
si le produit ou l'info demandée figure dans la liste PRODUITS ci-dessus, 
même si la question a déjà été posée ou reformulée différemment. 
Ne dis "je n'ai pas cette info" que si le produit ou le service n'apparaît 
vraiment nulle part dans le catalogue fourni.

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
// api/whatsapp-webhook.js


const conversationHistory = {};

async function sendWhatsAppImage(phoneNumberId, to, imageUrl, caption) {
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
        to: to,
        type: 'image',
        image: { link: imageUrl, caption: caption || '' },
      }),
    }
  );
}

// Nouvelle fonction : télécharge un média WhatsApp (audio) et le transcrit via Whisper
async function transcribeWhatsAppAudio(mediaId) {
  const mediaInfoResponse = await fetch(
    `https://graph.facebook.com/v21.0/${mediaId}`,
    { headers: { 'Authorization': `Bearer ${process.env.WHATSAPP_ACCESS_TOKEN}` } }
  );
  const mediaInfo = await mediaInfoResponse.json();
  const audioUrl = mediaInfo.url;

  const audioResponse = await fetch(audioUrl, {
    headers: { 'Authorization': `Bearer ${process.env.WHATSAPP_ACCESS_TOKEN}` },
  });
  const audioBuffer = await audioResponse.arrayBuffer();

  const formData = new FormData();
  formData.append('file', new Blob([audioBuffer], { type: 'audio/ogg' }), 'audio.ogg');
  formData.append('model', 'whisper-1');
  // On force le français comme langue principale : Whisper a alors moins
  // tendance à halluciner une langue totalement différente (italien, arabe)
  // et transcrit le wolof "phonétiquement" en alphabet latin, ce qui reste
  // plus proche du wolof réel que les hallucinations observées sans ce réglage
  formData.append('language', 'fr');

  const transcriptionResponse = await fetch(
    'https://api.openai.com/v1/audio/transcriptions',
    {
      method: 'POST',
      headers: { 'Authorization': `Bearer ${process.env.OPENAI_API_KEY}` },
      body: formData,
    }
  );

  const transcriptionData = await transcriptionResponse.json();
  return transcriptionData.text || null;
}

export default async function handler(req, res) {
  if (req.method === 'GET') {
    // ... (inchangé)
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
      let text = message.text?.body;

      // Nouveau : si c'est un message vocal, on le transcrit d'abord
      if (message.type === 'audio' && message.audio?.id) {
        console.log(`Message vocal reçu de ${from}, transcription en cours...`);
        text = await transcribeWhatsAppAudio(message.audio.id);

        if (!text) {
          console.error('Échec de la transcription audio');
          text = "[message vocal non transcrit]";
        } else {
          console.log(`Transcription obtenue: ${text}`);
        }
      }

      if (!text) {
        return res.status(200).send('OK');
      }

      const merchant = MERCHANTS[phoneNumberId];
      if (!merchant) {
        return res.status(200).send('OK');
      }

      const conversationKey = `${phoneNumberId}_${from}`;
      if (!conversationHistory[conversationKey]) {
        conversationHistory[conversationKey] = [];
      }

      conversationHistory[conversationKey].push({ role: 'user', content: text });
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

      conversationHistory[conversationKey].push({ role: 'assistant', content: replyText });

      // Envoi du texte, comme avant
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

      // Nouveau : détecte si un produit précis est mentionné et envoie sa photo
      const mentionedProduct = merchant.products.find(p =>
        replyText.toLowerCase().includes(p.name.toLowerCase().split(' ')[0])
      );

      if (mentionedProduct) {
        await sendWhatsAppImage(phoneNumberId, from, mentionedProduct.imageUrl, `${mentionedProduct.name} — ${mentionedProduct.price}`);
      }

      return res.status(200).send('OK');
    } catch (error) {
      console.error('Erreur webhook:', error);
      return res.status(200).send('OK');
    }
  }

  return res.status(405).send('Method Not Allowed');
}