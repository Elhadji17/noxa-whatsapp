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
3. Une fois la zone connue, annonce le paiement par Wave : dis au client de 
   payer via l'application Wave, en entrant ce numéro : ${merchantInfo.wavePaymentNumber}
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

GESTION DES PHOTOS :
Quand tu réponds à propos d'un produit qui a plusieurs couleurs, termine 
TOUJOURS ta réponse par un marqueur sur sa propre ligne, jamais visible ou 
mentionné au client :
- Si le client a choisi une couleur précise : [PHOTO: couleur=NomCouleur]
- Si le client veut voir toutes les couleurs (ex: "toutes", "je veux voir", 
  "montre-moi", "les deux") : [PHOTO: toutes]
- Si aucune couleur n'est encore choisie et qu'il n'a pas demandé à voir : 
  n'ajoute aucun marqueur

GESTION DE LA CONFIRMATION DE COMMANDE :
Quand une commande est réellement confirmée (produit + taille/couleur + 
zone de livraison connus, client d'accord pour commander, tu viens de lui 
donner les instructions de paiement Wave), termine ta réponse par ce 
marqueur exact sur sa propre ligne, jamais visible ou mentionné au client :
[COMMANDE: produit=NOM; zone=ZONE; total=MONTANT]
N'ajoute ce marqueur qu'une seule fois par commande, pas à chaque message 
qui suit.

