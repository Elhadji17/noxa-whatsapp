// api/whatsapp-webhook.js
import { Redis } from '@upstash/redis';
import { waitUntil } from '@vercel/functions';
import crypto from 'crypto';

// ---------------------------------------------------------------------------
// REDIS — connexion
// ---------------------------------------------------------------------------
// Redis.fromEnv() lit UPSTASH_REDIS_REST_URL / UPSTASH_REDIS_REST_TOKEN.
// Si ton intégration Vercel a injecté KV_REST_API_URL / KV_REST_API_TOKEN
// (nom historique conservé pour compatibilité), on les mappe explicitement —
// vérifie dans ton dashboard Vercel > Storage > (ta base) > .env.local
// quelles variables sont réellement présentes, et ajuste si besoin.
const redis = new Redis({
  url: process.env.UPSTASH_REDIS_REST_URL || process.env.KV_REST_API_URL,
  token: process.env.UPSTASH_REDIS_REST_TOKEN || process.env.KV_REST_API_TOKEN,
});

const STATE_TTL_SECONDS = 60 * 60 * 24 * 3;   // 3 jours d'inactivité = expiration
const DONE_TTL_SECONDS = 60 * 60 * 24;        // 24h pour la protection anti-doublon
// 120s : le fichier compte 10 appels externes chronométrés (timeout 20s
// chacun) — un message vocal avec photo peut chaîner transcription + GPT +
// TTS + upload + envoi + photo + notification. 45s n'était pas une garantie
// suffisante contre ce pire cas réaliste.
const LOCK_TTL_SECONDS = 180;
const LOCK_MAX_RETRIES = 6;
const LOCK_RETRY_DELAY_MS = 700;
const QUEUE_TTL_SECONDS = 60 * 60; // 1h — juste une protection contre les vieux messages, pas une file volontairement courte

// ---------------------------------------------------------------------------
// VERROU PAR CONVERSATION — avec token de propriétaire, libération conditionnelle
// ---------------------------------------------------------------------------
// Empêche deux messages rapprochés de la même conversation de se marcher
// dessus (lire le même état avant que l'autre ait sauvegardé le sien).
// Le token empêche qu'une requête en retard supprime le verrou d'une autre
// requête qui l'a légitimement acquis après expiration du sien.

const UNLOCK_SCRIPT = `
if redis.call("get", KEYS[1]) == ARGV[1] then
  return redis.call("del", KEYS[1])
else
  return 0
end
`;

function sleep(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}

async function acquireLock(conversationKey) {
  const lockKey = `lock:${conversationKey}`;
  const token = crypto.randomUUID();

  console.log(`DEBUG - LOCK TRY ${conversationKey}`);
  for (let attempt = 0; attempt < LOCK_MAX_RETRIES; attempt++) {
    const acquired = await redis.set(lockKey, token, { nx: true, ex: LOCK_TTL_SECONDS });
    if (acquired) {
      console.log(`DEBUG - LOCK ACQUIRED ${conversationKey} (tentative ${attempt + 1})`);
      return token;
    }
    await sleep(LOCK_RETRY_DELAY_MS);
  }
  console.log(`DEBUG - LOCK BUSY ${conversationKey} après ${LOCK_MAX_RETRIES} tentatives — mise en file`);
  return null;
}

async function releaseLock(conversationKey, token) {
  if (!token) return;
  const lockKey = `lock:${conversationKey}`;
  try {
    await redis.eval(UNLOCK_SCRIPT, [lockKey], [token]);
    console.log(`DEBUG - LOCK RELEASED ${conversationKey}`);
  } catch (err) {
    console.error('DEBUG - Erreur libération du verrou:', err);
  }
}

// ---------------------------------------------------------------------------
// FILE D'ATTENTE PAR CONVERSATION — pour ne jamais perdre un message
// ---------------------------------------------------------------------------
// Si le verrou est occupé, on ne renonce plus au message : on le pousse dans
// une liste Redis dédiée à cette conversation. Celui qui détient actuellement
// le verrou la videra (dans l'ordre d'arrivée) juste avant de le relâcher.
// Important : WhatsApp considère un message comme livré dès qu'on répond 200
// à son webhook — un message qu'on abandonnerait ici serait donc perdu pour
// de bon, pas juste "en attente" côté Meta. D'où l'importance de ne jamais
// répondre 200 sans avoir soit traité, soit mis en file le message.

function queueKey(conversationKey) {
  return `pending:${conversationKey}`;
}

async function pushPendingMessage(conversationKey, item) {
  await redis.rpush(queueKey(conversationKey), item);
  await redis.expire(queueKey(conversationKey), QUEUE_TTL_SECONDS);
}

// Remet un message en tête de file — utilisé quand son traitement a échoué,
// pour qu'il soit retenté (par un futur drain) plutôt que perdu. Il a été
// retiré par LPOP avant son traitement ; sans ce filet, un échec en cours de
// route le ferait disparaître pour de bon (voir le commentaire au point
// d'appel).
async function requeueFront(conversationKey, item) {
  await redis.lpush(queueKey(conversationKey), item);
  await redis.expire(queueKey(conversationKey), QUEUE_TTL_SECONDS);
}

async function popPendingMessage(conversationKey) {
  return await redis.lpop(queueKey(conversationKey));
}

// ---------------------------------------------------------------------------
// IDEMPOTENCE — un message WhatsApp donné n'est traité qu'une fois
// ---------------------------------------------------------------------------
// On marque un message comme "done" seulement APRÈS un traitement complet
// réussi (réponse envoyée + commande éventuellement transmise), jamais avant.
// Si le traitement plante en cours de route, le message n'est pas marqué
// "done" : un retry WhatsApp le retraitera. C'est volontaire — mieux vaut
// une réponse en double (gênant) qu'un message ou une commande perdue
// (grave). Le verrou de conversation limite le risque de double-traitement
// simultané pour ce même message.

async function isMessageAlreadyProcessed(messageId) {
  if (!messageId) return false;
  const done = await redis.get(`done:${messageId}`);
  return Boolean(done);
}

async function markMessageProcessed(messageId) {
  if (!messageId) return;
  await redis.set(`done:${messageId}`, '1', { ex: DONE_TTL_SECONDS });
}

// ---------------------------------------------------------------------------
// STATE & HISTORY — chargement/sauvegarde
// ---------------------------------------------------------------------------

function emptyField() {
  return { value: null, source: null, confirmed: false };
}

function emptyState() {
  return {
    product: emptyField(),
    size: emptyField(),
    color: emptyField(),
    deliveryZone: emptyField(),
    orderSent: false,
    pendingProposals: null,
    lastActivityAt: null,       // ms epoch — dernière interaction sur cette commande
    awaitingSessionChoice: false, // true tant qu'on attend la réponse "continuer ou repartir de zéro ?"
  };
}

async function loadState(conversationKey) {
  const stored = await redis.get(`state:${conversationKey}`);
  return stored || emptyState();
}

async function saveState(conversationKey, state) {
  await redis.set(`state:${conversationKey}`, state, { ex: STATE_TTL_SECONDS });
}

async function loadHistory(conversationKey) {
  const stored = await redis.get(`history:${conversationKey}`);
  return stored || [];
}

async function saveHistory(conversationKey, history) {
  await redis.set(`history:${conversationKey}`, history.slice(-10), { ex: STATE_TTL_SECONDS });
}

