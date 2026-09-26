"""
Benchmark cloud XLS-R sur Render — protocole validé avec Elhadji.

Envoie 10 fichiers représentatifs au service Render, deux fois de suite,
sans rien modifier entre les deux passages. Pour chaque appel : durée audio
(via librosa), temps de traitement (duration_ms renvoyé par le service),
RTF (temps de traitement / durée audio), texte transcrit.

IMPORTANT : ce script ne mesure PAS la RAM — regarde le dashboard Render
(Metrics) à la main pendant l'exécution, en particulier pendant le tout
premier appel et pendant les appels du second passage. Note "pic observé
sur la métrique Render", pas "pic RAM exact" (résolution d'échantillonnage
possiblement grossière).

Usage :
    pip install requests librosa pandas --break-system-packages  # si besoin
    python benchmark_xlsr_render.py --url https://noxa-xlsr-asr.onrender.com --folder ./Vocaux2
"""

import argparse
import os
import time

import librosa
import pandas as pd
import requests

# Sélection de 10 fichiers représentatifs (wolof pur, français, code-switching,
# nombres, tailles, prix, zones, produits) — ajuste si besoin.
FICHIERS_REPRESENTATIFS = [
    "conv1_tour1.ogg",   # wolof pur, produit
    "conv3_tour1.ogg",   # wolof pur, produit
    "conv6_tour1.ogg",   # français, produit
    "conv6_tour4.ogg",   # français, négociation
    "conv2_tour2.ogg",   # wolof pur, nombre, taille, couleur
    "conv7_tour4.ogg",   # code-switching, nombre, taille
    "conv9_tour5.ogg",   # code-switching, nombre, prix
    "conv4_tour4.ogg",   # code-switching, zone
    "conv10_tour5.ogg",  # code-switching, nombre, taille, couleur
    "conv5_tour2.ogg",   # wolof pur
]


def transcrire(url, chemin_audio, timeout=120):
    with open(chemin_audio, "rb") as f:
        files = {"file": (os.path.basename(chemin_audio), f, "audio/ogg")}
        debut = time.time()
        try:
            reponse = requests.post(f"{url}/transcribe", files=files, timeout=timeout)
        except requests.exceptions.RequestException as e:
            return None, int((time.time() - debut) * 1000), str(e), {}

    if reponse.status_code != 200:
        return None, int((time.time() - debut) * 1000), f"HTTP {reponse.status_code}: {reponse.text}", {}

    data = reponse.json()
    # Décomposition par étape, si le service la fournit (voir app.py).
    detail = {
        k: data.get(k) for k in
        ("read_ms", "decode_audio_ms", "preprocess_ms", "inference_ms", "decode_text_ms")
        if k in data
    }
    return data.get("text", ""), data.get("duration_ms"), None, detail


