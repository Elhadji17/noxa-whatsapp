export default async function handler(req, res) {
  if (req.method !== 'POST') {
    return res.status(405).send('Method Not Allowed');
  }

  try {
    const { businessName, sector, whatsapp, contactName } = req.body;

    if (!businessName || !sector || !whatsapp || !contactName) {
      return res.status(400).json({ error: 'Champs manquants' });
    }

    const message = `🔔 Nouvelle inscription NOXA
Commerce : ${businessName}
Secteur : ${sector}
Contact : ${contactName}
WhatsApp : ${whatsapp}`;

    await fetch(
      `https://graph.facebook.com/v21.0/${process.env.WHATSAPP_PHONE_NUMBER_ID}/messages`,
      {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${process.env.WHATSAPP_ACCESS_TOKEN}`,
        },
        body: JSON.stringify({
          messaging_product: 'whatsapp',
          to: process.env.ELHADJI_WHATSAPP_NUMBER, // ton propre numéro, format international sans +
          text: { body: message },
        }),
      }
    );

    return res.status(200).json({ success: true });
  } catch (error) {
    console.error('Erreur inscription:', error);
    return res.status(500).json({ error: 'Erreur serveur' });
  }
}