// ---------------------------------------------------------------------------
// PROMPT / CATALOGUE (inchangé)
// ---------------------------------------------------------------------------

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
  return `Tu es l'assistant WhatsApp de ${merchantInfo.name}, boutique de vêtements à Dakar.

RÈGLE ABSOLUE : N'utilise QUE les informations du catalogue ci-dessous. Si un produit, une taille, une couleur, un prix ou un service n'y figure pas, réponds : "Je n'ai pas cette info précise, on vérifie et je reviens vers vous vite !" Ne jamais inventer un prix, une taille, une couleur ou une disponibilité.

PRODUITS :
${buildProductListText(merchantInfo.products)}

LIVRAISON : ${buildDeliveryText(merchantInfo.deliveryZones)}
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
Utilise toujours le nom de couleur SANS le surnom entre parenthèses dans ce 
marqueur (ex: "couleur=Bleu", pas "couleur=Bleu (Modou LO)").

GESTION DES PROPOSITIONS (confirmation implicite) :
Quand tu poses une question de confirmation portant sur une ou plusieurs 
valeurs précises que TU proposes toi-même (ex: "c'est bien la taille 42 ?", 
"Bleu (Modou LO), c'est bien ça ?", "vous confirmez Médina ?", ou même le 
PRODUIT si tu viens de le proposer/deviner), termine ta réponse par un 
marqueur sur sa propre ligne, jamais visible au client :
[PROPOSITION: champ=valeur; champ2=valeur2]
Où "champ" est l'un de : produit, taille, couleur, zone.
Pour "produit", utilise le nom EXACT et complet tel qu'il figure dans le 
catalogue ci-dessus.
Utilise le nom de couleur SANS le surnom entre parenthèses (ex: "couleur=Bleu").
Utilise la zone exactement comme listée dans LIVRAISON ci-dessus.
N'ajoute ce marqueur QUE quand tu poses une vraie question fermée (oui/non) 
portant sur une valeur précise — jamais dans une réponse purement informative.
Si le client répond ensuite par une courte confirmation ("oui", "ok", etc.) 
sans répéter la valeur, le système la validera automatiquement grâce à ce 
marqueur — tu n'as rien d'autre à faire.
C'est le SEUL moyen pour qu'une confirmation implicite ("oui" tout seul) 
valide une donnée : le système ne réutilise jamais un ancien message ni ta 
propre réponse pour deviner ce qu'un "oui" confirme.

GESTION D'UN CLIENT QUI NE COMPREND PAS LE FRANÇAIS :
Si le client indique, en wolof ou autrement, qu'il ne comprend pas le 
français (ex: "deggouma français", "waxal wolof", "je ne comprends pas le français"), NE dis JAMAIS simplement que tu ne parles que français sans 
autre solution — ça laisse le client dans une impasse. À la place :
1. Réponds en une phrase très courte, dans le registre wolof urbain défini 
   plus bas (TON), pour rassurer que la demande est prise en compte.
2. Continue d'utiliser les chiffres (prix, tailles) et les photos produit, 
   qui se comprennent sans traduction.
3. Termine ta réponse par ce marqueur sur sa propre ligne, jamais visible 
   au client :
[BESOIN_WOLOF]
Ce marqueur prévient automatiquement le commerçant, qui parle wolof, pour 
qu'il puisse prendre le relais directement avec le client si besoin. 
N'ajoute ce marqueur qu'une seule fois par blocage linguistique constaté, 
pas à chaque message si le client continue malgré tout en français ensuite.

IMPORTANT — ce marqueur est réservé à un blocage EXPLICITE : le client dit 
lui-même qu'il ne comprend pas le français, ou demande qu'on lui parle 
wolof. Ne l'ajoute JAMAIS pour : une simple salutation ("salamalekoum", 
"nanga def"...), un message écrit en wolof ou mêlant wolof et français, une 
réponse courte ("waaw", "oui"), ou une question sur un produit. Écrire en 
wolof ne signifie PAS qu'un client ne comprend pas le français : beaucoup de 
clients écrivent en wolof tout en comprenant très bien les réponses en 
français.

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
43 ou 44 ?" (avec le marqueur [PROPOSITION:...] correspondant). Toute donnée 
critique confirmée après un message vocal doit systématiquement passer par 
une confirmation écrite explicite — un vocal, même limpide, ne suffit jamais 
seul à confirmer une commande.

Parfois, un message vocal est suivi d'une ligne commençant par
« [AUTRE TRANSCRIPTION AUTOMATIQUE du même vocal, par un second système] ».
Ce sont deux transcriptions du MÊME message, faites par deux systèmes
différents qui se trompent à des endroits différents : aucune n'est présumée
plus fiable. Croise-les pour comprendre ce que le client a voulu dire (un mot
déformé dans l'une est parfois correct dans l'autre). Si elles divergent sur
une information critique (produit, taille, couleur, prix), ou si tu hésites,
demande une confirmation explicite : la règle ci-dessus ne change pas, un
vocal ne suffit jamais à confirmer une commande.

IMPORTANT : tant qu'une information critique n'est pas confirmée, ne dis 
JAMAIS "je transmets votre commande" ni rien de similaire qui suggère que 
c'est déjà fait. Dis plutôt clairement que tu as besoin d'une confirmation, 
par exemple : "Pouvez-vous confirmer pour que je transmette votre commande ?"

Si tu as précédemment proposé une commande sans ajouter le marqueur 
[COMMANDE:...], et que le client confirme maintenant clairement par écrit 
(ex: "oui", "ok", "okay", "confirmé", "c'est bon", "d'accord"), tu DOIS 
impérativement ajouter le marqueur [COMMANDE: produit=NOM; taille=TAILLE; 
couleur=COULEUR; zone=ZONE; total=MONTANT] dans TA RÉPONSE À CE MESSAGE DE 
CONFIRMATION — c'est le moment précis où la commande doit être transmise 
au commerçant, pas avant.

IMPORTANT : le marqueur [COMMANDE:...] n'est qu'une PROPOSITION. Le système 
vérifie automatiquement chaque donnée avant de transmettre quoi que ce soit 
au commerçant — ne dis donc jamais au client que sa commande est "enregistrée" 
ou "validée" tant que tu n'as pas de certitude complète sur produit, taille, 
couleur et zone. Utilise plutôt une formule comme "C'est noté, je transmets 
votre commande !"

RÈGLE DE FIABILITÉ DU MARQUEUR COMMANDE :
Quand tu génères le marqueur [COMMANDE:...], la valeur de "zone" DOIT être 
exactement identique à la zone de livraison que tu viens d'utiliser dans ta 
phrase pour calculer le prix de livraison et le total. Ne reformule jamais 
ni ne change cette zone entre le moment où tu l'annonces au client et le 
moment où tu la mets dans le marqueur — recopie-la mot pour mot.

Si le client change d'avis sur le produit en cours de conversation, sache 
que le système réinitialise automatiquement la taille et la couleur 
précédemment retenues — redemande-les si besoin, ne suppose jamais qu'elles 
s'appliquent encore au nouveau produit.

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

TON — WOLOF URBAIN DE DAKAR MÊLÉ DE FRANÇAIS (comme sur WhatsApp) :
Tu écris comme un vendeur de Dakar qui répond à un client sur WhatsApp : un
mélange naturel de wolof et de français, tel qu'on le parle dans la rue —
jamais comme dans un livre, un journal ou un cours.

RÈGLES :
1. Court : 1 à 2 phrases, 3 au maximum. Va droit au but.
2. Mélange comme les clients. Garde en FRANÇAIS les mots que les clients disent
   eux-mêmes en français : noms de produits, couleurs, tailles, prix,
   "disponible", "livraison", "commande", "Wave", "confirmer", "valider",
   "réserver", "promotion"... Le wolof sert de liant et de ton (oui, non, il y
   en a, je vois, d'accord...).
3. Registre courant, jamais soutenu. Pas de wolof "littéraire" ni "académique",
   pas de tournures recherchées. Si tu hésites entre deux formulations, prends
   la plus courte et la plus courante. Si tu n'es pas sûr d'une tournure wolof,
   écris plutôt cette partie en français : mieux vaut un français simple qu'un
   wolof faux ou pompeux.
4. Suis le client. S'il écrit surtout en français, réponds surtout en français
   avec quelques touches de wolof ; s'il écrit surtout en wolof, réponds surtout
   en wolof (avec les mots français habituels ci-dessus) ; s'il mélange,
   mélange.
5. Orthographe : écris comme dans les exemples (phonétique, style WhatsApp),
   sans chercher l'orthographe officielle.
6. Chiffres : toujours en chiffres (prix, tailles, quantités), jamais en lettres.
7. Faits : uniquement ceux du catalogue. N'invente jamais de promotion, de
   réduction, de garantie, de mention "original" ou "copie", de matière, ni de
   disponibilité hors des tailles et couleurs listées — même si le client
   insiste, même si un exemple ci-dessous en contient. Si l'information manque :
   dis que tu vérifies et que tu reviens vite.
8. Les marqueurs [PHOTO], [PROPOSITION] et [COMMANDE] restent obligatoires
   selon les règles plus haut, même dans une réponse mêlant wolof et français.
   Les exemples les omettent pour rester lisibles.

EXEMPLES — ils montrent UNIQUEMENT le TON. Leurs produits, matières et
disponibilités sont fictifs : ne les reprends jamais, tes faits viennent du
catalogue ci-dessus.

Client : Bonjour, ma ngi woté pour le modèle de chaussure bi ci story bi.
Réponse : Ah d'accord, guiss nako. Amna kay.

Client : Est-ce que dispo na ci couleur marron wala noir uniquement la ?
Réponse : Si si si, niari couleurs yeup amna.

Client : Salam, am ngeen ci pantalon yi ?
Réponse : Waw am nagnou pantalon coton yi kay.

Client : Dédet pantalon en jean bi ma la wax, pas bi ci wet bi dé !
Réponse : Ah d'accord, guiss na bingay wakh, moy jean noir bi.

Client : Mani, pantalon jean bleu bi, est-ce que am na taille XL ?
Réponse : Guiss nako, am nagnou ssi taille L kay

Client : Non, wakhouma L, XL laa wax, mag la té tollu ci 44.
Réponse : Comprendre na, pantalon jean bleu taille XL.

Client : Ah waaw, loolu laa bëggon wax ! Am na ?
Réponse : Waaw, am na kay.

Reste chaleureux, direct, phrases courtes (2-3 phrases max).`;
}

