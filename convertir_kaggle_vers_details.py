#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Convertit le CSV de résultats d'un benchmark fait ailleurs (Kaggle : colonnes audio, prediction, ...)
au format 'details' de nos outils (source;categorie;chemin_audio;reference;hypothese;wer;cer;latence_ms;erreur),
pour l'utiliser avec test_extraction_catalogue.py (--moteur) et les autres scripts.

Ce que fait le script
  - Les RÉFÉRENCES et les catégories viennent de details_base.csv : tous les moteurs sont ainsi notés
    contre exactement les mêmes textes.
  - Les accents abîmés (« mÃ«n » pour « mën », classique quand un fichier UTF-8 est lu comme du Windows-1252)
    sont réparés ; un texte déjà correct n'est pas touché. Le nombre de textes réparés est affiché.
  - WER et CER sont recalculés avec nos formules, par phrase. Le CER compte les espaces comme des
    caractères, comme jiwer. Ces valeurs peuvent différer de celles du fichier d'origine.
  - Les vocaux de details_base.csv absents du fichier Kaggle sont listés (le test d'extraction les écartera).

ATTENTION à une confusion fréquente : un WER « corpus » calculé sur la concaténation de toutes les phrases est
inférieur de 2 à 3 points au WER calculé phrase par phrase, et pas du même montant selon les systèmes. Pour
comparer des moteurs, utilise toujours la même méthode. Ici, c'est phrase par phrase.

USAGE
  python convertir_kaggle_vers_details.py --kaggle noxa_benchmark_progress.csv --base details_base.csv --out details_nouveau.csv
"""

import argparse
import csv
import os
import re
import sys

try:
    import benchmark_corpus_xlsr as bc
except ImportError:
    raise SystemExit("benchmark_corpus_xlsr.py doit être dans le même dossier que ce script.")

MARQUEURS_ABIMES = ("Ã", "â€", "Â")


def reparer(texte):
    """Rend leur forme correcte à un texte UTF-8 lu en Windows-1252. Renvoie (texte, a_été_réparé)."""
    if not any(m in texte for m in MARQUEURS_ABIMES):
        return texte, False
    for essai in (texte, texte.replace("Ã ", "Ã\xa0")):
        try:
            return essai.encode("cp1252").decode("utf-8"), True
        except (UnicodeEncodeError, UnicodeDecodeError):
            continue
    return texte, False


def nom_fichier(chemin):
    return re.split(r"[\\/]", str(chemin))[-1]


def lire_csv(chemin):
    with open(chemin, "rb") as f:
        brut = f.read()
    try:
        contenu = brut.decode("utf-8-sig")
    except UnicodeDecodeError:
        contenu = brut.decode("cp1252", errors="replace")
    lignes = contenu.splitlines()
    delim = ";" if lignes and lignes[0].count(";") > lignes[0].count(",") else ","
    return list(csv.DictReader(lignes, delimiter=delim))


def colonne(lignes, *noms):
    for n in noms:
        if lignes and n in lignes[0]:
            return n
    raise SystemExit(f"Aucune des colonnes {noms} dans le fichier Kaggle. Colonnes présentes : {list(lignes[0].keys()) if lignes else '(vide)'}")


def wer_cer_phrase(reference, hypothese):
    r, h = bc.normaliser(reference), bc.normaliser(hypothese)
    rm, hm = r.split(), h.split()
    w = bc.distance_levenshtein(rm, hm) / len(rm) if rm else (0.0 if not hm else 1.0)
    c = bc.distance_levenshtein(list(r), list(h)) / len(r) if r else (0.0 if not h else 1.0)
    return w, c


def convertir(chemin_kaggle, chemin_base, chemin_sortie):
    kaggle = lire_csv(chemin_kaggle)
    if not kaggle:
        raise SystemExit("Le fichier Kaggle est vide.")
    col_pred = colonne(kaggle, "prediction", "hypothese", "hypothesis", "transcription")
    try:
        col_audio = colonne(kaggle, "audio", "fichier", "file")
    except SystemExit:
        col_audio = None
    col_lat = next((n for n in ("latency_seconds", "latence_s", "latency") if n in kaggle[0]), None)

    predictions, latences, repares = {}, {}, 0
    for l in kaggle:
        nom = nom_fichier(l[col_audio]) if col_audio else f"conv{l['Conv']}_tour{l['Tour']}.ogg"
        texte, change = reparer(l[col_pred] or "")
        repares += change
        predictions[nom] = texte
        try:
            latences[nom] = float(l[col_lat]) if col_lat else None
        except (TypeError, ValueError):
            latences[nom] = None

    base = lire_csv(chemin_base)
    sorties, absents = [], []
    for r in base:
        nom = nom_fichier(r["chemin_audio"])
        if nom not in predictions:
            absents.append(nom)
            continue
        w, c = wer_cer_phrase(r["reference"], predictions[nom])
        lat = latences.get(nom)
        sorties.append({"source": r.get("source", "noxa_reel"), "categorie": r.get("categorie", ""), "chemin_audio": r["chemin_audio"],
                        "reference": r["reference"], "hypothese": predictions[nom], "wer": f"{w:.4f}", "cer": f"{c:.4f}",
                        "latence_ms": f"{1000 * lat:.0f}" if lat is not None else "", "erreur": ""})
    inconnus = sorted(set(predictions) - {nom_fichier(r["chemin_audio"]) for r in base})
    colonnes = ["source", "categorie", "chemin_audio", "reference", "hypothese", "wer", "cer", "latence_ms", "erreur"]
    with open(chemin_sortie, "w", encoding="utf-8-sig", newline="") as f:
        w_csv = csv.DictWriter(f, fieldnames=colonnes, delimiter=";", quoting=csv.QUOTE_ALL)
        w_csv.writeheader()
        w_csv.writerows(sorties)
    return sorties, absents, inconnus, repares


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--kaggle", required=True)
    p.add_argument("--base", required=True, help="details_base.csv : fournit les références et les catégories")
    p.add_argument("--out", default="details_nouveau.csv")
    a = p.parse_args()
    sorties, absents, inconnus, repares = convertir(a.kaggle, a.base, a.out)
    print(f"{len(sorties)} vocaux convertis dans {a.out} ; {repares} texte(s) aux accents réparés.")
    if absents:
        print(f"⚠️  {len(absents)} vocal(aux) de details_base.csv absent(s) du fichier Kaggle : {absents}")
        print("   (si ce fichier a été copié-collé, il a peut-être été coupé : ré-exporte-le en entier)")
    if inconnus:
        print(f"⚠️  {len(inconnus)} fichier(s) du fichier Kaggle inconnu(s) de details_base.csv, ignoré(s) : {inconnus}")
    w = sum(float(s["wer"]) for s in sorties) / len(sorties)
    c = sum(float(s["cer"]) for s in sorties) / len(sorties)
    print(f"Moyenne par fichier avec nos formules : WER {100 * w:.2f} %, CER (espaces comptés) {100 * c:.2f} %")


if __name__ == "__main__":
    main()
