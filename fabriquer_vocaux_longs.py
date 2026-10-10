#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Assemble plusieurs vocaux de ton benchmark en fichiers WAV de durées choisies (10 s, 20 s, 30 s, 45 s...), pour
tester ce que devient la mémoire du service ASR avec des vocaux LONGS. Les fichiers sont à 48 kHz mono 16 bits : la
fréquence des vocaux WhatsApp, donc le service les rééchantillonne comme un vrai vocal.

Aucun prérequis obligatoire. Le script essaie de décoder tes vrais vocaux avec soundfile (pip install soundfile) puis
ffmpeg. S'il n'en trouve aucun, il fabrique à la place du BRUIT synthétique de la même durée : c'est suffisant pour tester
la mémoire (elle dépend de la durée de l'audio, pas de son contenu), le texte renvoyé par le service sera juste
sans intérêt. --synthetique force ce mode.

USAGE
  python fabriquer_vocaux_longs.py --dossier C:\\Users\\user\\noxa-whatsapp\\Vocaux2 --durees 10,20,30,45 --sortie longs
  python fabriquer_vocaux_longs.py --synthetique --durees 10,20,30,45 --sortie longs      (sans aucun vocal ni décodeur)

Produit  longs\\long_10s.wav, long_20s.wav, ...  Le service refuse par défaut les fichiers de plus de 10 Mo
(MAX_FILE_SIZE_BYTES) : à 48 kHz, cela fait environ 100 s ; le script prévient avant d'aller au-delà.
"""

import argparse
import os
import shutil
import subprocess
import sys
import wave

FREQUENCE = 48000
LIMITE_OCTETS = 10 * 1024 * 1024


class ErreurFabrication(Exception):
    pass


RAISONS = {}      # pourquoi chaque décodeur n'a pas marché (pour le message final)


def decoder_soundfile(chemin):
    """Renvoie des octets PCM 16 bits mono à 48 kHz, ou None si soundfile est absent ou ne sait pas lire le fichier."""
    try:
        import numpy as np
        import soundfile as sf
    except ImportError:
        RAISONS["soundfile"] = "non installé"
        return None
    try:
        donnees, frequence = sf.read(chemin, dtype="int16", always_2d=True)
    except Exception as e:
        RAISONS["soundfile"] = f"ne sait pas lire ce format ({str(e).splitlines()[0][:80]})"
        return None
    if frequence != FREQUENCE:
        RAISONS["soundfile"] = f"fréquence {frequence} Hz au lieu de {FREQUENCE}"
        return None            # on laisse ffmpeg rééchantillonner proprement
    return np.ascontiguousarray(donnees[:, 0]).tobytes()


def decoder_ffmpeg(chemin):
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        RAISONS["ffmpeg"] = "introuvable dans le PATH"
        return None
    r = subprocess.run([ffmpeg, "-nostdin", "-loglevel", "error", "-i", chemin, "-f", "s16le", "-ac", "1", "-ar", str(FREQUENCE), "pipe:1"],
                       capture_output=True)
    if r.returncode != 0 or not r.stdout:
        RAISONS["ffmpeg"] = "n'a pas pu lire le fichier"
        return None
    return r.stdout


def decoder(chemin, decodeurs=None):
    for d in (decodeurs or [decoder_soundfile, decoder_ffmpeg]):
        octets = d(chemin)
        if octets:
            return octets
    return None


def bruit_synthetique(secondes):
    """Bruit blanc 16 bits mono à 48 kHz de la durée demandée. Le modèle en tire du texte sans intérêt, mais son
    empreinte mémoire et son temps de calcul ne dépendent que de la durée."""
    return os.urandom(int(secondes * FREQUENCE) * 2)


def lister_vocaux(dossier):
    if not os.path.isdir(dossier):
        raise ErreurFabrication(f"Dossier introuvable : {dossier}")
    fichiers = sorted(f for f in os.listdir(dossier) if f.lower().endswith((".ogg", ".opus", ".wav", ".mp3", ".m4a")))
    if not fichiers:
        raise ErreurFabrication(f"Aucun fichier audio dans {dossier}")
    return [os.path.join(dossier, f) for f in fichiers]


def fabriquer(dossier, durees, sortie, decodeurs=None, synthetique=False):
    """Écrit un WAV par durée cible. Renvoie la liste (chemin, durée réelle en s, taille en octets).
    Sans décodeur (ou avec synthetique=True), le contenu est du bruit : valable pour tester la mémoire."""
    pcm = []
    if not synthetique and dossier:
        RAISONS.clear()
        for chemin in lister_vocaux(dossier):
            octets = decoder(chemin, decodeurs)
            if octets:
                pcm.append(octets)
        if not pcm:
            raison = " ; ".join(f"{k} : {v}" for k, v in RAISONS.items()) or "aucun décodeur disponible"
            print(f"⚠️  Impossible de décoder tes vocaux ({raison}).\n"
                  f"   Je fabrique du BRUIT synthétique à la place : suffisant pour tester la mémoire, le texte renvoyé sera sans intérêt.\n"
                  f"   (Pour utiliser tes vrais vocaux : pip install soundfile)")
            synthetique = True
    elif not dossier:
        synthetique = True
    os.makedirs(sortie, exist_ok=True)
    resultats = []
    for cible in durees:
        octets_cibles = int(cible * FREQUENCE) * 2
        if octets_cibles + 44 > LIMITE_OCTETS:
            print(f"⚠️  {cible} s dépasse 10 Mo : le service refusera ce fichier tant que MAX_FILE_SIZE_BYTES n'est pas augmenté.")
        if synthetique:
            donnees = bruit_synthetique(cible)
        else:
            morceaux, total, i = [], 0, 0
            while total < octets_cibles:
                m = pcm[i % len(pcm)]
                morceaux.append(m)
                total += len(m)
                i += 1
            donnees = b"".join(morceaux)[:octets_cibles]
        chemin = os.path.join(sortie, f"long_{int(cible)}s.wav")
        with wave.open(chemin, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(FREQUENCE)
            w.writeframes(donnees)
        resultats.append((chemin, len(donnees) / 2 / FREQUENCE, os.path.getsize(chemin)))
    return resultats


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--dossier", help="dossier contenant les vocaux (ex. Vocaux2) ; facultatif avec --synthetique")
    p.add_argument("--synthetique", action="store_true", help="fabrique du bruit au lieu d'assembler tes vocaux (aucun décodeur nécessaire)")
    p.add_argument("--durees", default="10,20,30,45", help="durées cibles en secondes, séparées par des virgules")
    p.add_argument("--sortie", default="longs")
    a = p.parse_args(argv)
    try:
        try:
            durees = [float(x) for x in a.durees.split(",") if x.strip()]
        except ValueError:
            raise ErreurFabrication(f"--durees attend des nombres, par exemple 10,20,30,45 (reçu : {a.durees!r})")
        if not durees or any(d <= 0 for d in durees):
            raise ErreurFabrication("--durees attend des nombres positifs, par exemple 10,20,30,45")
        if not a.dossier and not a.synthetique:
            raise ErreurFabrication("Donne --dossier (tes vocaux) ou --synthetique (du bruit).")
        for chemin, duree, taille in fabriquer(a.dossier, durees, a.sortie, synthetique=a.synthetique):
            print(f"{chemin} : {duree:.1f} s, {taille / 1e6:.2f} Mo")
    except (ErreurFabrication, ValueError) as e:
        print(f"ERREUR : {e}")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
