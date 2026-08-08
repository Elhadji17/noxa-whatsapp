// api/whatsapp-webhook.js

function buildProductListText(products) {
  return products.map(p => {
    const sizes = p.sizes ? ` — Tailles ${p.sizes.join('/')}` : '';
    const colorNames = p.colorVariants ? p.colorVariants.map(c => c.color) : (p.colors || []);
    const colors = colorNames.length ? ` — Couleurs: ${colorNames.join(', ')}` : '';
    return `- ${p.name}${sizes}${colors} — ${p.price}`;
  }).join('\n');
}

function buildSystemPrompt(merchantInfo) {
  return `Tu es l'assistant WhatsApp de ${merchantInfo.name}, boutique de vêtements à Dakar.

RÈGLE ABSOLUE : N'utilise QUE les informations du catalogue ci-dessous. Si un produit, une taille, une couleur, un prix ou un service n'y figure pas, réponds : "Je n'ai pas cette info précise, on vérifie et je reviens vers vous vite !" Ne jamais inventer un prix, une taille, une couleur ou une disponibilité.

PRODUITS :
${buildProductListText(merchantInfo.products)}

LIVRAISON : ${merchantInfo.delivery}
HORAIRES : ${merchantInfo.hours}

GESTION DE L'INTÉRÊT CLIENT :
Quand le client exprime un intérêt ou veut commander (ex : "je suis intéressé", 
"je le veux", "ça m'intéresse", "oui"), ne redemande JAMAIS ce qui l'intéresse 
s'il vient de le dire. Avance directement la conversation :
1. Confirme le produit, la taille/couleur si applicable, et le prix
2. Demande sa zone de livraison
3. Indique que le paiement se fait par Wave (le lien sera envoyé séparément)
Reste concret et avance vers la commande, ne tourne jamais en rond.

Avant de dire "je n'ai pas cette information", vérifie une dernière fois 
si le produit ou l'info demandée figure dans la liste PRODUITS ci-dessus, 
même si la question a déjà été posée ou reformulée différemment. 
Ne dis "je n'ai pas cette info" que si le produit ou le service n'apparaît 
vraiment nulle part dans le catalogue fourni.

GESTION DES COULEURS/TAILLES INDISPONIBLES :
Si un client demande une couleur ou une taille qui n'existe pas pour un 
produit du catalogue, ne dis pas simplement "je n'ai pas cette info" — 
propose activement les couleurs/tailles réellement disponibles pour ce 
produit.

TON : Chaleureux, direct, mélange naturel français/wolof simple ("Waaw", "am nañu ko", "jërejëf"). Réponses courtes, 2-3 phrases max.`;
}

const MERCHANTS = {
  "1235231613010112": {
    name: "Boutique test (numéro Meta)",
    delivery: "Dakar centre 1 500 FCFA, banlieue 2 500 FCFA, gratuite dès 30 000 FCFA",
    hours: "Lundi-Samedi 9h-19h, fermé dimanche",
    products: [
      {
        name: "Robe Wax imprimée",
        sizes: ["S", "M", "L"],
        colorVariants: [
          { color: "Bleu", imageUrl: "https://noxa-whatsapp.vercel.app/images/robe-bleu.jpg" },
          { color: "Rouge", imageUrl: "https://noxa-whatsapp.vercel.app/images/robe-noir.jpg" },
          { color: "Rouge", imageUrl: "https://noxa-whatsapp.vercel.app/images/robe-rouge.jpg" },
        ],
        price: "15 000 FCFA",
      },
      {
        name: "Ensemble Boubou homme",
        sizes: ["M", "L", "XL"],
        colorVariants: [
          { color: "Noir", imageUrl: "https://noxa-whatsapp.vercel.app/images/ensemble-noir.jpg" },
          { color: "Blanc", imageUrl: "https://noxa-whatsapp.vercel.app/images/ensemble-bleu.jpg" },
        ],
        price: "25 000 FCFA",
      },
      { name: "Chemise brodée", sizes: ["S", "M", "L", "XL"], colors: ["Blanc"], price: "12 000 FCFA", imageUrl: "https://noxa-whatsapp.vercel.app/images/chemise.jpg" },
      { name: "Sac à main tissu wax", sizes: ["Taille unique"], colors: ["Multicolore"], price: "8 000 FCFA", imageUrl: "https://noxa-whatsapp.vercel.app/images/sac.jpg" },
      { name: "Turban assorti", sizes: ["Taille unique"], colors: ["Vert"], price: "3 000 FCFA", imageUrl: "https://noxa-whatsapp.vercel.app/images/turban.jpg" },
    ],
  },
  "1321520757705474": {
    name: "Boutique test (numéro Sénégal)",
    delivery: "Dakar centre 1 500 FCFA, banlieue 2 500 FCFA, gratuite dès 30 000 FCFA",
    hours: "Lundi-Samedi 9h-19h, fermé dimanche",
    products: [
      {
        name: "Robe Wax imprimée",
        sizes: ["S", "M", "L"],
        colorVariants: [
          { color: "Bleu", imageUrl: "https://noxa-whatsapp.vercel.app/images/robe-bleu.jpg" },
          { color: "Rouge", imageUrl: "https://noxa-whatsapp.vercel.app/images/robe-rouge.jpg" },
        ],
        price: "15 000 FCFA",
      },
      {
        name: "Ensemble Boubou homme",
        sizes: ["M", "L", "XL"],
        colorVariants: [
          { color: "Noir", imageUrl: "https://noxa-whatsapp.vercel.app/images/ensemble-noir.jpg" },
          { color: "Blanc", imageUrl: "https://noxa-whatsapp.vercel.app/images/ensemble-blanc.jpg" },
        ],
        price: "25 000 FCFA",
      },
      { name: "Chemise brodée", sizes: ["S", "M", "L", "XL"], colors: ["Blanc"], price: "12 000 FCFA", imageUrl: "https://noxa-whatsapp.vercel.app/images/chemise.jpg" },
      { name: "Sac à main tissu wax", sizes: ["Taille unique"], colors: ["Multicolore"], price: "8 000 FCFA", imageUrl: "https://noxa-whatsapp.vercel.app/images/sac.jpg" },
      { name: "Turban assorti", sizes: ["Taille unique"], colors: ["Vert"], price: "3 000 FCFA", imageUrl: "https://noxa-whatsapp.vercel.app/images/turban.jpg" },
    ],
  },
};