TON : Chaleureux, direct, mélange naturel français/wolof simple ("Waaw", "am nañu ko", "jërejëf"). Réponses courtes, 2-3 phrases max.`;
}

const MERCHANTS = {
  "1235231613010112": {
    name: "Boutique test (numéro Meta)",
    ownerWhatsApp: "221781178874", // à remplacer par le vrai numéro du commerçant
    wavePaymentNumber: "221781178874", // à remplacer par le vrai numéro Wave du commerçant
    delivery: "Dakar centre 1 500 FCFA, banlieue 2 500 FCFA, gratuite dès 30 000 FCFA",
    hours: "Lundi-Samedi 9h-19h, fermé dimanche",
    products: [
      {
        name: "Robe Wax imprimée",
        sizes: ["S", "M", "L"],
        colorVariants: [
          { color: "Bleu", imageUrl: "https://noxa-whatsapp.vercel.app/images/robe-bleu.jpg" },
          { color: "Rouge", imageUrl: "https://noxa-whatsapp.vercel.app/images/robe-rouge-v2.jpg" },
          { color: "Noir", imageUrl: "https://noxa-whatsapp.vercel.app/images/robe-noir.jpg" },
        ],
        price: "15 000 FCFA",
      },
      {
        name: "Ensemble Boubou homme",
        sizes: ["M", "L", "XL"],
        colorVariants: [
          { color: "Noir", imageUrl: "https://noxa-whatsapp.vercel.app/images/ensemble-noir.jpg" },
          { color: "Bleu", imageUrl: "https://noxa-whatsapp.vercel.app/images/ensemble-bleu.jpg" },
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
    ownerWhatsApp: "221781178874", // à remplacer par le vrai numéro du commerçant
    wavePaymentNumber: "221781178874", // à remplacer par le vrai numéro Wave du commerçant
    delivery: "Dakar centre 1 500 FCFA, banlieue 2 500 FCFA, gratuite dès 30 000 FCFA",
    hours: "Lundi-Samedi 9h-19h, fermé dimanche",
    products: [
      {
        name: "Robe Wax imprimée",
        sizes: ["S", "M", "L"],
        colorVariants: [
          { color: "Bleu", imageUrl: "https://noxa-whatsapp.vercel.app/images/robe-bleu.jpg" },
          { color: "Rouge", imageUrl: "https://noxa-whatsapp.vercel.app/images/robe-rouge-v2.jpg" },
          { color: "Noir", imageUrl: "https://noxa-whatsapp.vercel.app/images/robe-noir.jpg" },
        ],
        price: "15 000 FCFA",
      },
      {
        name: "Ensemble Boubou homme",
        sizes: ["M", "L", "XL"],
        colorVariants: [
          { color: "Noir", imageUrl: "https://noxa-whatsapp.vercel.app/images/ensemble-noir.jpg" },
          { color: "Bleu", imageUrl: "https://noxa-whatsapp.vercel.app/images/ensemble-bleu.jpg" },
        ],
        price: "25 000 FCFA",
      },
      { name: "Chemise brodée", sizes: ["S", "M", "L", "XL"], colors: ["Blanc"], price: "12 000 FCFA", imageUrl: "https://noxa-whatsapp.vercel.app/images/chemise.jpg" },
      { name: "Sac à main tissu wax", sizes: ["Taille unique"], colors: ["Multicolore"], price: "8 000 FCFA", imageUrl: "https://noxa-whatsapp.vercel.app/images/sac.jpg" },
      { name: "Turban assorti", sizes: ["Taille unique"], colors: ["Vert"], price: "3 000 FCFA", imageUrl: "https://noxa-whatsapp.vercel.app/images/turban.jpg" },
    ],
  },
};

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

      // Détecte les marqueurs cachés (photo + commande) avant de nettoyer le texte
      const photoMatch = replyText.match(/\[PHOTO:\s*(.+?)\]/);
      const orderMatch = replyText.match(/\[COMMANDE:\s*(.+?)\]/);
      console.log('DEBUG - replyText complet:', replyText);
      console.log('DEBUG - orderMatch trouvé:', orderMatch ? orderMatch[1] : 'AUCUN');

      let finalClientText = replyText
        .replace(/\[PHOTO:.+?\]/, '')
        .replace(/\[COMMANDE:.+?\]/, '')
        .trim();

      // Envoie le texte nettoyé (sans marqueurs) au client
      await sendWhatsAppText(phoneNumberId, from, finalClientText);

      // Gestion de l'envoi de photo
      const mentionedProduct = merchant.products.find(p =>
        finalClientText.toLowerCase().includes(p.name.toLowerCase().split(' ')[0])
      );

      if (mentionedProduct && photoMatch) {
        const instruction = photoMatch[1].trim();

        if (mentionedProduct.colorVariants) {
          if (instruction === 'toutes') {
            for (const variant of mentionedProduct.colorVariants) {
              await sendWhatsAppImage(phoneNumberId, from, variant.imageUrl, `${mentionedProduct.name} (${variant.color}) — ${mentionedProduct.price}`);
            }
          } else if (instruction.startsWith('couleur=')) {
            const colorName = instruction.replace('couleur=', '').trim();
            const matchedColor = mentionedProduct.colorVariants.find(c =>
              c.color.toLowerCase() === colorName.toLowerCase()
            );
            if (matchedColor) {
              await sendWhatsAppImage(phoneNumberId, from, matchedColor.imageUrl, `${mentionedProduct.name} (${matchedColor.color}) — ${mentionedProduct.price}`);
            }
          }
        } else {
          await sendWhatsAppImage(phoneNumberId, from, mentionedProduct.imageUrl, `${mentionedProduct.name} — ${mentionedProduct.price}`);
        }
      }

      // Notifie le commerçant si une commande vient d'être confirmée
      if (orderMatch && merchant.ownerWhatsApp) {
        await sendWhatsAppText(
          phoneNumberId,
          merchant.ownerWhatsApp,
          `🛍️ Nouvelle commande !\n${orderMatch[1]}\nClient: ${from}\n\nVérifiez votre app Wave pour confirmer la réception du paiement avant de valider.`
        );
      }

      return res.status(200).send('OK');
    } catch (error) {
      console.error('Erreur webhook:', error);
      return res.status(200).send('OK');
    }
  }

  return res.status(405).send('Method Not Allowed');
}