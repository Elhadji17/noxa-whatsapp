"""
Test de concurrence contrôlé pour XLS-R sur Render — même fichier répété
N fois à chaque niveau, pour isoler l'effet de la concurrence de celui de
la variation de durée entre fichiers (biais identifié dans le test
précédent, où niveau 2 et niveau 3 utilisaient des fichiers différents).

Si le service sérialise vraiment les requêtes, on attend :
    1 × A ≈ T
    2 × A ≈ 2T
    3 × A ≈ 3T
(T = temps de traitement d'un seul appel à ce fichier)

Usage :
    python benchmark_concurrence_meme_fichier.py --url https://noxa-xlsr-asr.onrender.com --fichier ./Vocaux2/conv1_tour1.ogg
"""

import argparse
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd
import requests

NIVEAUX_CONCURRENCE = [1, 2, 3]
TOURS_PAR_NIVEAU = 3


def transcrire(url, chemin_audio, timeout=120):
    nom = os.path.basename(chemin_audio)
    with open(chemin_audio, "rb") as f:
        files = {"file": (nom, f, "audio/ogg")}
        debut = time.time()
        try:
            reponse = requests.post(f"{url}/transcribe", files=files, timeout=timeout)
        except requests.exceptions.RequestException as e:
            return {"duration_ms": None, "erreur": str(e), "start": debut, "end": time.time()}

    fin = time.time()
    if reponse.status_code != 200:
        return {"duration_ms": None, "erreur": f"HTTP {reponse.status_code}: {reponse.text}",
                "start": debut, "end": fin}

    data = reponse.json()
    return {"duration_ms": data.get("duration_ms"), "erreur": None, "start": debut, "end": fin}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True)
    parser.add_argument("--fichier", required=True, help="Un seul fichier, réutilisé à chaque appel")
    args = parser.parse_args()

    print(f"Vérification du service : {args.url}/health")
    try:
        print(requests.get(f"{args.url}/health", timeout=30).json())
    except Exception as e:
        print(f"ATTENTION : /health injoignable ({e})")

    print(f"Fichier utilisé pour tous les appels : {args.fichier}\n")

    resultats = []

    for niveau in NIVEAUX_CONCURRENCE:
        for tour in range(1, TOURS_PAR_NIVEAU + 1):
            print(f"--- Niveau {niveau} requête(s) simultanée(s), tour {tour}/{TOURS_PAR_NIVEAU} ---")

            debut_lot = time.time()
            with ThreadPoolExecutor(max_workers=niveau) as executor:
                futures = [executor.submit(transcrire, args.url, args.fichier) for _ in range(niveau)]
                lot = [f.result() for f in as_completed(futures)]
            duree_lot_ms = int((time.time() - debut_lot) * 1000)

            for idx, r in enumerate(lot):
                client_s = r["end"] - r["start"]
                serveur_s = (r["duration_ms"] / 1000) if r["duration_ms"] else None
                attente_s = (client_s - serveur_s) if serveur_s is not None else None

                r["niveau"] = niveau
                r["tour"] = tour
                r["duree_lot_ms"] = duree_lot_ms
                r["client_s"] = round(client_s, 2)
                r["attente_estimee_s"] = round(attente_s, 2) if attente_s is not None else None
                resultats.append(r)

                if r["erreur"]:
                    print(f"  #{idx+1} ERREUR : {r['erreur']}")
                else:
                    print(f"  #{idx+1} serveur={r['duration_ms']} ms | "
                          f"client={r['client_s']}s | attente estimée≈{r['attente_estimee_s']}s")

            print(f"  → Lot complet en {duree_lot_ms} ms\n")

    df = pd.DataFrame(resultats)
    sortie = os.path.join(os.path.dirname(args.fichier) or ".", "benchmark_concurrence_meme_fichier.csv")
    df.to_csv(sortie, index=False, encoding="utf-8-sig")

    print(f"{'='*60}\nRÉSUMÉ\n{'='*60}")
    print(f"Résultats sauvegardés dans : {sortie}\n")

    reussis = df[df["erreur"].isna()]
    for niveau in NIVEAUX_CONCURRENCE:
        sous_ensemble = reussis[reussis["niveau"] == niveau]
        duree_lots = df[df["niveau"] == niveau]["duree_lot_ms"].unique()
        if len(sous_ensemble) > 0:
            print(f"Niveau {niveau} :")
            print(f"  duration_ms serveur — moyenne={sous_ensemble['duration_ms'].mean():.0f}")
            print(f"  attente estimée — moyenne={sous_ensemble['attente_estimee_s'].mean():.2f}s")
            print(f"  durée du lot (mur-à-mur) par tour : {[int(d) for d in duree_lots]}")

    n1 = reussis[reussis["niveau"] == 1]["duration_ms"].mean()
    print(f"\nSi sérialisation stricte, on attend : lot(niveau N) ≈ N × {n1:.0f} ms")
    for niveau in (2, 3):
        lots = df[df["niveau"] == niveau]["duree_lot_ms"].unique()
        attendu = niveau * n1
        print(f"  Niveau {niveau} : lots observés {[int(d) for d in lots]} ms, "
              f"attendu ≈{attendu:.0f} ms")


if __name__ == "__main__":
    main()
