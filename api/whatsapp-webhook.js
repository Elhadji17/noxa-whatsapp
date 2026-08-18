// api/whatsapp-webhook.js

function buildProductListText(products) {
  return products.map(p => {
    const sizes = p.sizes ? ` — Tailles ${p.sizes.join('/')}` : '';
    let colorNames = [];
    if (p.colorVariants) colorNames = colorNames.concat(p.colorVariants.map(c => c.label || c.color));
    if (p.extraColors) colorNames = colorNames.concat(p.extraColors);
    if (!p.colorVariants && p.colors) colorNames = p.colors;
    const colors = colorNames.length ? ` — Couleurs: ${colorNames.join(', ')}` : '';
    return `- ${p.name}${sizes}${colors} — ${p.price}`;
  }).join('\n');
}

function buildDeliveryText(deliveryZones) {
  return deliveryZones.map(dz => `${dz.zones.join('/')} : ${dz.price} FCFA`).join(', ');
}
function buildSystemPrompt(merchantInfo) {
  return `Tu es l'assistant WhatsApp de ${buildDeliveryText(merchantInfo.deliveryZones)}, boutique de vêtements à Dakar.

RÈGLE ABSOLUE : N'utilise QUE les informations du catalogue ci-dessous. Si un produit, une taille, une couleur, un prix ou un service n'y figure pas, réponds : "Je n'ai pas cette info précise, on vérifie et je reviens vers vous vite !" Ne jamais inventer un prix, une taille, une couleur ou une disponibilité.

PRODUITS :
${buildProductListText(merchantInfo.products)}

LIVRAISON : ${merchantInfo.delivery}
HORAIRES : ${merchantInfo.hours}

GESTION DE L'INTÉRÊT CLIENT :
Quand le client exprime un intérêt ou veut commander, avance directement la conversation :
1. Confirme le produit, la taille/couleur si applicable, et le prix
2. Demande sa zone de livraison
3. Une fois la zone connue, annonce le paiement par Wave : dis au client de 
   payer via l'application Wave, en entrant ce numéro : ${merchantInfo.wavePaymentNumber}
Reste concret et avance vers la commande, ne tourne jamais en rond.

Avant de dire "je n'ai pas cette information", vérifie une dernière fois 
si le produit ou l'info demandée figure dans la liste PRODUITS ci-dessus.

GESTION DES COULEURS/TAILLES INDISPONIBLES :
Propose activement les couleurs/tailles réellement disponibles si celle 
demandée n'existe pas.

GESTION DES PHOTOS :
Quand tu réponds à propos d'un produit à plusieurs couleurs, mentionne 
TOUJOURS le nom complet du produit dans ta phrase, MÊME si le client vient 
de le nommer, ET termine par un marqueur sur sa propre ligne, jamais visible 
au client :
- Couleur précise choisie : [PHOTO: couleur=NomCouleur]
- Toutes les couleurs demandées : [PHOTO: toutes]
- Aucune couleur encore choisie : n'ajoute aucun marqueur
Ne dis JAMAIS que tu n'as pas de photo disponible.

RÈGLE DE SÉCURITÉ VOCALE (TRÈS IMPORTANT) :
Certains messages du client sont précédés de "[MESSAGE VOCAL - transcription 
potentiellement imparfaite]". Cela signifie que la transcription automatique 
peut être déformée, en particulier les NOMBRES (tailles/pointures), qui sont 
souvent mal transcrits (ex: un chiffre comme 42, 43 ou 44 peut ressortir 
comme un mot incohérent).

Si une information critique (taille, produit, ou couleur en cas d'ambiguïté) 
provient UNIQUEMENT d'un message marqué [MESSAGE VOCAL] et n'a jamais été 
confirmée clairement ensuite, tu ne dois JAMAIS :
- l'utiliser pour finaliser une commande (marqueur [COMMANDE:...])
- l'affirmer avec certitude au client comme si elle était sûre

Dans ce cas, demande TOUJOURS une confirmation explicite avant de continuer, 
par exemple : "Pour être sûr, vous voulez bien confirmer la pointure : 42, 
43 ou 44 ?" N'ajoute le marqueur [COMMANDE:...] que lorsque le produit, la 
taille et la couleur (si applicable) ont été confirmés sans ambiguïté, que 
ce soit par écrit ou par une réponse vocale limpide et non ambiguë (par 
exemple si le client répète clairement un seul chiffre en réponse à ta 
question de clarification).

Si tu as précédemment proposé une commande sans ajouter le marqueur 
[COMMANDE:...] (parce que l'information venait d'un message vocal et tu 
attendais une confirmation écrite), et que le client confirme maintenant 
clairement par écrit (ex: "oui", "confirmé", "c'est bon"), tu DOIS 
impérativement ajouter le marqueur [COMMANDE: produit=NOM; zone=ZONE; 
total=MONTANT] dans TA RÉPONSE À CE MESSAGE DE CONFIRMATION — c'est le 
moment précis où la commande doit être transmise au commerçant, pas avant.

RÈGLE DE FIABILITÉ DU MARQUEUR COMMANDE :
Quand tu génères le marqueur [COMMANDE: produit=...; zone=...; total=...], 
la valeur de "zone" DOIT être exactement identique à la zone de livraison 
que tu viens d'utiliser dans ta phrase pour calculer le prix de livraison 
et le total. Ne reformule jamais ni ne change cette zone entre le moment où 
tu l'annonces au client et le moment où tu la mets dans le marqueur — 
recopie-la mot pour mot.

Après une commande confirmée, si le client répond par un message court sans 
nouvelle demande (ex: "Ok", "Merci", "👍"), ne recommence PAS une salutation 
générique comme si la conversation était nouvelle — réponds simplement de 
façon chaleureuse et brève, par exemple "Avec plaisir ! N'hésitez pas si vous 
avez besoin d'autre chose. 🙏"

GESTION DE LA CONFIRMATION DE COMMANDE :
Quand une commande est confirmée (et seulement après avoir respecté la 
RÈGLE DE SÉCURITÉ VOCALE ci-dessus si applicable), termine par :
[COMMANDE: produit=NOM; taille=TAILLE; couleur=COULEUR; zone=ZONE; total=MONTANT]
Utilise "aucune" si la taille ou la couleur ne s'applique pas à ce produit.
Une seule fois par commande.

TON : Chaleureux, direct, mélange naturel français/wolof simple ("Waaw", "am nañu ko", "jërejëf"). Réponses courtes, 2-3 phrases max.`;
}

