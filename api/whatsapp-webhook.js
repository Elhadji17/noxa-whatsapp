// api/whatsapp-webhook.js

function buildProductListText(products) {
  return products.map(p => {
    const sizes = p.sizes ? ` — Tailles ${p.sizes.join('/')}` : '';
    let colorNames = [];
    if (p.colorVariants) colorNames = colorNames.concat(p.colorVariants.map(c => c.color));
    if (p.extraColors) colorNames = colorNames.concat(p.extraColors);
    if (!p.colorVariants && p.colors) colorNames = p.colors;
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

Ne dis JAMAIS que tu n'as pas de photo disponible — tu en as toujours pour 
les produits du catalogue. Utilise systématiquement le marqueur [PHOTO: ...] 
dès qu'une couleur est connue (même choisie plus tôt dans la conversation) 
ou que le client redemande à la voir.

TON : Chaleureux, direct, mélange naturel français/wolof simple ("Waaw", "am nañu ko", "jërejëf"). Réponses courtes, 2-3 phrases max.`;
}

const MERCHANTS = {
  "1291978550658723": {
  name: "AD Shoes",
  ownerWhatsApp: "33664017511",
  wavePaymentNumber: "221781645041",
  delivery: "Dakar Centre/Plateau/Médina 1 500 FCFA, Sacré-Cœur/Mermoz/Ouakam/Yoff/Maristes 1 500 FCFA, Parcelles Assainies/Guédiawaye/Pikine 2 000 FCFA, Rufisque/Bargny 3 000 FCFA",
  hours: "Lundi-Samedi 9h-20h30 (non-stop), Dimanche 15h-20h",
  products: [
    {
      name: "Moccasin Cuir Dakar Classic",
      sizes: ["39", "40", "41", "42", "43", "44"],
      colorVariants: [
        { color: "Noir", imageUrl: "https://noxa-whatsapp.vercel.app/images/moccasin-noir.jpg" },
        { color: "Marron Chocolat", imageUrl: "https://noxa-whatsapp.vercel.app/images/moccasin-marron.jpg" },
      ],
      extraColors: ["Tan"],
      price: "18 000 FCFA",
    },
    {
      name: "Chaussure de ville Ndakarou",
      sizes: ["40", "41", "42", "43", "44", "45"],
      colorVariants: [
        { color: "Noir(Goudi)", imageUrl: "https://noxa-whatsapp.vercel.app/images/ndakarou-noir.jpg" },
        { color: "Marron(Bant)", imageUrl: "https://noxa-whatsapp.vercel.app/images/ndakarou-marron.jpg" },
      ],
      extraColors: ["Vert"],
      price: "22 000 FCFA",
    },
    {
      name: "Sandale Cuir Samarra",
      sizes: ["38", "39", "40", "41", "42", "43"],
      colorVariants: [
        { color: "Noir", imageUrl: "https://noxa-whatsapp.vercel.app/images/samarra-noir.jpg" },
        { color: "Marron", imageUrl: "https://noxa-whatsapp.vercel.app/images/samarra-marron.jpg" },
      ],
      price: "12 000 FCFA",
    },
    {
      name: "Chaussure Richelieu Luxe Officer",
      sizes: ["39", "40", "41", "42", "43", "44"],
      colorVariants: [
        { color: "Noir Verni", imageUrl: "https://noxa-whatsapp.vercel.app/images/luxe-officer-noir.jpg" },
        { color: "Marron Sombre", imageUrl: "https://noxa-whatsapp.vercel.app/images/luxe-officer-marron.jpg" },
      ],
      price: "28 000 FCFA",
    },
    {
      name: "Air Force One",
      sizes: ["40", "41", "42", "43", "44", "45"],
      colorVariants: [
        { color: "Blanc/Bleu", imageUrl: "https://noxa-whatsapp.vercel.app/images/airforce-bleu.jpg" },
        { color: "Rouge/Noir", imageUrl: "https://noxa-whatsapp.vercel.app/images/airforce-rouge.jpg" },
      ],
      extraColors: ["Blanc Pure", "Noir"],
      price: "10 000 FCFA",
    },
    {
      name: "Samba",
      sizes: ["38", "39", "40", "41", "42", "43", "44"],
      colorVariants: [
        { color: "Blanc/Gris", imageUrl: "https://noxa-whatsapp.vercel.app/images/samba-gris.jpg" },
        { color: "Blanc/Bleu", imageUrl: "https://noxa-whatsapp.vercel.app/images/samba-bleu.jpg" },
      ],
      extraColors: ["Vert"],
      price: "12 000 FCFA",
    },
    {
      name: "Chaussure de sport Taggat Yaram",
      sizes: ["40", "41", "42", "43", "44", "45"],
      colorVariants: [
        { color: "Bleu(Modou LO)", imageUrl: "https://noxa-whatsapp.vercel.app/images/taggat-bleu.jpg" },
        { color: "Rouge/Noir(Balla GAYE 2)", imageUrl: "https://noxa-whatsapp.vercel.app/images/taggat-rouge.jpg" },
      ],
      extraColors: ["Noir"],
      price: "20 000 FCFA",
    },
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
const lastColorPerConversation = {}; // ← à ajouter

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
  const response = await fetch(
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
  const data = await response.json();
  console.log('DEBUG - Résultat envoi image:', imageUrl, JSON.stringify(data));
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
        finalClientText.toLowerCase().includes(p.name.toLowerCase())
      );

      if (mentionedProduct) {
        if (mentionedProduct.colorVariants) {
          let instruction = photoMatch ? photoMatch[1].trim() : null;

          if (!instruction) {
            const clientTextLower = text.toLowerCase();
            const clientColor = mentionedProduct.colorVariants.find(c => {
              const colorLower = c.color.toLowerCase();
              return clientTextLower.includes(colorLower) || colorLower.includes(clientTextLower);
            });
            if (clientColor) {
              instruction = `couleur=${clientColor.color}`;
              lastColorPerConversation[conversationKey] = clientColor.color;
            } else if (lastColorPerConversation[conversationKey]) {
              instruction = `couleur=${lastColorPerConversation[conversationKey]}`;
            }
          } else if (instruction.startsWith('couleur=')) {
            lastColorPerConversation[conversationKey] = instruction.replace('couleur=', '').trim();
          }

          // ↓↓↓ C'est cette partie qui manquait entièrement ↓↓↓
          if (instruction === 'toutes') {
            for (const variant of mentionedProduct.colorVariants) {
              await sendWhatsAppImage(phoneNumberId, from, variant.imageUrl, `${mentionedProduct.name} (${variant.color}) — ${mentionedProduct.price}`);
            }
          } else if (instruction && instruction.startsWith('couleur=')) {
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
        const notifResponse = await fetch(
          `https://graph.facebook.com/v21.0/${phoneNumberId}/messages`,
          {
            method: 'POST',
            headers: {
              'Content-Type': 'application/json',
              'Authorization': `Bearer ${process.env.WHATSAPP_ACCESS_TOKEN}`,
            },
            body: JSON.stringify({
              messaging_product: 'whatsapp',
              to: merchant.ownerWhatsApp,
              text: { body: `🛍️ Nouvelle commande !\n${orderMatch[1]}\nClient: ${from}\n\nVérifiez votre app Wave pour confirmer la réception du paiement avant de valider.` },
            }),
          }
        );
        const notifData = await notifResponse.json();
        console.log('DEBUG - Résultat notification commerçant:', JSON.stringify(notifData));
      }

      return res.status(200).send('OK');
    } catch (error) {
      console.error('Erreur webhook:', error);
      return res.status(200).send('OK');
    }
  }

  return res.status(405).send('Method Not Allowed');
}