"""
Phase A — test du service MMS isolé, AVANT toute intégration à NOXA.

Rejoue les mêmes vocaux que le benchmark Colab contre le service HTTP déployé,
et compare : transcription obtenue, latence, erreurs éventuelles.

Usage :
    python test_phase_a.py --url https://ton-service.onrender.com --folder ./vocaux

Le dossier --folder doit contenir les convX.csv (colonne 'Vocal client (fichier)'
et 'Transcription MMS' pour comparaison) et les .ogg correspondants.
"""

import argparse
import glob
import os
import time

import pandas as pd
import requests


def transcribe_via_service(base_url, audio_path, timeout=300):
    with open(audio_path, "rb") as f:
        files = {"file": (os.path.basename(audio_path), f, "audio/ogg")}
        started = time.time()
        response = requests.post(f"{base_url}/transcribe", files=files, timeout=timeout)
        elapsed_ms = int((time.time() - started) * 1000)

    if response.status_code != 200:
        return None, elapsed_ms, f"[ERREUR HTTP {response.status_code}: {response.text}]"

    data = response.json()
    return data.get("text", ""), elapsed_ms, None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True, help="URL de base du service (ex: https://xxx.onrender.com)")
    parser.add_argument("--folder", required=True, help="Dossier contenant convX.csv + les .ogg")
    parser.add_argument("--col-fichier", default="Vocal client (fichier)")
    parser.add_argument("--col-mms-colab", default="Transcription MMS", help="Colonne des résultats Colab, pour comparaison")
    args = parser.parse_args()

    print(f"Vérification du service : {args.url}/health")
    health = requests.get(f"{args.url}/health", timeout=30)
    print(health.json())

    csv_files = sorted(glob.glob(os.path.join(args.folder, "conv*.csv")))
    print(f"\n{len(csv_files)} fichier(s) CSV trouvé(s)")

    all_results = []

    for csv_path in csv_files:
        df = pd.read_csv(csv_path)
        for _, row in df.iterrows():
            audio_filename = str(row[args.col_fichier]).strip()
            audio_path = os.path.join(args.folder, audio_filename)

            if not os.path.exists(audio_path):
                print(f"  [MANQUANT] {audio_filename}")
                continue

            print(f"  Transcription de {audio_filename}...")
            text, elapsed_ms, error = transcribe_via_service(args.url, audio_path)

            colab_result = row.get(args.col_mms_colab, "")

            all_results.append({
                "fichier": audio_filename,
                "transcription_service": text if text is not None else error,
                "transcription_colab": colab_result,
                "identique_colab": (text == colab_result) if text is not None else False,
                "latence_ms": elapsed_ms,
                "erreur": error,
            })

    df_results = pd.DataFrame(all_results)

    print("\n=== Résumé ===")
    print(f"Total testé : {len(df_results)}")
    print(f"Erreurs : {df_results['erreur'].notna().sum()}")
    print(f"Identiques à Colab : {df_results['identique_colab'].sum()}")
    print(f"Latence moyenne : {df_results['latence_ms'].mean():.0f} ms")
    print(f"Latence max : {df_results['latence_ms'].max():.0f} ms")

    output_path = os.path.join(args.folder, "phase_a_resultats.csv")
    df_results.to_csv(output_path, index=False, encoding="utf-8-sig")
    print(f"\nRésultats détaillés sauvegardés dans : {output_path}")


if __name__ == "__main__":
    main()
