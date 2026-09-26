"""
Génère des fichiers audio de test plus longs en concaténant plusieurs
vocaux existants (avec un court silence entre chaque), pour tester si la
latence XLS-R évolue linéairement avec la durée ou non.

Le contenu résultant n'a pas de sens linguistique (phrases différentes
mises bout à bout) — ce n'est pas voulu pour évaluer la qualité, seulement
pour faire varier la durée d'entrée et observer inference_ms en fonction
de duree_audio_s.

Usage :
    pip install librosa soundfile numpy --break-system-packages  # si besoin
    python generer_audios_longs.py --folder ./Vocaux2 --sortie ./Vocaux2/longs
"""

import argparse
import glob
import os
import random

import librosa
import numpy as np
import soundfile as sf

SR = 16000
SILENCE_ENTRE_SEGMENTS_S = 0.3

# Cibles de durée approximative à générer (en secondes).
DUREES_CIBLES = [10, 15, 20, 30]


def charger_tous_les_fichiers(dossier):
    fichiers = sorted(glob.glob(os.path.join(dossier, "*.ogg")))
    audios = []
    for f in fichiers:
        try:
            y, _ = librosa.load(f, sr=SR)
            audios.append((os.path.basename(f), y))
        except Exception as e:
            print(f"  Ignoré (illisible) : {f} ({e})")
    return audios


def construire_audio_cible(audios, duree_cible_s, rng):
    silence = np.zeros(int(SILENCE_ENTRE_SEGMENTS_S * SR), dtype=np.float32)
    morceaux = []
    noms_utilises = []
    duree_totale = 0.0

    disponibles = audios[:]
    rng.shuffle(disponibles)

    i = 0
    while duree_totale < duree_cible_s:
        if i >= len(disponibles):
            # Repasse sur la liste si on a besoin de plus de matière.
            rng.shuffle(disponibles)
            i = 0
        nom, y = disponibles[i]
        morceaux.append(y)
        morceaux.append(silence)
        noms_utilises.append(nom)
        duree_totale += len(y) / SR + SILENCE_ENTRE_SEGMENTS_S
        i += 1

    audio_concatene = np.concatenate(morceaux)
    return audio_concatene, duree_totale, noms_utilises


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--folder", required=True, help="Dossier des .ogg sources")
    parser.add_argument("--sortie", required=True, help="Dossier de sortie pour les fichiers longs")
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    os.makedirs(args.sortie, exist_ok=True)
    rng = random.Random(args.seed)

    print(f"Chargement des fichiers sources depuis {args.folder}...")
    audios = charger_tous_les_fichiers(args.folder)
    print(f"{len(audios)} fichier(s) source(s) chargé(s).")

    if not audios:
        print("Aucun fichier source trouvé — vérifie le chemin --folder.")
        return

    for duree_cible in DUREES_CIBLES:
        audio_concatene, duree_reelle, noms_utilises = construire_audio_cible(audios, duree_cible, rng)
        # WAV plutôt qu'OGG/Vorbis : écriture PCM brute, beaucoup plus fiable
        # pour ces tailles — un essai précédent en OGG a produit un fichier
        # de 3,36 Ko quasi vide pour la cible 30s, sans erreur visible. Le
        # service XLS-R détecte le format depuis le contenu réel du fichier,
        # pas depuis l'extension : rester en .ogg côté nom ne changerait rien
        # au fonctionnement, mais .wav est plus honnête sur le vrai format.
        nom_sortie = f"synthetique_{duree_cible}s.wav"
        chemin_sortie = os.path.join(args.sortie, nom_sortie)

        sf.write(chemin_sortie, audio_concatene, SR, format="WAV", subtype="PCM_16")

        # Vérification immédiate : recharge le fichier et compare sa durée
        # réelle à ce qu'on vient d'écrire, pour détecter un échec silencieux
        # d'écriture avant même de lancer le benchmark dessus.
        taille_octets = os.path.getsize(chemin_sortie)
        y_verif, _ = librosa.load(chemin_sortie, sr=SR)
        duree_verifiee = len(y_verif) / SR

        statut = "OK" if abs(duree_verifiee - duree_reelle) < 0.5 else "ANOMALIE"
        print(f"  {nom_sortie} : {duree_reelle:.1f}s attendus, {duree_verifiee:.1f}s vérifiés "
              f"({taille_octets} octets) [{statut}], "
              f"{len(noms_utilises)} segment(s) : {', '.join(noms_utilises[:3])}"
              f"{'...' if len(noms_utilises) > 3 else ''}")
        if statut == "ANOMALIE":
            print(f"    ATTENTION : durée vérifiée très différente de la durée attendue — "
                  f"fichier probablement corrompu, à ne pas utiliser pour le benchmark.")

    print(f"\nFichiers générés dans : {args.sortie}")


if __name__ == "__main__":
    main()