// Génère le systemPrompt de chaque commerçant automatiquement à partir de ses produits
Object.values(MERCHANTS).forEach(m => {
  m.systemPrompt = buildSystemPrompt(m);
});

const conversationHistory = {};

async function sendWhatsAppText(phoneNumberId, to, body) {
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
        text: { body },
      }),
    }
  );
}

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

      // Types de contenu non supportés
      if (['image', 'sticker', 'location', 'document'].includes(message.type)) {
        await sendWhatsAppText(phoneNumberId, from, "Je ne peux pas encore traiter ce type de message. Dites-moi en quelques mots ce qui vous intéresse, je réponds tout de suite ! 😊");
        return res.status(200).send('OK');
      }

      let text = message.text?.body;

      // Message vocal : transcription
      if (message.type === 'audio' && message.audio?.id) {
        text = await transcribeWhatsAppAudio(message.audio.id);

        if (!text) {
          await sendWhatsAppText(phoneNumberId, from, "Je n'ai pas bien compris votre message vocal, pouvez-vous réessayer ou l'écrire en texte ?");
          return res.status(200).send('OK');
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

      let aiData;
      try {
        const aiResponse = await fetch('https://api.openai.com/v1/chat/completions', {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'Authorization': `Bearer ${process.env.OPENAI_API_KEY}`,
          },
          body: JSON.stringify({
            model: 'gpt-4o',
            messages: [
              { role: 'system', content: merchant.systemPrompt },
              ...recentHistory,
            ],
          }),
        });
        aiData = await aiResponse.json();
      } catch (err) {
        console.error('Erreur appel OpenAI:', err);
        await sendWhatsAppText(phoneNumberId, from, "Petit souci technique de notre côté, on revient vers vous très vite !");
        return res.status(200).send('OK');
      }

      const replyText = aiData.choices?.[0]?.message?.content || "Désolé, je n'ai pas pu traiter votre message.";

      conversationHistory[conversationKey].push({ role: 'assistant', content: replyText });

      await sendWhatsAppText(phoneNumberId, from, replyText);

      const mentionedProduct = merchant.products.find(p =>
        replyText.toLowerCase().includes(p.name.toLowerCase().split(' ')[0])
      );

      if (mentionedProduct) {
        let imageToSend = mentionedProduct.colorVariants
          ? mentionedProduct.colorVariants[0].imageUrl
          : mentionedProduct.imageUrl;

        if (mentionedProduct.colorVariants) {
          const matchedColor = mentionedProduct.colorVariants.find(c =>
            replyText.toLowerCase().includes(c.color.toLowerCase())
          );
          if (matchedColor) {
            imageToSend = matchedColor.imageUrl;
          }
        }

        await sendWhatsAppImage(phoneNumberId, from, imageToSend, `${mentionedProduct.name} — ${mentionedProduct.price}`);
      }

      return res.status(200).send('OK');
    } catch (error) {
      console.error('Erreur webhook:', error);
      return res.status(200).send('OK');
    }
  }

  return res.status(405).send('Method Not Allowed');
}