function findDeliveryZone(deliveryZones, zoneName) {
  if (!zoneName) return null;
  const zoneLower = zoneName.toLowerCase();
  return deliveryZones.find(dz =>
    dz.zones.some(z => z.toLowerCase() === zoneLower)
  );
}

function parsePrice(priceStr) {
  // "18 000 FCFA" -> 18000
  return parseInt(String(priceStr).replace(/[^\d]/g, ''), 10);
}

function validateOrder(state, merchant) {
  const errors = [];

  // 1. Produit confirmé et existant
  if (!state.product.confirmed || !state.product.value) {
    errors.push('produit non confirmé');
  }
  const product = merchant.products.find(p => p.name === state.product.value);
  if (!product) {
    errors.push('produit introuvable dans le catalogue');
  }

  // 2. Taille confirmée et valide pour CE produit
  if (product && product.sizes) {
    if (!state.size.confirmed || !state.size.value) {
      errors.push('taille non confirmée');
    } else if (!product.sizes.includes(state.size.value)) {
      errors.push('taille invalide pour ce produit');
    }
  }

  // 3. Couleur confirmée et valide pour CE produit (si applicable)
  const hasColors = product && (product.colorVariants || product.extraColors || product.colors);
  if (hasColors) {
    let validColors = [];
    if (product.colorVariants) validColors = validColors.concat(product.colorVariants.map(c => c.color));
    if (product.extraColors) validColors = validColors.concat(product.extraColors);
    if (product.colors) validColors = validColors.concat(product.colors);

    if (!state.color.confirmed || !state.color.value) {
      errors.push('couleur non confirmée');
    } else if (!validColors.some(c => c.toLowerCase() === state.color.value.toLowerCase())) {
      errors.push('couleur invalide pour ce produit');
    }
  }

  // 4. Zone confirmée et connue
  if (!state.deliveryZone.confirmed || !state.deliveryZone.value) {
    errors.push('zone de livraison non confirmée');
  }
  const zoneMatch = findDeliveryZone(merchant.deliveryZones, state.deliveryZone.value);
  if (state.deliveryZone.value && !zoneMatch) {
    errors.push('zone de livraison inconnue');
  }

  // 5. Commande pas déjà envoyée
  if (state.orderSent) {
    errors.push('commande déjà transmise');
  }

  if (errors.length > 0) {
    return { valid: false, errors };
  }

  // Calcul du prix, entièrement côté serveur — jamais celui donné par GPT
  const productPrice = parsePrice(product.price);
  const deliveryPrice = zoneMatch.price;
  const total = productPrice + deliveryPrice;

  return {
    valid: true,
    orderDetails: {
      product: product.name,
      size: state.size.value,
      color: state.color.value,
      zone: state.deliveryZone.value,
      total,
    },
  };
}

