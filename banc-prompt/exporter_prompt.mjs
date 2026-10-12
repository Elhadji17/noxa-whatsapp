#!/usr/bin/env node
// Sort en texte le prompt système RÉEL de ton webhook pour un marchand donné, exactement celui qu'enverrait la production.
//
// Usage (depuis la racine de ton dépôt, là où se trouve node_modules) :
//   node exporter_prompt.mjs api/whatsapp-webhook.js "AD Shoes" > prompts/A_actuel.txt
// Pour inclure la consigne « deux transcriptions » (mode dual), exporte avec la même variable que la production :
//   ASR_PROVIDER=dual node exporter_prompt.mjs api/whatsapp-webhook.js "AD Shoes" > prompts/A_actuel.txt
//
// Comment ça marche : le webhook n'exporte pas ses fonctions. Le script en fait une copie temporaire À CÔTÉ du fichier
// (pour que ses imports trouvent node_modules), y ajoute un export, la charge, affiche le prompt, puis supprime la copie.
// Ton fichier n'est jamais modifié. Le webhook peut afficher un avertissement Redis si les variables d'environnement
// manquent : ce n'est pas gênant, le prompt est construit au chargement du fichier.
import fs from 'node:fs';
import path from 'node:path';
import { pathToFileURL } from 'node:url';

const [, , fichier, marchand] = process.argv;
if (!fichier || !marchand) {
  console.error('Usage : node exporter_prompt.mjs CHEMIN/whatsapp-webhook.js "Nom du marchand" > prompt.txt');
  process.exit(2);
}
if (!fs.existsSync(fichier)) {
  console.error(`Fichier introuvable : ${fichier}`);
  process.exit(2);
}
const source = fs.readFileSync(fichier, 'utf8');
const copie = path.join(path.dirname(path.resolve(fichier)), `.export_prompt_${process.pid}.mjs`);
fs.writeFileSync(copie, `${source}\nexport { MERCHANTS };\n`);
let code = 0;
try {
  const module = await import(pathToFileURL(copie).href);
  const marchands = Object.values(module.MERCHANTS);
  const trouve = marchands.find((m) => m.name === marchand);
  if (!trouve) {
    console.error(`Marchand introuvable : « ${marchand} ». Marchands disponibles : ${marchands.map((m) => `« ${m.name} »`).join(', ')}`);
    code = 3;   // pas de process.exit ici : il empêcherait le nettoyage ci-dessous
  } else {
    process.stdout.write(trouve.systemPrompt);
  }
} finally {
  fs.rmSync(copie, { force: true });   // la copie temporaire est TOUJOURS supprimée, même en cas d'erreur
}
process.exitCode = code;
