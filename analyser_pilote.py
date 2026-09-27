"""
Analyse un export JSONL des logs Vercel (Dashboard > Logs > Export) pour
calculer les métriques de suivi du pilote NOXA à 2 commerçants.

Usage :
    python analyser_pilote.py chemin/vers/export.jsonl

Comment obtenir l'export : sur le dashboard Vercel > Logs, choisir la
période voulue, puis exporter au format JSONL (comme les extraits déjà
copiés dans cette conversation).
"""

import argparse
import json
import re
import sys
from collections import Counter
from datetime import datetime


def parser_lignes(chemin):
    lignes = []
    with open(chemin, "r", encoding="utf-8") as f:
        for ligne in f:
            ligne = ligne.strip()
            if not ligne or ligne.startswith("---") or ligne.startswith("-------"):
                continue
            try:
                lignes.append(json.loads(ligne))
            except json.JSONDecodeError:
                continue
    return lignes


def extraire_json_du_message(message):
    """Les logs DEBUG mettent souvent un objet JS après le préfixe texte,
    ex: 'DEBUG - ASR RESULT { requestedProvider: ... }'. On tente de le
    parser en étant tolérant (clés non-quotées, guillemets simples)."""
    if "{" not in message:
        return None
    brut = message[message.index("{"):]
    # Tolérance basique : quote les clés non quotées, remplace guillemets simples.
    brut = re.sub(r"(\w+):", r'"\1":', brut)
    brut = brut.replace("'", '"')
    try:
        return json.loads(brut)
    except json.JSONDecodeError:
        return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("fichier", help="Export JSONL des logs Vercel")
    args = parser.parse_args()

    lignes = parser_lignes(args.fichier)
    print(f"{len(lignes)} entrées de log chargées.\n")

    messages_vocaux = 0
    messages_texte = 0
    asr_resultats = []
    asr_echecs_totaux = 0
    erreurs_diverses = []
    commandes_validees = 0
    commandes_refusees = 0

    for l in lignes:
        msg = l.get("message", "")
        if not msg:
            continue

        if "PROCESS START" in msg:
            data = extraire_json_du_message(msg)
            if data:
                if data.get("type") == "voice":
                    messages_vocaux += 1
                elif data.get("type") == "text":
                    messages_texte += 1

        if "ASR RESULT" in msg:
            data = extraire_json_du_message(msg)
            if data:
                asr_resultats.append(data)

        if "ASR ÉCHEC TOTAL" in msg:
            asr_echecs_totaux += 1

        if l.get("level") == "error":
            erreurs_diverses.append(msg[:200])

        if "MERCHANT NOTIFICATION" in msg:
            data = extraire_json_du_message(msg)
            if data and data.get("sent"):
                commandes_validees += 1

        if "Commande refusée par validateOrder" in msg:
            commandes_refusees += 1

    print("=" * 60)
    print("VOLUME")
    print("=" * 60)
    print(f"Messages vocaux détectés (PROCESS START) : {messages_vocaux}")
    print(f"Messages texte détectés (PROCESS START)  : {messages_texte}")

    print(f"\n{'='*60}")
    print("ASR — FOURNISSEUR ET FIABILITÉ")
    print("=" * 60)
    if asr_resultats:
        compteur_provider = Counter(r.get("actualProvider") for r in asr_resultats)
        compteur_fallback = Counter(r.get("fallbackUsed") for r in asr_resultats)
        print(f"Appels ASR avec résultat (au moins un fournisseur a répondu) : {len(asr_resultats)}")
        print(f"Répartition par fournisseur réel : {dict(compteur_provider)}")
        print(f"Repli utilisé : {compteur_fallback.get(True, 0)} / {len(asr_resultats)} "
              f"({compteur_fallback.get(True, 0) / len(asr_resultats) * 100:.0f}%)")
    else:
        print("Aucun résultat ASR trouvé dans cet export.")
    print(f"Échecs totaux (même le repli a échoué) : {asr_echecs_totaux}")

    print(f"\n{'='*60}")
    print("COMMANDES")
    print("=" * 60)
    print(f"Commandes transmises au commerçant : {commandes_validees}")
    print(f"Commandes refusées par validateOrder (donnée manquante/invalide) : {commandes_refusees}")

    print(f"\n{'='*60}")
    print(f"ERREURS ({len(erreurs_diverses)} au total)")
    print("=" * 60)
    if erreurs_diverses:
        for e in erreurs_diverses[:15]:
            print(f"  - {e}")
        if len(erreurs_diverses) > 15:
            print(f"  ... et {len(erreurs_diverses) - 15} de plus (voir le fichier brut)")
    else:
        print("Aucune erreur dans cet export.")

    print(f"\n{'='*60}")
    print("À VÉRIFIER MANUELLEMENT (non automatisable depuis les logs seuls)")
    print("=" * 60)
    print("- Durée moyenne des vocaux et RTF réel : pas capturé côté webhook actuellement")
    print("- Qualité Wolof/français/code-switching : relire un échantillon de transcriptions")
    print("- Erreurs sur champs critiques (taille/prix/zone/paiement) : comparer")
    print("  les commandes transmises au commerçant à ce que le client voulait réellement")


if __name__ == "__main__":
    main()