const MERCHANTS = {
  "1291978550658723": {
    name: "AD Shoes",
    ownerWhatsApp: "33664017511",
    wavePaymentNumber: "221781645041",
    // Pour AD Shoes, remplace :
    // delivery: "Dakar Centre/Plateau/Médina 1 500 FCFA, ..."
    // par :
    deliveryZones: [
      { zones: ["Dakar Centre", "Plateau", "Médina"], price: 1500 },
      { zones: ["Sacré-Cœur", "Mermoz", "Ouakam", "Yoff", "Maristes"], price: 1500 },
      { zones: ["Parcelles Assainies", "Guédiawaye", "Pikine"], price: 2000 },
      { zones: ["Rufisque", "Bargny"], price: 3000 },
    ],
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
          { color: "Noir", label: "Noir (Goudi)", imageUrl: "https://noxa-whatsapp.vercel.app/images/ndakarou-noir.jpg" },
          { color: "Marron", label: "Marron (Bant)", imageUrl: "https://noxa-whatsapp.vercel.app/images/ndakarou-marron.jpg" },
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
          { color: "Bleu", label: "Bleu (Modou LO)", imageUrl: "https://noxa-whatsapp.vercel.app/images/taggat-bleu.jpg" },
          { color: "Rouge/Noir", label: "Rouge/Noir (Balla GAYE 2)", imageUrl: "https://noxa-whatsapp.vercel.app/images/taggat-rouge.jpg" },
        ],
        extraColors: ["Noir"],
        price: "20 000 FCFA",
      },
    ],
  },
  "1321520757705474": {
    name: "Boutique test (numéro Sénégal)",
    ownerWhatsApp: "221781178874",
    wavePaymentNumber: "221781178874",
    delivery: "Dakar centre 1 500 FCFA, banlieue 2 500 FCFA, gratuite dès 30 000 FCFA",
    hours: "Lundi-Samedi 9h-19h, fermé dimanche",
    products: [
      { name: "Robe Wax imprimée", sizes: ["S","M","L"], colorVariants: [{ color: "Bleu", imageUrl: "https://noxa-whatsapp.vercel.app/images/robe-bleu.jpg" },{ color: "Rouge", imageUrl: "https://noxa-whatsapp.vercel.app/images/robe-rouge-v2.jpg" },{ color: "Noir", imageUrl: "https://noxa-whatsapp.vercel.app/images/robe-noir.jpg" }], price: "15 000 FCFA" },
      { name: "Ensemble Boubou homme", sizes: ["M","L","XL"], colorVariants: [{ color: "Noir", imageUrl: "https://noxa-whatsapp.vercel.app/images/ensemble-noir.jpg" },{ color: "Bleu", imageUrl: "https://noxa-whatsapp.vercel.app/images/ensemble-bleu.jpg" }], price: "25 000 FCFA" },
      { name: "Chemise brodée", sizes: ["S","M","L","XL"], colors: ["Blanc"], price: "12 000 FCFA", imageUrl: "https://noxa-whatsapp.vercel.app/images/chemise.jpg" },
      { name: "Sac à main tissu wax", sizes: ["Taille unique"], colors: ["Multicolore"], price: "8 000 FCFA", imageUrl: "https://noxa-whatsapp.vercel.app/images/sac.jpg" },
      { name: "Turban assorti", sizes: ["Taille unique"], colors: ["Vert"], price: "3 000 FCFA", imageUrl: "https://noxa-whatsapp.vercel.app/images/turban.jpg" },
    ],
  },
};