def percentile(valeurs, p):
    """Percentile simple (interpolation linéaire), sans dépendance numpy."""
    if not valeurs:
        return None
    valeurs_triees = sorted(valeurs)
    k = (len(valeurs_triees) - 1) * (p / 100)
    f, c = int(k), min(int(k) + 1, len(valeurs_triees) - 1)
    if f == c:
        return valeurs_triees[f]
    return valeurs_triees[f] + (valeurs_triees[c] - valeurs_triees[f]) * (k - f)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True, help="URL du service, ex: https://noxa-xlsr-asr.onrender.com")
    parser.add_argument("--folder", required=True, help="Dossier contenant les fichiers .ogg")
    parser.add_argument(
        "--fichiers", nargs="+", default=None,
        help="Liste explicite de noms de fichiers à tester (remplace FICHIERS_REPRESENTATIFS). "
             "Utile pour tester les fichiers synthétiques longs : "
             "--fichiers synthetique_10s.ogg synthetique_15s.ogg synthetique_20s.ogg synthetique_30s.ogg"
    )
    args = parser.parse_args()

    liste_fichiers = args.fichiers if args.fichiers else FICHIERS_REPRESENTATIFS

    print(f"Vérification du service : {args.url}/health")
    try:
        health = requests.get(f"{args.url}/health", timeout=30)
        print(health.json())
    except Exception as e:
        print(f"ATTENTION : /health injoignable ({e}) — le service dort peut-être encore.")

    resultats = []

    for passage in (1, 2):
        print(f"\n{'='*60}\nPASSAGE {passage}\n{'='*60}")
        for i, nom_fichier in enumerate(liste_fichiers):
            chemin = os.path.join(args.folder, nom_fichier)
            if not os.path.exists(chemin):
                print(f"  [MANQUANT] {nom_fichier}")
                continue

            duree_audio = librosa.get_duration(path=chemin)

            statut = "first_inference" if (passage == 1 and i == 0) else "warm"
            print(f"  [{passage}/{statut}] {nom_fichier} (audio: {duree_audio:.1f}s)...")

            texte, duration_ms, erreur, detail = transcrire(args.url, chemin)

            rtf = (duration_ms / 1000 / duree_audio) if (duration_ms and duree_audio > 0) else None

            ligne = {
                "passage": passage,
                "statut": statut,
                "fichier": nom_fichier,
                "duree_audio_s": round(duree_audio, 2),
                "duration_ms": duration_ms,
                "rtf": round(rtf, 3) if rtf else None,
                "texte": texte,
                "erreur": erreur,
            }
            ligne.update(detail)  # read_ms, decode_audio_ms, preprocess_ms, inference_ms, decode_text_ms
            resultats.append(ligne)

            if erreur:
                print(f"    ERREUR : {erreur}")
            else:
                print(f"    {duration_ms} ms | RTF={rtf:.3f} | {texte}")

    df = pd.DataFrame(resultats)
    sortie = os.path.join(args.folder, "benchmark_xlsr_render.csv")
    df.to_csv(sortie, index=False, encoding="utf-8-sig")

    print(f"\n{'='*60}\nRÉSUMÉ\n{'='*60}")
    print(f"Résultats sauvegardés dans : {sortie}\n")

    reussis = df[df["erreur"].isna()]
    warm = reussis[reussis["statut"] == "warm"]

    for statut in ("first_inference", "warm"):
        sous_ensemble = reussis[reussis["statut"] == statut]
        if len(sous_ensemble) > 0:
            print(f"{statut} (n={len(sous_ensemble)}) : "
                  f"duration_ms moyen={sous_ensemble['duration_ms'].mean():.0f}, "
                  f"RTF moyen (des ratios individuels)={sous_ensemble['rtf'].mean():.3f}")

    if len(warm) > 0:
        # RTF global pondéré : somme des temps / somme des durées audio.
        # Différent (et plus représentatif de la charge totale) que la
        # moyenne arithmétique des RTF individuels calculée ci-dessus.
        rtf_global = warm["duration_ms"].sum() / 1000 / warm["duree_audio_s"].sum()
        print(f"\nRTF global pondéré (warm, n={len(warm)}) : {rtf_global:.3f}")

        latences = warm["duration_ms"].tolist()
        print(f"\nLatence warm (n={len(warm)}) — À LIRE COMME \"observé sur cet "
              f"échantillon\", pas comme une propriété stable de Render (n trop "
              f"petit pour un vrai p95) :")
        print(f"  min    = {min(latences):.0f} ms")
        print(f"  p50    = {percentile(latences, 50):.0f} ms")
        print(f"  p90    = {percentile(latences, 90):.0f} ms")
        print(f"  p95    = {percentile(latences, 95):.0f} ms")
        print(f"  max    = {max(latences):.0f} ms")

        # Décomposition moyenne par étape, si disponible (nécessite le app.py
        # mis à jour avec read_ms/decode_audio_ms/preprocess_ms/inference_ms/
        # decode_text_ms).
        colonnes_detail = ["read_ms", "decode_audio_ms", "preprocess_ms", "inference_ms", "decode_text_ms"]
        if all(c in warm.columns for c in colonnes_detail) and warm[colonnes_detail].notna().all().all():
            print(f"\nDécomposition moyenne par étape (warm, n={len(warm)}) :")
            for c in colonnes_detail:
                print(f"  {c:16s} = {warm[c].mean():.0f} ms")
        else:
            print("\n(Décomposition par étape non disponible — redéploie le service "
                  "avec la version mise à jour de app.py pour l'obtenir.)")

    erreurs = df[df["erreur"].notna()]
    if len(erreurs) > 0:
        print(f"\n{len(erreurs)} erreur(s) rencontrée(s) — voir le CSV pour le détail.")


if __name__ == "__main__":
    main()
