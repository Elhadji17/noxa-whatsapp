// api/whatsapp-webhook.js

// Vérification du webhook (Meta appelle cet endpoint en GET une seule fois,
// pour confirmer que ton URL est valide, quand tu configures le webhook)
export default async function handler(req, res) {
  if (req.method === 'GET') {
    const VERIFY_TOKEN = noxa_verify_2026_xyz; // à définir toi-même

    const mode = req.query['hub.mode'];
    const token = req.query['hub.verify_token'];
    const challenge = req.query['hub.challenge'];

    if (mode === 'subscribe' && token === VERIFY_TOKEN) {
      console.log('Webhook vérifié avec succès');
      return res.status(200).send(challenge);
    } else {
      return res.status(403).send('Verification failed');
    }
  }

  // Réception des messages entrants (Meta appelle cet endpoint en POST
  // à chaque fois qu'un client écrit sur ton numéro WhatsApp)
  if (req.method === 'POST') {
    try {
      const body = req.body;

      const entry = body.entry?.[0];
      const change = entry?.changes?.[0];
      const message = change?.value?.messages?.[0];

      if (!message) {
        // Peut être un accusé de statut (message lu/livré), pas un vrai message entrant
        return res.status(200).send('OK');
      }

      const from = message.from; // numéro du client
      const text = message.text?.body; // contenu du message

      console.log(`Message reçu de ${from} : ${text}`);

      // Étape suivante (pas encore codée ici) : envoyer "text" à OpenAI,
      // récupérer la réponse, puis la renvoyer au client via l'API WhatsApp

      return res.status(200).send('OK');
    } catch (error) {
      console.error('Erreur webhook:', error);
      return res.status(200).send('OK'); // toujours renvoyer 200 à Meta, même en cas d'erreur interne
    }
  }

  return res.status(405).send('Method Not Allowed');
}