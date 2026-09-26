"""
Test de concurrence pour le service XLS-R sur Render.

Envoie 1, puis 2, puis 3 requêtes VRAIMENT simultanées (via des threads),
répété sur plusieurs tours, pour voir si la latence individuelle se
dégrade avec le nombre de clients simultanés — la question la plus proche
de l'usage réel de NOXA (plusieurs commerçants qui envoient un vocal à peu
près au même moment).

Usage :
    python benchmark_concurrence_xlsr.py --url https://noxa-xlsr-asr.onrender.com --folder ./Vocaux2
"""

import argparse
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd
import requests

# Fichiers utilisés, dans l'ordre — le niveau N pioche les N premiers.
FICHIERS = [
    "conv1_tour1.ogg",
    "conv3_tour1.ogg",
    "conv6_tour1.ogg",
]

NIVEAUX_CONCURRENCE = [1, 2, 3]
TOURS_PAR_NIVEAU = 3  # répétitions, pour ne pas tirer une conclusion d'un seul essai


def transcrire(url, chemin_audio, timeout=120):
    nom = os.path.basename(chemin_audio)
    with open(chemin_audio, "rb") as f:
        files = {"file": (nom, f, "audio/ogg")}
        debut = time.time()
        try:
            reponse = requests.post(f"{url}/transcribe", files=files, timeout=timeout)
        except requests.exceptions.RequestException as e:
            return {"fichier": nom, "duration_ms": None, "erreur": str(e), "start": debut, "end": time.time()}

    fin = time.time()
    if reponse.status_code != 200:
        return {"fichier": nom, "duration_ms": None,
                "erreur": f"HTTP {reponse.status_code}: {reponse.text}", "start": debut, "end": fin}

    data = reponse.json()
    return {"fichier": nom, "duration_ms": data.get("duration_ms"), "erreur": None, "start": debut, "end": fin}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True)
    parser.add_argument("--folder", required=True)
    args = parser.parse_args()

    print(f"Vérification du service : {args.url}/health")
    try:
        print(requests.get(f"{args.url}/health", timeout=30).json())
    except Exception as e:
        print(f"ATTENTION : /health injoignable ({e})")

    resultats = []

    for niveau in NIVEAUX_CONCURRENCE:
        fichiers_niveau = [os.path.join(args.folder, FICHIERS[i % len(FICHIERS)]) for i in range(niveau)]

        for tour in range(1, TOURS_PAR_NIVEAU + 1):
            print(f"\n--- Niveau {niveau} requête(s) simultanée(s), tour {tour}/{TOURS_PAR_NIVEAU} ---")

            debut_lot = time.time()
            with ThreadPoolExecutor(max_workers=niveau) as executor:
                futures = [executor.submit(transcrire, args.url, chemin) for chemin in fichiers_niveau]
                lot = [f.result() for f in as_completed(futures)]
            duree_lot_ms = int((time.time() - debut_lot) * 1000)

            for r in lot:
                r["niveau"] = niveau
                r["tour"] = tour
                r["duree_lot_ms"] = duree_lot_ms
                resultats.append(r)
                if r["erreur"]:
                    print(f"  [{r['fichier']}] ERREUR : {r['erreur']}")
                else:
                    print(f"  [{r['fichier']}] {r['duration_ms']} ms")

            print(f"  → Lot complet en {duree_lot_ms} ms (mur-à-mur, {niveau} requête(s))")

    df = pd.DataFrame(resultats)
    sortie = os.path.join(args.folder, "benchmark_concurrence.csv")
    df.to_csv(sortie, index=False, encoding="utf-8-sig")

    print(f"\n{'='*60}\nRÉSUMÉ\n{'='*60}")
    print(f"Résultats sauvegardés dans : {sortie}\n")

    reussis = df[df["erreur"].isna()]
    for niveau in NIVEAUX_CONCURRENCE:
        sous_ensemble = reussis[reussis["niveau"] == niveau]
        duree_lots = df[df["niveau"] == niveau]["duree_lot_ms"].unique()
        if len(sous_ensemble) > 0:
            print(f"Niveau {niveau} (n={len(sous_ensemble)} requêtes réussies sur {TOURS_PAR_NIVEAU} tour(s)) :")
            print(f"  duration_ms individuel — moyenne={sous_ensemble['duration_ms'].mean():.0f}, "
                  f"min={sous_ensemble['duration_ms'].min():.0f}, max={sous_ensemble['duration_ms'].max():.0f}")
            print(f"  durée du lot (mur-à-mur) par tour : {[int(d) for d in duree_lots]}")

    erreurs = df[df["erreur"].notna()]
    if len(erreurs) > 0:
        print(f"\n{len(erreurs)} erreur(s) — voir le CSV pour le détail.")

    print("\nLecture : si duration_ms individuel augmente nettement avec le niveau,")
    print("les requêtes se gênent (CPU/mémoire partagés). Si ça reste stable,")
    print("le service absorbe la concurrence sans dégradation notable sur cet échantillon.")


if __name__ == "__main__":
    main()