Object.values(MERCHANTS).forEach(m => { m.systemPrompt = buildSystemPrompt(m); });

const conversationHistory = {};
// Remplace les deux anciennes constantes par celle-ci
const conversationState = {};

function getConversationState(conversationKey) {
  if (!conversationState[conversationKey]) {
    conversationState[conversationKey] = {
      product: { value: null, source: null, confirmed: false },
      size: { value: null, source: null, confirmed: false },
      color: { value: null, source: null, confirmed: false },
      deliveryZone: { value: null, source: null, confirmed: false },
      orderSent: false,
    };
  }
  return conversationState[conversationKey];
}

// Met à jour un champ, en respectant une règle simple :
// - une info venant du texte écrit est directement confirmée
// - une info venant du vocal reste NON confirmée, sauf si elle confirme
//   une valeur déjà confirmée précédemment (le client répète la même chose)
function setStateField(state, field, value, isVoice) {
  const current = state[field];
  const source = isVoice ? 'voice' : 'text';

  if (!isVoice) {
    state[field] = { value, source, confirmed: true };
    return;
  }

  // Message vocal : ne confirme que si ça correspond à une valeur déjà confirmée
  if (current.confirmed && current.value === value) {
    state[field] = { value, source: 'voice_reconfirmed', confirmed: true };
  } else {
    state[field] = { value, source: 'voice', confirmed: false };
  }
}

async function sendWhatsAppText(phoneNumberId, to, body) {
  await fetch(`https://graph.facebook.com/v21.0/${phoneNumberId}/messages`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${process.env.WHATSAPP_ACCESS_TOKEN}` },
    body: JSON.stringify({ messaging_product: 'whatsapp', to, text: { body } }),
  });
}

async function sendWhatsAppImage(phoneNumberId, to, imageUrl, caption) {
  const response = await fetch(`https://graph.facebook.com/v21.0/${phoneNumberId}/messages`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${process.env.WHATSAPP_ACCESS_TOKEN}` },
    body: JSON.stringify({ messaging_product: 'whatsapp', to, type: 'image', image: { link: imageUrl, caption: caption || '' } }),
  });
  const data = await response.json();
  console.log('DEBUG - Envoi image:', imageUrl, JSON.stringify(data));
}

async function transcribeWhatsAppAudio(mediaId) {
  const mediaInfoResponse = await fetch(`https://graph.facebook.com/v21.0/${mediaId}`, { headers: { 'Authorization': `Bearer ${process.env.WHATSAPP_ACCESS_TOKEN}` } });
  const mediaInfo = await mediaInfoResponse.json();
  const audioResponse = await fetch(mediaInfo.url, { headers: { 'Authorization': `Bearer ${process.env.WHATSAPP_ACCESS_TOKEN}` } });
  const audioBuffer = await audioResponse.arrayBuffer();
  const formData = new FormData();
  formData.append('file', new Blob([audioBuffer], { type: 'audio/ogg' }), 'audio.ogg');
  formData.append('model', 'whisper-1');
  formData.append('language', 'fr');
  const transcriptionResponse = await fetch('https://api.openai.com/v1/audio/transcriptions', {
    method: 'POST', headers: { 'Authorization': `Bearer ${process.env.OPENAI_API_KEY}` }, body: formData,
  });
  const transcriptionData = await transcriptionResponse.json();
  return transcriptionData.text || null;
}