function normalizeText(value) {
  return String(value || '')
    .normalize('NFD')
    .replace(/[\u0300-\u036f]/g, '')
    .toLowerCase()
    .replace(/['']/g, "'")
    .replace(/[-/]/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();
}

function productWordSet(product) {
  const nameWords = normalizeText(product.name).split(' ').filter(w => w.length >= 3);
  const aliasWords = (product.aliases || []).flatMap(a =>
    normalizeText(a).split(' ').filter(w => w.length >= 3)
  );
  return [...new Set([...nameWords, ...aliasWords])];
}

// Détermine, à partir du catalogue réel du commerçant, quels mots sont
// "partagés" entre plusieurs produits (ex: "cuir", "chaussure") — ceux-là ne
// peuvent jamais suffire seuls à identifier un produit. Un mot qui n'apparaît
// que dans un seul produit du catalogue (ex: "sandale" s'il n'y en a qu'une)
// est au contraire pleinement distinctif. Calculé dynamiquement plutôt
// qu'avec une liste codée en dur, pour rester correct quel que soit le
// catalogue d'un commerçant donné.
function buildProductWordIndex(products) {
  const perProduct = products.map(p => ({ product: p, words: productWordSet(p) }));
  const wordProductCount = new Map();
  for (const { words } of perProduct) {
    for (const w of new Set(words)) {
      wordProductCount.set(w, (wordProductCount.get(w) || 0) + 1);
    }
  }
  return { perProduct, wordProductCount };
}

function escapeRegExp(s) {
  return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

// Vérifie qu'un mot du catalogue apparaît comme mot entier dans le texte
// client, pas comme simple sous-chaîne. Sans ça, "dakar" matcherait à
// l'intérieur de "ndakarou" (ils partagent ces lettres), créant une fausse
// ambiguïté entre deux produits qui n'ont en réalité rien en commun.
function containsWord(normalizedText, word) {
  return new RegExp(`\\b${escapeRegExp(word)}\\b`).test(normalizedText);
}

// Calcule un score de correspondance entre un produit et le texte du client :
// - un mot présent dans un seul produit du catalogue (distinctif) vaut 2 points
// - un mot partagé par plusieurs produits vaut seulement 1 point d'appoint
// Un produit dont SEULS des mots partagés matchent obtient un score de 0 —
// un mot ambigu ne peut jamais, seul, désigner un produit.
function productMatchScore(words, wordProductCount, normalizedText) {
  let distinctiveHits = 0;
  let sharedHits = 0;

  for (const w of words) {
    if (!containsWord(normalizedText, w)) continue;
    if ((wordProductCount.get(w) || 1) > 1) {
      sharedHits += 1;
    } else {
      distinctiveHits += 1;
    }
  }

  if (distinctiveHits === 0) return 0;
  return distinctiveHits * 2 + sharedHits;
}

// Trouve le produit le plus probable dans le texte du client.
// - Si un seul produit a le meilleur score (> 0), il est retourné.
// - Si plusieurs produits sont à égalité sur le meilleur score, on ne
//   choisit PAS au hasard : on retourne un résultat "ambigu" pour que
//   l'appelant demande une clarification plutôt que de deviner.
function findMentionedProduct(products, normalizedText) {
  const { perProduct, wordProductCount } = buildProductWordIndex(products);

  let bestScore = 0;
  let bestProducts = [];

  for (const { product, words } of perProduct) {
    const score = productMatchScore(words, wordProductCount, normalizedText);
    if (score === 0) continue;
    if (score > bestScore) {
      bestScore = score;
      bestProducts = [product];
    } else if (score === bestScore) {
      bestProducts.push(product);
    }
  }

  if (bestProducts.length === 0) return { product: null, ambiguous: false };
  if (bestProducts.length === 1) return { product: bestProducts[0], ambiguous: false };
  return { product: null, ambiguous: true, candidates: bestProducts };
}

function findDeliveryZone(deliveryZones, zoneName) {
  if (!zoneName) return null;
  const zoneNorm = normalizeText(zoneName);
  return deliveryZones.find(dz =>
    dz.zones.some(z => normalizeText(z) === zoneNorm)
  );
}

function parsePrice(priceStr) {
  return parseInt(String(priceStr).replace(/[^\d]/g, ''), 10);
}

function validateOrder(state, merchant) {
  const errors = [];

  if (!state.product.confirmed || !state.product.value) {
    errors.push('produit non confirmé');
  }
  const product = merchant.products.find(p => p.name === state.product.value);
  if (!product) {
    errors.push('produit introuvable dans le catalogue');
  }

  if (product && product.sizes) {
    if (!state.size.confirmed || !state.size.value) {
      errors.push('taille non confirmée');
    } else if (!product.sizes.includes(state.size.value)) {
      errors.push('taille invalide pour ce produit');
    }
  }

  const hasColors = product && (product.colorVariants || product.extraColors || product.colors);
  if (hasColors) {
    let validColors = [];
    if (product.colorVariants) validColors = validColors.concat(product.colorVariants.map(c => c.color));
    if (product.extraColors) validColors = validColors.concat(product.extraColors);
    if (product.colors) validColors = validColors.concat(product.colors);

    if (!state.color.confirmed || !state.color.value) {
      errors.push('couleur non confirmée');
    } else if (!validColors.some(c => normalizeText(c) === normalizeText(state.color.value))) {
      errors.push('couleur invalide pour ce produit');
    }
  }

  if (!state.deliveryZone.confirmed || !state.deliveryZone.value) {
    errors.push('zone de livraison non confirmée');
  }
  const zoneMatch = findDeliveryZone(merchant.deliveryZones, state.deliveryZone.value);
  if (state.deliveryZone.value && !zoneMatch) {
    errors.push('zone de livraison inconnue');
  }

  if (state.orderSent) {
    errors.push('commande déjà transmise');
  }

  if (errors.length > 0) {
    return { valid: false, errors };
  }

  const productPrice = parsePrice(product.price);
  const deliveryPrice = zoneMatch.price;

  if (!Number.isFinite(productPrice) || !Number.isFinite(deliveryPrice)) {
    return { valid: false, errors: ['prix invalide, vérifiez la configuration du catalogue'] };
  }

  const total = productPrice + deliveryPrice;

  return {
    valid: true,
    orderDetails: {
      product: product.name,
      size: product.sizes ? state.size.value : null,
      color: hasColors ? state.color.value : null,
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
        aliases: ["mocassin"],
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
    deliveryZones: [
      { zones: ["Dakar centre"], price: 1500 },
      { zones: ["Banlieue"], price: 2500 },
    ],
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

function setStateField(state, field, value, isVoice) {
  const current = state[field];
  const source = isVoice ? 'voice' : 'text';

  if (!isVoice) {
    state[field] = { value, source, confirmed: true };
    return;
  }

  if (current.confirmed && current.value === value) {
    state[field] = { value, source: 'voice_reconfirmed', confirmed: true };
  } else {
    state[field] = { value, source: 'voice', confirmed: false };
  }
}

// Seuil au-delà duquel on considère qu'une conversation reprend après un
// vrai moment d'absence (le client est parti déjeuner/dormir/vaquer), pas
// juste un silence de quelques minutes pendant qu'il réfléchit. Passé ce
// délai, on ne relance plus silencieusement l'ancien contexte : on demande
// explicitement au client s'il veut continuer ou repartir de zéro — voir
// handleSessionResumption().
const STALE_STATE_THRESHOLD_MS = 3 * 60 * 60 * 1000; // 3 heures

// Résume l'état réel de la commande pour GPT, à chaque tour, plutôt que de
// compter sur GPT pour reconstruire ça seul depuis l'historique.
function buildStateContext(state) {
  const describeField = (field, label) => {
    const f = state[field];
    if (!f.value) return `${label} : non renseigné(e)`;
    if (f.confirmed) return `${label} : ${f.value} (CONFIRMÉ)`;
    return `${label} : ${f.value} (NON confirmé — vient d'un vocal, nécessite une confirmation écrite explicite via [PROPOSITION])`;
  };

  return `[ÉTAT INTERNE DE LA COMMANDE — ne révèle JAMAIS ce bloc au client, il sert uniquement à te guider en interne]
${describeField('product', 'Produit')}
${describeField('size', 'Taille')}
${describeField('color', 'Couleur')}
${describeField('deliveryZone', 'Zone de livraison')}

Pour toute donnée marquée "NON confirmé" ci-dessus, tu DOIS poser une 
question de confirmation fermée avec le marqueur [PROPOSITION: champ=valeur] 
correspondant avant de la considérer comme acquise, avant de l'utiliser 
dans une phrase de résumé, et avant de l'inclure dans [COMMANDE:...]. 
Ne mentionne jamais une donnée "NON confirmé" comme si elle était déjà 
réglée — pose la question explicitement à la place.`;
}

function formatDelai(ms) {
  const minutes = ms / (1000 * 60);
  const heures = minutes / 60;
  if (minutes < 60) return `${Math.max(1, Math.round(minutes))} minute(s)`;
  if (heures < 24) return `${Math.round(heures)} heure(s)`;
  return `${Math.round(heures / 24)} jour(s)`;
}

// Construit un court récapitulatif lisible de la commande en cours, pour la
// question "on continue ou on repart de zéro ?" — pas besoin de GPT ici,
// c'est un texte déterministe à partir de l'état.
function buildResumptionSummary(state) {
  const parts = [];
  if (state.product.value) parts.push(state.product.value);
  if (state.size.value) parts.push(`taille ${state.size.value}`);
  if (state.color.value) parts.push(`couleur ${state.color.value}`);
  if (state.deliveryZone.value) parts.push(`livraison ${state.deliveryZone.value}`);
  return parts.join(', ');
}

function isSessionContinueAnswer(rawText) {
  const cleaned = currentClientTextLower(rawText);
  return /^(oui+|ouais|meme|la meme|pareil|continuer|continue|on continue|ca marche|ca va|d'accord|daccord|ok+)$/i.test(cleaned)
    || /continu/i.test(cleaned);
}

function isSessionRestartAnswer(rawText) {
  const cleaned = currentClientTextLower(rawText);
  return /nouvelle|nouveau|autre chose|recommenc|repart|zero|non merci/i.test(cleaned);
}

// Un message qui nomme déjà clairement un produit, ou une zone de livraison
// connue, n'a pas besoin qu'on lui demande "on continue ou pas ?" — il parle
// de lui-même. Utilisé pour ne jamais faire barrage à une demande explicite
// avec la question de reprise de session (voir handleSessionResumption).
function messageHasBusinessContent(merchant, currentText) {
  const productDetection = findMentionedProduct(merchant.products, currentText);
  if (productDetection.product || productDetection.ambiguous) return true;
  if (merchant.deliveryZones) {
    for (const dz of merchant.deliveryZones) {
      if (dz.zones.some(z => currentText.includes(normalizeText(z)))) return true;
    }
  }
  return false;
}

// Intercepte le tout début de processMessage quand une commande en cours,
// abandonnée depuis plus de STALE_STATE_THRESHOLD_MS, refait surface.
// Retourne true si le message a été entièrement géré ici (il ne faut alors
// pas continuer vers la détection produit/GPT pour ce tour) ; false sinon —
// y compris quand on vient de réinitialiser l'état pour un nouveau sujet,
// afin que CE MÊME message soit ensuite traité normalement (pas perdu).
async function handleSessionResumption({ merchant, phoneNumberId, from, text, state, messageId }) {
  const hasAnyData = state.product.value || state.size.value || state.color.value || state.deliveryZone.value;
  const currentText = currentClientTextLower(text);
  const hasNewContent = messageHasBusinessContent(merchant, currentText);

  // Trace de décision : sans elle, impossible de savoir a posteriori
  // pourquoi une reprise de session a (ou n'a pas) été proposée.
  if (hasAnyData || state.awaitingSessionChoice) {
    console.log('DEBUG - SESSION CHECK', {
      messageId,
      hasAnyData: Boolean(hasAnyData),
      awaiting: Boolean(state.awaitingSessionChoice),
      hasNewContent,
      elapsedMin: state.lastActivityAt ? Math.round((Date.now() - state.lastActivityAt) / 60000) : null,
    });
  }

  if (state.awaitingSessionChoice) {
    if (hasNewContent || isSessionRestartAnswer(text)) {
      // Le client répond soit par un signal explicite de nouveau départ,
      // soit — plus fréquent — en nommant directement ce qu'il veut
      // vraiment (ex: un autre produit). Dans les deux cas : on repart de
      // zéro SANS renvoyer de message générique qui écraserait ce contenu
      // — on laisse ce même message continuer vers la détection normale.
      console.log('DEBUG - Reprise de session : nouveau départ (contenu explicite ou "nouvelle")', { messageId, hasNewContent });
      Object.assign(state, emptyState());
      return false;
    }
    if (isSessionContinueAnswer(text)) {
      console.log('DEBUG - Reprise de session : le client continue', { messageId });
      state.awaitingSessionChoice = false;
      state.lastActivityAt = Date.now();
      const recap = buildResumptionSummary(state);
      await sendWhatsAppText(
        phoneNumberId, from,
        recap
          ? `Parfait, on reprend ! Pour rappel : ${recap}. Dites-moi comment continuer. 🙏`
          : "Parfait, on continue ! Dites-moi où on en était. 🙏"
      );
      return true;
    }
    // Réponse pas claire et sans contenu exploitable — on repose la
    // question plutôt que de deviner.
    await sendWhatsAppText(
      phoneNumberId, from,
      "Pardon, je n'ai pas bien compris : voulez-vous qu'on continue la commande en cours, ou qu'on reparte sur autre chose ? 🙏"
    );
    return true;
  }

  // Un message qui contient déjà une demande claire (produit, zone...) n'a
  // jamais besoin de la question de reprise, quelle que soit l'ancienneté
  // de l'état — il sera traité normalement, avec la réinitialisation
  // partielle habituelle si le produit diffère de celui déjà en cours.
  if (hasNewContent) {
    return false;
  }

  if (hasAnyData && state.lastActivityAt && (Date.now() - state.lastActivityAt) > STALE_STATE_THRESHOLD_MS) {
    console.log('DEBUG - Session ancienne détectée, question de reprise posée', {
      messageId, delaiMs: Date.now() - state.lastActivityAt,
    });
    state.awaitingSessionChoice = true;
    const recap = buildResumptionSummary(state);
    await sendWhatsAppText(
      phoneNumberId, from,
      `On avait commencé à discuter${recap ? ' de ' + recap : ''} il y a ${formatDelai(Date.now() - state.lastActivityAt)}. ` +
      `Vous voulez qu'on continue cette commande, ou qu'on reparte sur autre chose ? 🙏`
    );
    return true;
  }

  return false;
}

// Retire le marqueur vocal ET, s'il existe, le bloc de la seconde transcription (ASR_PROVIDER=dual) : tous les
// détecteurs déterministes ne lisent ainsi que la transcription principale, exactement comme avant.
function stripVoiceMarkers(rawText) {
  return String(rawText)
    .replace(/\s*\[AUTRE TRANSCRIPTION AUTOMATIQUE[^\]]*\][\s\S]*$/i, '')
    .replace(/^\[MESSAGE VOCAL[^\]]*\]\s*/i, '');
}

function currentClientTextLower(rawText) {
  return normalizeText(stripVoiceMarkers(rawText));
}

function isShortAffirmative(rawText) {
  const cleaned = currentClientTextLower(rawText);
  return /^(oui+|ouais|ok+|okay|d'accord|daccord|c'est bon|cest bon|ca marche|confirme|parfait|va y)$/i.test(cleaned);
}

function stripWolofMarker(content) {
  return String(content || '').replace(/\s*\[BESOIN_WOLOF\]/g, '').trim();
}

// Détection DÉTERMINISTE (sans GPT) d'un client qui dit ne pas comprendre le
// français ou qui demande explicitement qu'on lui parle wolof. Volontairement
// ÉTROITE : seuls des signaux forts déclenchent, jamais le simple mot
// "wolof" ou "français" (un client peut très bien écrire "je parle français
// et un peu wolof" ou "vous parlez wolof ?" sans être bloqué).
// Limite connue : sur un message vocal, XLS-R peut déformer "deggouma" au
// point que ces motifs ne matchent pas — d'où le marqueur [BESOIN_WOLOF]
// côté GPT conservé en complément, pas remplacé.
function indicatesFrenchBlock(rawText) {
  const t = currentClientTextLower(rawText);
  // "je ne comprends pas" en wolof : deggouma, degguma, deguma, dégguma...
  // (les accents sont déjà retirés par normalizeText).
  const wolofDontUnderstand = /\bdegg?[a-z]{0,3}ma+\b/.test(t);
  const mentionsFrench = /fran[cs]/.test(t);
  // "je ne comprends / parle / lis pas (le) français" en français
  const frenchDontUnderstand = /\b(ne )?(comprends?|parle|lis|sais) pas (le )?fran[cs]/.test(t);
  // "parle-moi wolof" : waxal wolof, wax ma wolof, wax ak ma wolof
  const asksForWolof = /\b(waxal|wax ma|wax ak ma)\b.{0,15}\bwolof\b/.test(t);

  return (wolofDontUnderstand && mentionsFrench) || frenchDontUnderstand || asksForWolof;
}

// Le marqueur [BESOIN_WOLOF] émis par GPT est un signal FAIBLE : un faux
// positif a été observé en réel sur un simple "salamalekoum". Il n'est donc
// retenu seul que sur un message vocal (l'ASR peut déformer la phrase au
// point que indicatesFrenchBlock ne la reconnaisse plus). Sur du texte écrit,
// il doit être corroboré par une mention explicite de la langue.
function isGptWolofSignalCredible({ langueBlocageMatch, wasVoiceMessage, text }) {
  if (!langueBlocageMatch) return false;
  if (wasVoiceMessage) return true;
  return /fran[cs]|wolof/.test(currentClientTextLower(text));
}

// Réponse FIXE envoyée à un client qui dit ne pas comprendre le français.
// Volontairement écrite à la main et non générée : un test réel a montré que
// GPT improvise alors du wolof faux (mots hors de la liste autorisée) et
// relance la commande en cours. Seuls des mots wolof validés y figurent.
// ⚠️ À faire relire, et idéalement réécrire en vrai wolof, par un locuteur.
const WOLOF_HANDOFF_REPLY = "Waaw 🙏 Jërejëf ! Le commerçant va vous répondre directement en wolof.";

const WOLOF_ALERT_COOLDOWN_SECONDS = 6 * 60 * 60;

// Prévient le commerçant qu'un client ne comprend pas le français.
// Deux clés Redis distinctes, HORS de `state` (l'état est réinitialisé lors
// d'un nouveau départ ou après une commande, ce qui effacerait le délai).
// SET NX EX est atomique : un seul appelant obtient la clé.
//  - strong=true  : détection déterministe → clé "wolofalert:"
//  - strong=false : marqueur GPT seul      → clé "wolofalert-gpt:"
// Séparées pour qu'une fausse alerte GPT ne bloque PAS pendant 6 h une vraie
// détection déterministe. Une alerte GPT seule est en revanche ignorée si une
// alerte déterministe a déjà été envoyée : le commerçant est déjà prévenu.
// Retourne true si une alerte est effectivement partie.
async function notifyMerchantWolofNeed({ merchant, phoneNumberId, from, messageId, strong, source, clientMessage }) {
  if (!merchant.ownerWhatsApp) return false;

  const strongKey = `wolofalert:${phoneNumberId}_${from}`;
  const gptKey = `wolofalert-gpt:${phoneNumberId}_${from}`;
  let alertKey = null;
  if (strong) {
    const acquired = await redis.set(strongKey, '1', { nx: true, ex: WOLOF_ALERT_COOLDOWN_SECONDS });
    if (acquired) alertKey = strongKey;
  } else if (!(await redis.get(strongKey))) {
    const acquired = await redis.set(gptKey, '1', { nx: true, ex: WOLOF_ALERT_COOLDOWN_SECONDS });
    if (acquired) alertKey = gptKey;
  }

  if (!alertKey) {
    console.log('DEBUG - Besoin wolof détecté mais commerçant déjà alerté récemment, pas de doublon', { messageId, from, source });
    return false;
  }

  // Le bot ne traite plus ce message lui-même : on transmet au commerçant ce
  // que le client a écrit, pour qu'il ait le contexte.
  const citation = stripVoiceMarkers(String(clientMessage || '')).trim().slice(0, 200);
  const ligneCitation = citation ? `\nDernier message du client : « ${citation} »` : '';

  try {
    await sendWhatsAppText(
      phoneNumberId, merchant.ownerWhatsApp,
      `🗣️ Un client (${from}) ne comprend pas le français — pouvez-vous lui répondre directement en wolof ?${ligneCitation}\nMessage WhatsApp : wa.me/${from}`
    );
    console.log('DEBUG - NOTIFICATION BESOIN WOLOF envoyée au commerçant', { messageId, from, source });
    return true;
  } catch (err) {
    // L'envoi a échoué : on libère la clé pour ne pas bloquer 6 h une
    // alerte qui n'est jamais partie.
    await redis.del(alertKey);
    console.error('DEBUG - Échec notification besoin wolof:', err);
    return false;
  }
}

const FIELD_MAP = { produit: 'product', taille: 'size', couleur: 'color', zone: 'deliveryZone' };

async function fetchWithTimeout(url, options = {}, timeoutMs = 20000) {
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), timeoutMs);
  try {
    return await fetch(url, { ...options, signal: controller.signal });
  } finally {
    clearTimeout(timeoutId);
  }
}

async function sendWhatsAppText(phoneNumberId, to, body) {
  const response = await fetchWithTimeout(`https://graph.facebook.com/v21.0/${phoneNumberId}/messages`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${process.env.WHATSAPP_ACCESS_TOKEN}` },
    body: JSON.stringify({ messaging_product: 'whatsapp', to, text: { body } }),
  });
  if (!response.ok) {
    const errorText = await response.text();
    throw new Error(`Échec envoi texte WhatsApp ${response.status}: ${errorText}`);
  }
  return response;
}

async function sendWhatsAppImage(phoneNumberId, to, imageUrl, caption) {
  const response = await fetchWithTimeout(`https://graph.facebook.com/v21.0/${phoneNumberId}/messages`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${process.env.WHATSAPP_ACCESS_TOKEN}` },
    body: JSON.stringify({ messaging_product: 'whatsapp', to, type: 'image', image: { link: imageUrl, caption: caption || '' } }),
  });
  const data = await response.json();
  console.log('DEBUG - Envoi image:', imageUrl, JSON.stringify(data));
  if (!response.ok) {
    throw new Error(`Échec envoi image WhatsApp ${response.status}: ${JSON.stringify(data)}`);
  }
  return data;
}

// ---------------------------------------------------------------------------
// ASR — fournisseur interchangeable (ASR_PROVIDER=whisper|xlsr|whosper|dual)
// ---------------------------------------------------------------------------
// Choix retenu après benchmark sur 52 vocaux réels (voir le classeur de
// comparaison) : MMS-1B-all est exclu de ce fichier — licence CC-BY-NC-4.0,
// non exploitable commercialement, gardé uniquement dans les notebooks de
// benchmark hors-ligne, jamais ici. Les deux candidats retenus :
//   - xlsr    : abdouaziiz/wav2vec2-xls-r-300m-wolof-lm (MIT). Rapide
//               (~55ms mesurés sur GPU), mais faible sur le français/
//               code-switching.
//   - whosper : CAYTU/whosper-large-v2 (Apache 2.0). Nettement meilleur sur
//               le français/code-switching, mais ~4s par vocal même sur
//               GPU (décodage autorégressif) — donc plus lent et plus
//               coûteux à héberger.
// Aucun des deux ne dépasse 3-4 bonnes reconnaissances sur 9 nombres
// critiques testés (tailles/prix) : le garde-fou de confirmation écrite
// obligatoire (plus bas dans ce fichier) reste donc requis quel que soit le
// choix ici, ce n'est pas négociable.
//
// XLSR_SERVICE_URL / WHOSPER_SERVICE_URL : URL du service HTTP externe
// (voir /mnt/user-data/outputs/mms-asr-service, à adapter pour chacun des
// deux modèles) exposant un endpoint POST /transcribe qui accepte le fichier
// audio et renvoie { text: "..." }. Tant que ces services ne sont pas
// déployés, ASR_PROVIDER=whisper (comportement actuel, OpenAI) reste la
// valeur par défaut — pas de rupture si les variables ne sont pas encore
// configurées.
const ASR_PROVIDER = process.env.ASR_PROVIDER || 'whisper';
// 60s, pas 30s : le benchmark de concurrence sur XLS-R/Render 1c-2g a montré
// un service séquentiel (pas de vrai parallélisme) — un lot de 3 requêtes
// simultanées a pris jusqu'à ~18,5s pour la dernière, et un épisode isolé
// non expliqué a atteint ~21s pour une seule requête. 30s laissait trop peu
// de marge si plusieurs commerçants envoient un vocal presque en même temps.
const ASR_SERVICE_TIMEOUT_MS = 60000;
// Secret partagé entre ce webhook et les services ASR externes (XLS-R,
// Whosper) — évite qu'un tiers découvrant l'URL Render puisse consommer les
// ressources du service. Voir le correctif correspondant côté app.py.
const ASR_SERVICE_SHARED_SECRET = process.env.ASR_SERVICE_SHARED_SECRET;

// ASR_PROVIDER=dual : deux services ASR interrogés EN PARALLÈLE sur le même vocal.
//   - XLSR_SERVICE_URL : le service XLS-R actuel (transcription « principale »)
//   - NOXA_SERVICE_URL : le service du modèle ajusté NOXA (transcription « alternative »)
// Mesuré sur UNE SEULE voix (29 vocaux, 37 champs critiques relus par Claude) : environ 67 % de champs justes avec les
// deux transcriptions contre 31 % avec XLS-R seul. À confirmer sur d'autres voix : ne pas le tenir pour acquis.
// La transcription principale (XLS-R) alimente SEULE tous les détecteurs déterministes (produit, taille, couleur, zone,
// oui court, blocage linguistique), exactement comme avant ; la seconde n'est donnée qu'au modèle de conversation.
// Chaque service peut avoir son propre secret (XLSR_SERVICE_SECRET, NOXA_SERVICE_SECRET) ; à défaut, le secret
// partagé ci-dessus. Si l'un des deux échoue, l'autre sert seul ; si les deux échouent, repli Whisper-FR comme
// d'habitude. Retour arrière immédiat : ASR_PROVIDER=xlsr. Le garde-fou « un vocal ne confirme jamais une commande »
// est inchangé.

// ---------------------------------------------------------------------------
// FOURNISSEUR DE CONVERSATION — interchangeable (AI_PROVIDER=gpt4o|claude)
// ---------------------------------------------------------------------------
// Pilote : après le benchmark à l'aveugle sur 16 messages en wolof urbain
// (naturel/exactitude/code-switching notés par le commerçant, attributions
// vérifiées contre la clé secrète), Claude et Gemini sont nettement devant
// GPT-4o sur le naturel et le code-switching. On teste donc Claude sur du
// vrai trafic, avec repli immédiat vers GPT-4o en cas de besoin.
// AI_PROVIDER=gpt4o reste la valeur par défaut : rien ne change si la
// variable n'est pas configurée.
// Point de vigilance identifié AVANT ce pilote, à surveiller pendant : sur ce
// même benchmark, Claude n'a utilisé le marqueur [PROPOSITION] aucune fois
// (0/16), contre 2 pour GPT-4o et 3 pour Gemini — y compris sur des messages
// ambigus où une proposition de confirmation aurait été utile. Ça ne prouve
// pas que Claude ne sait pas l'utiliser, mais ça reste à vérifier en
// conditions réelles avant de considérer le pilote concluant.
const AI_PROVIDER = process.env.AI_PROVIDER || 'gpt4o';
const ANTHROPIC_MODEL = process.env.ANTHROPIC_MODEL || 'claude-sonnet-5-5';
const AI_REQUEST_TIMEOUT_MS = 30000;

// Appelle OpenAI (comportement historique, inchangé). Retourne le texte de
// réponse, ou lève une erreur — chaque appelant décide comment réagir.
async function callOpenAIChat({ systemPrompt, stateContext, recentHistory }) {
  const response = await fetchWithTimeout('https://api.openai.com/v1/chat/completions', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${process.env.OPENAI_API_KEY}` },
    body: JSON.stringify({
      model: 'gpt-4o',
      messages: [
        { role: 'system', content: systemPrompt },
        { role: 'system', content: stateContext },
        ...recentHistory,
      ],
    }),
  }, AI_REQUEST_TIMEOUT_MS);
  if (!response.ok) {
    throw new Error(`OpenAI ${response.status}: ${await response.text()}`);
  }
  const data = await response.json();
  return data.choices?.[0]?.message?.content || null;
}

// Appelle Claude (API Messages d'Anthropic). Contrat de sortie identique à
// callOpenAIChat : le texte brut, marqueurs [PHOTO]/[COMMANDE]/[PROPOSITION]/
// [BESOIN_WOLOF] compris — leur extraction plus bas dans processMessage est
// déjà indépendante du fournisseur, aucun changement nécessaire là-bas.
// Différence d'API à noter : Anthropic prend un seul champ `system` (pas une
// liste de messages system) et exige `max_tokens`, qu'OpenAI ne demande pas.
async function callClaudeChat({ systemPrompt, stateContext, recentHistory }) {
  const response = await fetchWithTimeout('https://api.anthropic.com/v1/messages', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'x-api-key': process.env.ANTHROPIC_API_KEY,
      'anthropic-version': '2023-06-01',
    },
    body: JSON.stringify({
      model: ANTHROPIC_MODEL,
      max_tokens: 1024,
      system: `${systemPrompt}\n\n${stateContext}`,
      messages: recentHistory,
    }),
  }, AI_REQUEST_TIMEOUT_MS);
  if (!response.ok) {
    throw new Error(`Anthropic ${response.status}: ${await response.text()}`);
  }
  const data = await response.json();
  return (data.content || []).filter(b => b.type === 'text').map(b => b.text).join('') || null;
}

// Point d'entrée unique — processMessage n'a pas besoin de savoir quel
// fournisseur répond, seulement de recevoir le texte ou une erreur.
async function callAiChat(args) {
  if (AI_PROVIDER === 'claude') {
    return callClaudeChat(args);
  }
  return callOpenAIChat(args);
}

async function downloadWhatsAppAudio(mediaId) {
  const mediaInfoResponse = await fetchWithTimeout(`https://graph.facebook.com/v21.0/${mediaId}`, { headers: { 'Authorization': `Bearer ${process.env.WHATSAPP_ACCESS_TOKEN}` } });
  const mediaInfo = await mediaInfoResponse.json();
  const audioResponse = await fetchWithTimeout(mediaInfo.url, { headers: { 'Authorization': `Bearer ${process.env.WHATSAPP_ACCESS_TOKEN}` } });
  return await audioResponse.arrayBuffer();
}

async function transcribeWithWhisperFR(audioBuffer) {
  const formData = new FormData();
  formData.append('file', new Blob([audioBuffer], { type: 'audio/ogg' }), 'audio.ogg');
  formData.append('model', 'whisper-1');
  formData.append('language', 'fr');
  const response = await fetchWithTimeout('https://api.openai.com/v1/audio/transcriptions', {
    method: 'POST', headers: { 'Authorization': `Bearer ${process.env.OPENAI_API_KEY}` }, body: formData,
  });
  if (!response.ok) {
    console.error('DEBUG - Échec transcription Whisper-FR:', await response.text());
    return null;
  }
  const data = await response.json();
  return data.text || null;
}

// Fonction commune : les deux services externes (XLS-R, Whosper) partagent
// exactement le même contrat HTTP (voir mms-asr-service/app.py, réutilisable
// tel quel pour XLS-R ; Whosper nécessite un service équivalent avec le
// paquet `whosper` — non fourni ici, à construire sur le même modèle).
async function transcribeViaExternalService(serviceUrl, audioBuffer, providerName, secret = ASR_SERVICE_SHARED_SECRET) {
  if (!serviceUrl) {
    console.error(`DEBUG - ${providerName}: URL de service non configurée (variable d'environnement manquante)`);
    return null;
  }
  const formData = new FormData();
  formData.append('file', new Blob([audioBuffer], { type: 'audio/ogg' }), 'audio.ogg');
  try {
    const response = await fetchWithTimeout(`${serviceUrl}/transcribe`, {
      method: 'POST',
      headers: secret ? { 'Authorization': `Bearer ${secret}` } : {},
      body: formData,
    }, ASR_SERVICE_TIMEOUT_MS);
    if (!response.ok) {
      console.error(`DEBUG - Échec transcription ${providerName}:`, await response.text());
      return null;
    }
    const data = await response.json();
    return data.text || null;
  } catch (err) {
    console.error(`DEBUG - Erreur appel service ${providerName}:`, err);
    return null;
  }
}

function transcribeWithXLSR(audioBuffer) {
  return transcribeViaExternalService(process.env.XLSR_SERVICE_URL, audioBuffer, 'XLS-R', process.env.XLSR_SERVICE_SECRET || ASR_SERVICE_SHARED_SECRET);
}

function transcribeWithNoxa(audioBuffer) {
  return transcribeViaExternalService(process.env.NOXA_SERVICE_URL, audioBuffer, 'NOXA', process.env.NOXA_SERVICE_SECRET || ASR_SERVICE_SHARED_SECRET);
}

function transcribeWithWhosper(audioBuffer) {
  return transcribeViaExternalService(process.env.WHOSPER_SERVICE_URL, audioBuffer, 'Whosper');
}

// Retourne { text, provider, fallbackUsed } plutôt qu'une simple chaîne —
// savoir QUI a réellement produit la transcription est indispensable pour
// interpréter les logs et ne pas croire, à tort, qu'un fournisseur "rapide"
// a répondu alors que c'est le repli Whisper-FR qui a pris le relais.
async function transcribeWhatsAppAudio(mediaId) {
  const audioBuffer = await downloadWhatsAppAudio(mediaId);

  let text = null;
  let alternativeText = null;
  let provider = ASR_PROVIDER;
  let fallbackUsed = false;

  if (ASR_PROVIDER === 'dual') {
    // Les deux services travaillent en même temps : l'attente est celle du plus lent, pas la somme.
    const chronometre = async (fn) => {
      const debut = Date.now();
      const value = await fn(audioBuffer);
      return { value, ms: Date.now() - debut };
    };
    const [xlsr, noxa] = await Promise.all([chronometre(transcribeWithXLSR), chronometre(transcribeWithNoxa)]);
    console.log('DEBUG - ASR DUAL', {
      xlsrOk: !!xlsr.value, noxaOk: !!noxa.value, xlsrMs: xlsr.ms, noxaMs: noxa.ms,
      agree: !!(xlsr.value && noxa.value) && normalizeText(xlsr.value) === normalizeText(noxa.value),
    });
    if (xlsr.value && noxa.value) {
      text = xlsr.value;
      alternativeText = noxa.value;
      provider = 'xlsr+noxa';
    } else if (xlsr.value) {
      text = xlsr.value;
      provider = 'xlsr';
    } else if (noxa.value) {
      text = noxa.value;
      provider = 'noxa';
    }
  } else if (ASR_PROVIDER === 'xlsr') {
    text = await transcribeWithXLSR(audioBuffer);
  } else if (ASR_PROVIDER === 'whosper') {
    text = await transcribeWithWhosper(audioBuffer);
  } else {
    text = await transcribeWithWhisperFR(audioBuffer);
  }

  // Filet de sécurité : si le fournisseur choisi échoue (service down,
  // timeout, variable d'environnement absente, réponse vide...) et que ce
  // n'est pas déjà Whisper-FR, on retente avec Whisper-FR plutôt que de
  // perdre le message.
  if (!text && ASR_PROVIDER !== 'whisper') {
    console.log(`DEBUG - Repli sur Whisper-FR après échec de ${ASR_PROVIDER}`);
    text = await transcribeWithWhisperFR(audioBuffer);
    provider = 'whisper';
    fallbackUsed = true;
  }

  console.log('DEBUG - ASR RESULT', { requestedProvider: ASR_PROVIDER, actualProvider: provider, fallbackUsed, textLength: text?.length || 0, hasAlternative: !!alternativeText });

  return { text, provider, fallbackUsed, alternativeText };
}

// Texte du message vocal tel que stocké dans l'historique et vu par le modèle de conversation. Sans seconde
// transcription, EXACTEMENT le format d'avant. Avec elle, un second bloc balisé que stripVoiceMarkers() sait retirer.
function formatVoiceMessage(asrResult) {
  const principal = `[MESSAGE VOCAL - transcription potentiellement imparfaite, fournisseur=${asrResult.provider}${asrResult.fallbackUsed ? ' (repli)' : ''}] ${asrResult.text}`;
  if (!asrResult.alternativeText) return principal;
  return `${principal}\n[AUTRE TRANSCRIPTION AUTOMATIQUE du même vocal, par un second système] ${asrResult.alternativeText}`;
}

function formatNumbersForSpeech(text) {
  return text.replace(/\b\d{5,}\b/g, (match) => {
    if (match.length >= 9) {
      const country = match.slice(0, 3);
      const rest = match.slice(3);
      const groups = rest.match(/.{1,2}/g) || [];
      return `${country} ${groups.join(' ')}`;
    }
    return match.replace(/(\d)(?=(\d{3})+(?!\d))/g, '$1 ');
  });
}

async function generateAndSendVoiceReply(phoneNumberId, to, text) {
  try {
    const speechText = formatNumbersForSpeech(text);

    const ttsResponse = await fetchWithTimeout('https://api.openai.com/v1/audio/speech', {
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
    if (!ttsResponse.ok) {
      console.error('DEBUG - Échec génération TTS:', await ttsResponse.text());
      return;
    }
    const audioBuffer = await ttsResponse.arrayBuffer();

    const formData = new FormData();
    formData.append('file', new Blob([audioBuffer], { type: 'audio/mpeg' }), 'reply.mp3');
    formData.append('messaging_product', 'whatsapp');

    const uploadResponse = await fetchWithTimeout(
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

    await fetchWithTimeout(`https://graph.facebook.com/v21.0/${phoneNumberId}/messages`, {
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
  }
}

// ---------------------------------------------------------------------------
// LOGIQUE DE TRAITEMENT D'UN MESSAGE — extraite dans sa propre fonction pour
// qu'elle s'exécute proprement à l'intérieur du bloc verrouillé, avec un seul
// point de sauvegarde en sortie (succès ou échec).
// ---------------------------------------------------------------------------
async function processMessage({ merchant, phoneNumberId, from, text, wasVoiceMessage, state, history, messageId }) {
  // Client qui dit ne pas comprendre le français : réponse FIXE, sans GPT, et
  // alerte du commerçant. Traité AVANT tout le reste (reprise de session,
  // détection produit, GPT) : le message ne modifie ni l'état de commande ni
  // n'en relance un ancien. L'alerte part en premier : si l'envoi au client
  // échoue et que le message est remis en file, le commerçant est déjà prévenu.
  if (indicatesFrenchBlock(text)) {
    console.log('DEBUG - Blocage linguistique détecté (déterministe) : réponse fixe, GPT court-circuité', { messageId });
    await notifyMerchantWolofNeed({
      merchant, phoneNumberId, from, messageId,
      strong: true, source: 'détection déterministe', clientMessage: text,
    });
    await sendWhatsAppText(phoneNumberId, from, WOLOF_HANDOFF_REPLY);
    history.push({ role: 'user', content: text });
    history.push({ role: 'assistant', content: WOLOF_HANDOFF_REPLY });
    state.lastActivityAt = Date.now();
    return;
  }

  if (state.orderSent) {
    // Une commande a déjà été transmise sur cette conversation. Sans cette
    // réinitialisation, l'état (produit/taille/couleur/zone/orderSent) reste
    // bloqué indéfiniment (TTL de 3 jours) et toute nouvelle commande — même
    // légitime — se retrouve rejetée ou simplement re-narrée comme si de rien
    // n'était. On repart donc sur un état de commande neuf : l'historique de
    // conversation, lui, reste intact pour le contexte.
    console.log('DEBUG - Commande précédente déjà transmise, réinitialisation de l\'état pour ce nouveau message', { messageId });
    Object.assign(state, emptyState());
  }

  // Une commande en cours, abandonnée depuis un moment, qui refait surface —
  // on demande explicitement au client s'il veut continuer ou repartir de
  // zéro plutôt que de deviner à sa place. Si géré ici, le message est
  // entièrement traité (réponse envoyée) : on s'arrête avant GPT pour ce tour.
  if (await handleSessionResumption({ merchant, phoneNumberId, from, text, state, messageId })) {
    return;
  }

  if (state.pendingProposals) {
    if (isShortAffirmative(text)) {
      for (const [field, value] of Object.entries(state.pendingProposals)) {
        setStateField(state, field, value, wasVoiceMessage);
      }
    }
    state.pendingProposals = null;
  }

  const currentText = currentClientTextLower(text);

  let mentionedProduct = null;
  const productDetection = findMentionedProduct(merchant.products, currentText);

  if (productDetection.ambiguous) {
    // Deux produits (ou plus) matchent avec la même force — ne pas deviner.
    // On redemande explicitement plutôt que de risquer le mauvais produit.
    const names = productDetection.candidates.map(p => p.name).join(' ou ');
    const clarification = `Vous parlez bien de : ${names} ? Précisez-moi lequel des deux 🙏`;
    console.log('DEBUG - Produit ambigu, clarification demandée:', names);
    history.push({ role: 'user', content: text });
    history.push({ role: 'assistant', content: clarification });
    await sendWhatsAppText(phoneNumberId, from, clarification);
    return;
  }

  mentionedProduct = productDetection.product;

  if (mentionedProduct) {
    const isNewProduct = state.product.value && state.product.value !== mentionedProduct.name;
    setStateField(state, 'product', mentionedProduct.name, wasVoiceMessage);
    if (isNewProduct) {
      state.size = emptyField();
      state.color = emptyField();
    }
  } else if (state.product.value) {
    mentionedProduct = merchant.products.find(p => p.name === state.product.value);
  }

  if (mentionedProduct && mentionedProduct.sizes) {
    const sizeMatch = mentionedProduct.sizes.find(s => {
      const regex = new RegExp(`\\b${s}\\b`);
      return regex.test(currentText);
    });
    if (sizeMatch) {
      setStateField(state, 'size', sizeMatch, wasVoiceMessage);
    }
  }

  let detectedColor = null;
  if (mentionedProduct) {
    let allColors = [];
    if (mentionedProduct.colorVariants) {
      allColors = allColors.concat(mentionedProduct.colorVariants.map(c => ({
        value: c.color, label: c.label || c.color,
      })));
    }
    if (mentionedProduct.extraColors) {
      allColors = allColors.concat(mentionedProduct.extraColors.map(c => ({ value: c, label: c })));
    }
    if (mentionedProduct.colors) {
      allColors = allColors.concat(mentionedProduct.colors.map(c => ({ value: c, label: c })));
    }

    const found = allColors.find(c =>
      currentText.includes(normalizeText(c.value)) || currentText.includes(normalizeText(c.label))
    );
    if (found) {
      detectedColor = found.value;
      setStateField(state, 'color', found.value, wasVoiceMessage);
    }
  }

  if (merchant.deliveryZones) {
    let foundZone = null;
    for (const dz of merchant.deliveryZones) {
      const match = dz.zones.find(z => currentText.includes(normalizeText(z)));
      if (match) { foundZone = match; break; }
    }
    if (foundZone) {
      setStateField(state, 'deliveryZone', foundZone, wasVoiceMessage);
    }
  }

  console.log('DEBUG - currentText:', currentText, '| produit détecté:', mentionedProduct ? mentionedProduct.name : 'AUCUN');
  console.log('DEBUG - STATE APRES DETECTION:', JSON.stringify(state));

  history.push({ role: 'user', content: text });
  // L'historique stocke les réponses brutes de GPT, marqueurs compris. Pour
  // [BESOIN_WOLOF] c'est un risque : un modèle qui revoit son propre marqueur
  // dans le contexte a tendance à le recopier sur les tours suivants, même
  // sur une simple salutation. On le retire à l'usage (donc aussi de
  // l'historique déjà stocké), sans toucher aux autres marqueurs dont le
  // comportement est déjà éprouvé.
  const recentHistory = history.slice(-10).map(m =>
    m.role === 'assistant' ? { ...m, content: stripWolofMarker(m.content) } : m
  );

  const stateContext = buildStateContext(state);
  // Marque cette interaction comme le nouveau point de référence pour
  // détecter une future reprise de session après un silence prolongé (voir
  // handleSessionResumption).
  state.lastActivityAt = Date.now();

  let replyText;
  try {
    replyText = await callAiChat({
      systemPrompt: merchant.systemPrompt,
      stateContext,
      recentHistory,
    });
  } catch (err) {
    console.error(`Erreur appel ${AI_PROVIDER}:`, err);
    await sendWhatsAppText(phoneNumberId, from, "Petit souci technique de notre côté, on revient vers vous très vite !");
    return;
  }
  if (!replyText) {
    replyText = "Désolé, je n'ai pas pu traiter votre message.";
  }
  console.log('DEBUG - AI RAW', { provider: AI_PROVIDER, replyText });
  history.push({ role: 'assistant', content: replyText });

  const photoMatch = replyText.match(/\[PHOTO:\s*(.+?)\]/);
  const orderMatch = replyText.match(/\[COMMANDE:\s*(.+?)\]/);
  const propositionMatch = replyText.match(/\[PROPOSITION:\s*(.+?)\]/);
  const langueBlocageMatch = /\[BESOIN_WOLOF\]/.test(replyText);

  // Filet générique, pas seulement les 4 marqueurs connus : un test à l'aveugle
  // (voir ab-wolof/) a montré GPT inventer [PRODUIT: ...], un marqueur qui
  // n'existe dans aucune instruction du prompt. L'ancien code, qui ne retirait
  // que PHOTO/COMMANDE/PROPOSITION/BESOIN_WOLOF nommément, l'aurait laissé
  // partir tel quel au client. Cette regex retire TOUT crochet dont le
  // contenu commence par un mot en MAJUSCULES (le style que le prompt utilise
  // pour ses propres marqueurs), qu'il soit reconnu ou non ci-dessous.
  // g (global) : retire aussi une éventuelle répétition du même marqueur,
  // pas seulement la première occurrence comme avant.
  const MARQUEUR_INTERNE = /\[[A-ZÀ-Ÿ_]{3,}(?::[^\]]*)?\]/g;
  const finalClientText = replyText.replace(MARQUEUR_INTERNE, '').replace(/\s{2,}/g, ' ').trim();

  if (finalClientText) {
    if (wasVoiceMessage) {
      await generateAndSendVoiceReply(phoneNumberId, from, finalClientText);
    }
    await sendWhatsAppText(phoneNumberId, from, finalClientText);
    console.log('DEBUG - OUTBOUND TEXT', { messageId, text: finalClientText });
  }

  // Marqueur [BESOIN_WOLOF] émis par GPT : signal FAIBLE (un faux positif a été
  // observé sur un simple "salamalekoum"), retenu seul uniquement sur un vocal
  // ou si le message mentionne explicitement la langue — voir
  // isGptWolofSignalCredible. Le cas texte explicite est déjà traité plus haut,
  // de façon déterministe, avant GPT.
  if (isGptWolofSignalCredible({ langueBlocageMatch, wasVoiceMessage, text })) {
    await notifyMerchantWolofNeed({
      merchant, phoneNumberId, from, messageId,
      strong: false, source: 'marqueur GPT seul', clientMessage: text,
    });
  } else if (langueBlocageMatch) {
    // Rendu visible pour mesurer, pendant le pilote, ces faux positifs évités.
    console.log('DEBUG - Marqueur BESOIN_WOLOF ignoré (message texte sans mention de la langue)', { messageId, from });
  }

  if (propositionMatch) {
    const pending = {};
    propositionMatch[1].split(';').forEach(pair => {
      const [k, v] = pair.split('=').map(s => s && s.trim());
      if (k && v) {
        const field = FIELD_MAP[k.toLowerCase()];
        if (field) pending[field] = v;
      }
    });
    if (Object.keys(pending).length > 0) {
      state.pendingProposals = pending;
    }
  }

  if (mentionedProduct) {
    if (mentionedProduct.colorVariants) {
      let instruction = photoMatch ? photoMatch[1].trim() : null;

      if (!instruction && detectedColor) {
        const isPhotoColor = mentionedProduct.colorVariants.some(c => c.color === detectedColor);
        if (isPhotoColor) {
          instruction = `couleur=${detectedColor}`;
        }
      }

      if (instruction === 'toutes') {
        for (const variant of mentionedProduct.colorVariants) {
          await sendWhatsAppImage(
            phoneNumberId, from, variant.imageUrl,
            `${mentionedProduct.name} (${variant.label || variant.color}) — ${mentionedProduct.price}`
          );
          console.log('DEBUG - OUTBOUND IMAGE', { messageId, imageUrl: variant.imageUrl });
        }
      } else if (instruction && instruction.startsWith('couleur=')) {
        const colorName = instruction.replace('couleur=', '').trim();
        const matchedColor = mentionedProduct.colorVariants.find(c =>
          normalizeText(c.color) === normalizeText(colorName)
        );
        if (matchedColor) {
          await sendWhatsAppImage(
            phoneNumberId, from, matchedColor.imageUrl,
            `${mentionedProduct.name} (${matchedColor.label || matchedColor.color}) — ${mentionedProduct.price}`
          );
          console.log('DEBUG - OUTBOUND IMAGE', { messageId, imageUrl: matchedColor.imageUrl });
        }
      }
    } else {
      await sendWhatsAppImage(phoneNumberId, from, mentionedProduct.imageUrl, `${mentionedProduct.name} — ${mentionedProduct.price}`);
      console.log('DEBUG - OUTBOUND IMAGE', { messageId, imageUrl: mentionedProduct.imageUrl });
    }
  }

  if (orderMatch && merchant.ownerWhatsApp) {
    if (wasVoiceMessage) {
      console.log('DEBUG - [COMMANDE] détecté sur un message vocal non confirmé par écrit : notification bloquée par sécurité.');
    } else {
      console.log('DEBUG - STATE AVANT VALIDATION:', JSON.stringify(state));
      const validation = validateOrder(state, merchant);
      console.log('DEBUG - ORDER VALIDATION', {
        messageId,
        valid: validation.valid,
        order: validation.valid ? validation.orderDetails : validation.errors,
      });

      if (validation.valid) {
        const d = validation.orderDetails;
        const notifResponse = await fetchWithTimeout(`https://graph.facebook.com/v21.0/${phoneNumberId}/messages`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${process.env.WHATSAPP_ACCESS_TOKEN}` },
          body: JSON.stringify({
            messaging_product: 'whatsapp',
            to: merchant.ownerWhatsApp,
            text: { body: `🛍️ Nouvelle commande !\nProduit: ${d.product}\nTaille: ${d.size || 'aucune'}\nCouleur: ${d.color || 'aucune'}\nZone: ${d.zone}\nTotal: ${d.total} FCFA\nClient: ${from}\n\nVérifiez votre app Wave pour confirmer la réception du paiement avant de valider.` },
          }),
        });
        console.log('DEBUG - MERCHANT NOTIFICATION', { messageId, sent: notifResponse.ok, order: d });
        if (notifResponse.ok) {
          state.orderSent = true;
        } else {
          console.error('DEBUG - Échec envoi notification commerçant:', await notifResponse.text());
        }
      } else {
        console.log('DEBUG - Commande refusée par validateOrder:', validation.errors.join(', '));
        await sendWhatsAppText(
          phoneNumberId,
          from,
          "Il me manque encore une information pour finaliser votre commande — pouvez-vous confirmer le produit, la taille, la couleur et votre zone de livraison ? 🙏"
        );
      }
    }
  }

  console.log('DEBUG - STATE AFTER', { messageId, state });
}

// ---------------------------------------------------------------------------
// HANDLER
// ---------------------------------------------------------------------------
// ---------------------------------------------------------------------------
// TRAITEMENT COMPLET D'UN WEBHOOK ENTRANT — exécuté en arrière-plan via
// waitUntil(), APRÈS que la réponse 200 a déjà été envoyée à Meta.
// ---------------------------------------------------------------------------
// Pourquoi : plusieurs guides d'intégration WhatsApp Cloud API rapportent que
// Meta attend un accusé de réception (HTTP 200) en quelques secondes — passé
// ce délai, la livraison est considérée en échec et remise en file de retry,
// MÊME si notre serveur finit par répondre 200 un peu plus tard. Le pipeline
// de NOXA (transcription + GPT + TTS + envoi photo + notification) peut
// dépasser ce délai. On accuse donc réception immédiatement dans le handler,
// et tout le vrai traitement se fait ici, après coup.
// Conséquence assumée : Meta ne renverra plus jamais un message pour nous en
// cas d'erreur de notre côté (puisqu'on a déjà répondu 200) — c'est pour ça
// que l'idempotence et la file d'attente ci-dessus deviennent le SEUL filet
// de sécurité, pas un confort en plus du retry de Meta.
async function handleIncomingMessage(body) {
  try {
    const change = body.entry?.[0]?.changes?.[0];
    const message = change?.value?.messages?.[0];
    const phoneNumberId = change?.value?.metadata?.phone_number_id;

    if (!message) return;
    const from = message.from;
    const messageId = message.id;
    const startedAt = Date.now();

    if (['image', 'sticker', 'location', 'document'].includes(message.type)) {
      await sendWhatsAppText(phoneNumberId, from, "Je ne peux pas encore traiter ce type de message. Dites-moi en quelques mots ce qui vous intéresse ! 😊");
      return;
    }

    let text = message.text?.body;
    let wasVoiceMessage = false;

    if (message.type === 'audio' && message.audio?.id) {
      const asrResult = await transcribeWhatsAppAudio(message.audio.id);
      if (!asrResult.text) {
        // Échec total : même le repli Whisper-FR n'a rien donné. Log dédié
        // et comptable — jusqu'ici invisible dans les métriques du pilote.
        console.log('DEBUG - ASR ÉCHEC TOTAL', { messageId, requestedProvider: ASR_PROVIDER });
        await sendWhatsAppText(phoneNumberId, from, "Je n'ai pas bien compris votre message vocal, pouvez-vous réessayer ou l'écrire en texte ?");
        return;
      }
      wasVoiceMessage = true;
      text = formatVoiceMessage(asrResult);
    }
    if (!text) return;

    const merchant = MERCHANTS[phoneNumberId];
    if (!merchant) return;

    const conversationKey = `${phoneNumberId}_${from}`;

    console.log('DEBUG - PROCESS START', { messageId, conversationKey, type: wasVoiceMessage ? 'voice' : 'text' });

    // --- Verrou par conversation (tentatives courtes) ---
    const lockToken = await acquireLock(conversationKey);

    if (!lockToken) {
      // Le verrou est occupé après quelques tentatives courtes : on ne
      // renonce PAS au message — on le met en file, celui qui détient le
      // verrou le traitera juste avant de le relâcher.
      await pushPendingMessage(conversationKey, { messageId, from, text, wasVoiceMessage });
      console.log(`DEBUG - QUEUE PUSH ${conversationKey} messageId=${messageId}`);
      return;
    }

    try {
      // --- Traiter d'abord le message qui a déclenché cette invocation ---
      // Même filet que pour un message de file : si le traitement échoue en
      // cours de route, on ne le laisse pas disparaître silencieusement — on
      // le remet en tête de file pour qu'un futur message de cette
      // conversation le retente, exactement comme pour QUEUE DRAIN ci-dessous.
      if (!(await isMessageAlreadyProcessed(messageId))) {
        try {
          const state = await loadState(conversationKey);
          const history = await loadHistory(conversationKey);

          await processMessage({ merchant, phoneNumberId, from, text, wasVoiceMessage, state, history, messageId });

          await saveState(conversationKey, state);
          await saveHistory(conversationKey, history);
          await markMessageProcessed(messageId);
          console.log('DEBUG - DONE', { messageId });
        } catch (initialError) {
          console.error(`DEBUG - Échec traitement message initial ${messageId}, remis en attente:`, initialError);
          await requeueFront(conversationKey, { messageId, from, text, wasVoiceMessage });
        }
      } else {
        console.log('DEBUG - Message déjà traité, ignoré:', messageId);
      }

      // --- Vider la file de cette conversation avant de relâcher le verrou ---
      // Traite dans l'ordre d'arrivée tout message qu'un autre appel aurait
      // mis en attente pendant qu'on était occupé.
      // Important : LPOP retire immédiatement l'élément de Redis. Si le
      // traitement échoue APRÈS ce retrait, le message serait perdu pour de
      // bon (Meta a déjà reçu son 200, il ne le renverra pas) — donc en cas
      // d'échec, on le remet en tête de file avant d'arrêter le drain, pour
      // qu'un futur message de cette conversation le retente.
      let queuedItem = await popPendingMessage(conversationKey);
      while (queuedItem) {
        console.log(`DEBUG - QUEUE DRAIN ${conversationKey} messageId=${queuedItem.messageId}`);
        if (!(await isMessageAlreadyProcessed(queuedItem.messageId))) {
          try {
            const qState = await loadState(conversationKey);
            const qHistory = await loadHistory(conversationKey);

            await processMessage({
              merchant,
              phoneNumberId,
              from: queuedItem.from,
              text: queuedItem.text,
              wasVoiceMessage: queuedItem.wasVoiceMessage,
              state: qState,
              history: qHistory,
              messageId: queuedItem.messageId,
            });

            await saveState(conversationKey, qState);
            await saveHistory(conversationKey, qHistory);
            await markMessageProcessed(queuedItem.messageId);
            console.log('DEBUG - DONE', { messageId: queuedItem.messageId });
          } catch (queueError) {
            console.error(`DEBUG - Échec traitement message en file ${queuedItem.messageId}, remis en attente:`, queueError);
            await requeueFront(conversationKey, queuedItem);
            break;
          }
        }
        queuedItem = await popPendingMessage(conversationKey);
      }
    } finally {
      await releaseLock(conversationKey, lockToken);
    }

    console.log('DEBUG - PROCESS END', { messageId, conversationKey, durationMs: Date.now() - startedAt });
  } catch (error) {
    // Puisque Meta a déjà reçu son 200, cette erreur ne déclenchera AUCUN
    // retry de sa part — ce log est la seule trace qui restera de l'échec.
    // À surveiller manuellement (ou via une alerte) pendant le pilote.
    console.error('DEBUG - Erreur traitement arrière-plan:', error);
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
    // Accusé de réception IMMÉDIAT, avant tout traitement — voir le
    // commentaire au-dessus de handleIncomingMessage pour la raison. Le
    // vrai traitement continue en arrière-plan via waitUntil, qui garde
    // l'invocation Vercel active au-delà de la réponse HTTP (permis par
    // Fluid Compute, jusqu'à la durée max configurée pour la fonction).
    waitUntil(handleIncomingMessage(req.body));
    return res.status(200).send('OK');
  }

  return res.status(405).send('Method Not Allowed');
}