// Espace les nombres (téléphone, prix) pour que le TTS les lise chiffre par
// chiffre / en petits groupes, plutôt que comme un seul très grand nombre.
function formatNumbersForSpeech(text) {
  return text.replace(/\b\d{5,}\b/g, (match) => {
    // Découpe en groupes de 2-3 chiffres, garde les 3 premiers comme indicatif
    if (match.length >= 9) {
      const country = match.slice(0, 3);
      const rest = match.slice(3);
      const groups = rest.match(/.{1,2}/g) || [];
      return `${country} ${groups.join(' ')}`;
    }
    // Pour les prix/totaux plus courts, simple espacement par milliers
    return match.replace(/(\d)(?=(\d{3})+(?!\d))/g, '$1 ');
  });
}

async function generateAndSendVoiceReply(phoneNumberId, to, text) {
  try {
    const speechText = formatNumbersForSpeech(text);

    // 1. Génère l'audio avec OpenAI TTS
    const ttsResponse = await fetch('https://api.openai.com/v1/audio/speech', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${process.env.OPENAI_API_KEY}`,
      },
      body: JSON.stringify({
        model: 'tts-1',
        voice: 'nova',
        input: speechText,
      }),
    });
    const audioBuffer = await ttsResponse.arrayBuffer();

    // 2. Upload l'audio vers l'API Media de WhatsApp
    const formData = new FormData();
    formData.append('file', new Blob([audioBuffer], { type: 'audio/mpeg' }), 'reply.mp3');
    formData.append('messaging_product', 'whatsapp');

    const uploadResponse = await fetch(
      `https://graph.facebook.com/v21.0/${phoneNumberId}/media`,
      {
        method: 'POST',
        headers: { 'Authorization': `Bearer ${process.env.WHATSAPP_ACCESS_TOKEN}` },
        body: formData,
      }
    );
    const uploadData = await uploadResponse.json();
    const mediaId = uploadData.id;

    if (!mediaId) {
      console.error('DEBUG - Échec upload média TTS:', JSON.stringify(uploadData));
      return;
    }

    // 3. Envoie le message vocal en référençant ce media_id
    await fetch(`https://graph.facebook.com/v21.0/${phoneNumberId}/messages`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${process.env.WHATSAPP_ACCESS_TOKEN}`,
      },
      body: JSON.stringify({
        messaging_product: 'whatsapp',
        to,
        type: 'audio',
        audio: { id: mediaId },
      }),
    });
  } catch (err) {
    console.error('Erreur génération/envoi TTS:', err);
    // Pas grave si ça échoue : le texte est déjà parti, le client a sa réponse
  }
}

export default async function handler(req, res) {
  if (req.method === 'GET') {
    const VERIFY_TOKEN = process.env.WHATSAPP_VERIFY_TOKEN;
    if (req.query['hub.mode'] === 'subscribe' && req.query['hub.verify_token'] === VERIFY_TOKEN) {
      return res.status(200).send(req.query['hub.challenge']);
    }
    return res.status(403).send('Verification failed');
  }

  if (req.method === 'POST') {
    try {
      const body = req.body;
      const change = body.entry?.[0]?.changes?.[0];
      const message = change?.value?.messages?.[0];
      const phoneNumberId = change?.value?.metadata?.phone_number_id;

      if (!message) return res.status(200).send('OK');
      const from = message.from;

      if (['image', 'sticker', 'location', 'document'].includes(message.type)) {
        await sendWhatsAppText(phoneNumberId, from, "Je ne peux pas encore traiter ce type de message. Dites-moi en quelques mots ce qui vous intéresse ! 😊");
        return res.status(200).send('OK');
      }

      let text = message.text?.body;
      let wasVoiceMessage = false;

      if (message.type === 'audio' && message.audio?.id) {
        text = await transcribeWhatsAppAudio(message.audio.id);
        if (!text) {
          await sendWhatsAppText(phoneNumberId, from, "Je n'ai pas bien compris votre message vocal, pouvez-vous réessayer ou l'écrire en texte ?");
          return res.status(200).send('OK');
        }
        wasVoiceMessage = true;
        // Marque ce message comme vocal pour que GPT applique la règle de sécurité
        text = `[MESSAGE VOCAL - transcription potentiellement imparfaite] ${text}`;
      }
      if (!text) return res.status(200).send('OK');

      const merchant = MERCHANTS[phoneNumberId];
      if (!merchant) return res.status(200).send('OK');

      const conversationKey = `${phoneNumberId}_${from}`;
      const state = getConversationState(conversationKey);
      if (!conversationHistory[conversationKey]) conversationHistory[conversationKey] = [];
      conversationHistory[conversationKey].push({ role: 'user', content: text });
      const recentHistory = conversationHistory[conversationKey].slice(-10);

      let aiData;
      try {
        const aiResponse = await fetch('https://api.openai.com/v1/chat/completions', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${process.env.OPENAI_API_KEY}` },
          body: JSON.stringify({ model: 'gpt-4o', messages: [{ role: 'system', content: merchant.systemPrompt }, ...recentHistory] }),
        });
        aiData = await aiResponse.json();
      } catch (err) {
        console.error('Erreur appel OpenAI:', err);
        await sendWhatsAppText(phoneNumberId, from, "Petit souci technique de notre côté, on revient vers vous très vite !");
        return res.status(200).send('OK');
      }

      const replyText = aiData.choices?.[0]?.message?.content || "Désolé, je n'ai pas pu traiter votre message.";
      conversationHistory[conversationKey].push({ role: 'assistant', content: replyText });

      const photoMatch = replyText.match(/\[PHOTO:\s*(.+?)\]/);
      const orderMatch = replyText.match(/\[COMMANDE:\s*(.+?)\]/);

      const finalClientText = replyText.replace(/\[PHOTO:.+?\]/, '').replace(/\[COMMANDE:.+?\]/, '').trim();
      if (finalClientText) {
        await sendWhatsAppText(phoneNumberId, from, finalClientText);

        // Si le client a écrit en vocal, on répond aussi en vocal (en plus du texte)
        if (wasVoiceMessage) {
          await generateAndSendVoiceReply(phoneNumberId, from, finalClientText);
        }
      }

      // Recherche du produit : uniquement le tour actuel (pas tout l'historique),
      // avec la mémoire lastProductPerConversation comme filet de sécurité pour
      // les références implicites ("celui-là") — évite de renvoyer la photo à
      // chaque tour suivant sans raison.
      // Recherche du produit : remplace l'ancien mentionedProduct/lastProductPerConversation
      const searchText = [text, finalClientText].join(' ').toLowerCase();
      let mentionedProduct = merchant.products.find(p => searchText.includes(p.name.toLowerCase()));

      if (mentionedProduct) {
        setStateField(state, 'product', mentionedProduct.name, wasVoiceMessage);
      } else if (state.product.value) {
        mentionedProduct = merchant.products.find(p => p.name === state.product.value);
      }

      if (mentionedProduct && mentionedProduct.sizes) {
        const sizeMatch = mentionedProduct.sizes.find(s => {
          const regex = new RegExp(`\\b${s}\\b`);
          return regex.test(text);
        });
        if (sizeMatch) {
          setStateField(state, 'size', sizeMatch, wasVoiceMessage);
        }
      }

      if (mentionedProduct) {
        if (mentionedProduct.colorVariants) {
          let instruction = photoMatch ? photoMatch[1].trim() : null;

          if (!instruction) {
            const clientTextLower = text.toLowerCase();
            const clientColor = mentionedProduct.colorVariants.find(c => {
              const colorLower = c.color.toLowerCase();
              const labelLower = (c.label || c.color).toLowerCase();
              return clientTextLower.includes(colorLower) || clientTextLower.includes(labelLower);
            });
            if (clientColor) {
              setStateField(state, 'color', clientColor.color, wasVoiceMessage);
            }
          }
          if (merchant.deliveryZones) {
            const zoneMatch = findDeliveryZone(merchant.deliveryZones, text);
            // findDeliveryZone cherche une correspondance exacte ; pour capter la zone
            // dans une phrase libre, on cherche plutôt si un nom de zone connu apparaît
            // dans le texte du client
            const clientTextLower = text.toLowerCase();
            let foundZone = null;
            for (const dz of merchant.deliveryZones) {
              const match = dz.zones.find(z => clientTextLower.includes(z.toLowerCase()));
              if (match) {
                foundZone = match;
                break;
              }
            }
            if (foundZone) {
              setStateField(state, 'deliveryZone', foundZone, wasVoiceMessage);
            }
          } 

          if (instruction === 'toutes') {
            for (const variant of mentionedProduct.colorVariants) {
              await sendWhatsAppImage(
                phoneNumberId,
                from,
                variant.imageUrl,
                `${mentionedProduct.name} (${variant.label || variant.color}) — ${mentionedProduct.price}`
              );
            }
          } else if (instruction && instruction.startsWith('couleur=')) {
            const colorName = instruction.replace('couleur=', '').trim();
            const matchedColor = mentionedProduct.colorVariants.find(c =>
              c.color.toLowerCase() === colorName.toLowerCase()
            );
            if (matchedColor) {
              await sendWhatsAppImage(
                phoneNumberId,
                from,
                matchedColor.imageUrl,
                `${mentionedProduct.name} (${matchedColor.label || matchedColor.color}) — ${mentionedProduct.price}`
              );
            }
          }
        } else {
          await sendWhatsAppImage(phoneNumberId, from, mentionedProduct.imageUrl, `${mentionedProduct.name} — ${mentionedProduct.price}`);
        }
      }

      // Notification de commande, avec garde-fou de sécurité vocale.
      if (orderMatch && merchant.ownerWhatsApp) {
        if (wasVoiceMessage) {
          console.log('DEBUG - [COMMANDE] détecté sur un message vocal non confirmé par écrit : notification bloquée par sécurité.');
        } else {
          const validation = validateOrder(state, merchant);

          if (validation.valid) {
            const d = validation.orderDetails;
            await fetch(`https://graph.facebook.com/v21.0/${phoneNumberId}/messages`, {
              method: 'POST',
              headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${process.env.WHATSAPP_ACCESS_TOKEN}` },
              body: JSON.stringify({
                messaging_product: 'whatsapp',
                to: merchant.ownerWhatsApp,
                text: { body: `🛍️ Nouvelle commande !\nProduit: ${d.product}\nTaille: ${d.size}\nCouleur: ${d.color}\nZone: ${d.zone}\nTotal: ${d.total} FCFA\nClient: ${from}\n\nVérifiez votre app Wave pour confirmer la réception du paiement avant de valider.` },
              }),
            });
            state.orderSent = true;
          } else {
            console.log('DEBUG - Commande refusée par validateOrder:', validation.errors.join(', '));
            // On ne notifie PAS le commerçant, et on informe le client qu'il manque
            // encore une confirmation, plutôt que de le laisser croire que c'est réglé
            await sendWhatsAppText(
              phoneNumberId,
              from,
              "Il me manque encore une information pour finaliser votre commande — pouvez-vous confirmer le produit, la taille, la couleur et votre zone de livraison ? 🙏"
            );
          }
        }
      }

      return res.status(200).send('OK');
    } catch (error) {
      console.error('Erreur webhook:', error);
      return res.status(200).send('OK');
    }
  }

  return res.status(405).send('Method Not Allowed');
}