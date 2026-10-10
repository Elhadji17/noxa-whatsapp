#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Re-note les résultats déjà produits (details_base.csv, details_finetune.csv)
avec des RÉFÉRENCES CORRIGÉES lues dans la grille Excel — sans aucune nouvelle
inférence, puisque les hypothèses du modèle sont déjà sauvegardées.

Pourquoi : dans les 52 références, un même mot est écrit de plusieurs façons
(ex. "beugue", "beug", "beugg", "bëgg"...) alors que le modèle écrit toujours
la même orthographe. Une partie du WER/CER mesuré vient donc de l'écriture de
la référence, pas d'une erreur de reconnaissance.

PROTOCOLE RECOMMANDÉ (pour ne pas fausser la mesure)
  1. Corrige l'orthographe des phrases dans la colonne "Transcription corrigée"
     de la grille, à partir de CE QUE TU AS DIT et de l'orthographe standard
     du wolof. Ne regarde PAS ce que le modèle a écrit pendant cette passe :
     recopier sa graphie baisserait artificiellement le WER.
  2. Lance ce script : il recalcule les tables et les intervalles de confiance.
  3. Seulement ensuite, relance avec --mots-proches pour voir les mots où la
     référence et le modèle ne diffèrent plus que de quelques lettres : ça sert
     à repérer des graphies oubliées, pas à copier le modèle.

Limite à garder en tête : les mots français insérés dans une phrase wolof
(basket, alors, modèle...) sont écrits en orthographe française dans les
références, alors que le modèle écrit leur prononciation (baseel, alool). Ce
décalage-là n'est PAS corrigeable en "corrigeant le wolof" : il pèsera
toujours sur les catégories code_switching et francais.

USAGE
  python rescorer_references.py --xlsx "NOXA_grille_collecte_dataset.xlsx" ^
      --base details_base.csv --finetune details_finetune.csv
  python rescorer_references.py ... --mots-proches mots_proches.csv

Nécessite benchmark_corpus_xlsr.py dans le même dossier, ainsi que numpy et
pandas (+ openpyxl pour lire le .xlsx).
"""

import argparse
import csv
import os
import re
import sys
from collections import defaultdict

try:
    import numpy as np
except ImportError:
    raise SystemExit("numpy est nécessaire (installé avec pandas) : pip install numpy")
try:
    import benchmark_corpus_xlsr as bc
except ImportError:
    raise SystemExit("benchmark_corpus_xlsr.py doit être dans le même dossier que ce script.")


def nom_fichier(chemin):
    # Indépendant du système : les chemins du manifeste sont des chemins Windows.
    return re.split(r"[\\/]", str(chemin))[-1]


def texte_cellule(valeur):
    if valeur is None:
        return ""
    if isinstance(valeur, float) and valeur != valeur:  # NaN
        return ""
    return str(valeur).strip()


def lire_details(chemin):
    with open(chemin, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f, delimiter=";"))


def lire_references_grille(chemin_xlsx, feuille):
    try:
        import pandas as pd
    except ImportError:
        raise SystemExit("pandas non installé : pip install pandas openpyxl")
    df = pd.read_excel(chemin_xlsx, sheet_name=feuille)
    refs, conflits = {}, set()
    for _, l in df.iterrows():
        if "Exemple fictif" in texte_cellule(l.get("Notes")):
            continue  # ligne modèle, pas une vraie donnée
        nom, texte = texte_cellule(l.get("Fichier audio")), texte_cellule(l.get("Transcription corrigée"))
        if not nom or not texte:
            continue
        if nom in refs and refs[nom] != texte:
            conflits.add(nom)
        refs[nom] = texte
    return refs, sorted(conflits)


def appliquer_references(lignes, refs):
    """Renvoie des lignes avec la référence remplacée par celle de la grille
    (par nom de fichier). Si un fichier est absent de la grille, on garde
    l'ancienne référence et on le signale."""
    sorties, absents, modifiees = [], [], 0
    for l in lignes:
        nom = nom_fichier(l["chemin_audio"])
        nouvelle = refs.get(nom)
        if nouvelle is None:
            absents.append(nom)
            nouvelle = l["reference"]
        if bc.normaliser(nouvelle) != bc.normaliser(l["reference"]):
            modifiees += 1
        sorties.append(dict(l, reference=nouvelle))
    return sorties, absents, modifiees


def comptes(l):
    ref, hyp = bc.normaliser(l["reference"]), bc.normaliser(l["hypothese"])
    rm, hm = ref.split(), hyp.split()
    rc, hc = list(ref.replace(" ", "")), list(hyp.replace(" ", ""))
    return (bc.distance_levenshtein(rm, hm), len(rm), bc.distance_levenshtein(rc, hc), len(rc))


def wer_cer_groupe(tab, idx):
    s = tab[idx].sum(axis=0)
    return s[0] / s[1], s[2] / s[3]


def intervalle(tab_base, tab_ft, idx, n_boot, graine):
    rng = np.random.default_rng(graine)
    idx = np.asarray(idx)
    ech = idx[rng.integers(0, len(idx), size=(n_boot, len(idx)))]
    b, f = tab_base[ech].sum(axis=1), tab_ft[ech].sum(axis=1)
    dw = f[:, 0] / f[:, 1] - b[:, 0] / b[:, 1]
    dc = f[:, 2] / f[:, 3] - b[:, 2] / b[:, 3]
    return np.percentile(dw, [2.5, 97.5]), np.percentile(dc, [2.5, 97.5])


def dist_norm(a, b):
    return bc.distance_levenshtein(list(a), list(b)) / max(len(a), len(b))


def aligner_mots(ref, hyp):
    """Alignement mot à mot où substituer deux mots proches coûte moins cher
    que substituer deux mots éloignés : les variantes d'écriture s'alignent
    donc entre elles plutôt qu'avec un mot au hasard."""
    n, m = len(ref), len(hyp)
    D = [[0.0] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        D[i][0] = float(i)
    for j in range(1, m + 1):
        D[0][j] = float(j)
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            D[i][j] = min(D[i - 1][j] + 1, D[i][j - 1] + 1, D[i - 1][j - 1] + dist_norm(ref[i - 1], hyp[j - 1]))
    i, j, paires = n, m, []
    while i > 0 or j > 0:
        if i > 0 and j > 0 and abs(D[i][j] - (D[i - 1][j - 1] + dist_norm(ref[i - 1], hyp[j - 1]))) < 1e-9:
            paires.append((ref[i - 1], hyp[j - 1])); i -= 1; j -= 1
        elif i > 0 and abs(D[i][j] - (D[i - 1][j] + 1)) < 1e-9:
            paires.append((ref[i - 1], None)); i -= 1
        else:
            paires.append((None, hyp[j - 1])); j -= 1
    return paires[::-1]


def mots_proches(lignes, seuil=0.4):
    """Paires (mot de référence, mot du modèle) proches mais différentes,
    avec leur nombre d'occurrences et un exemple."""
    agg = {}
    for l in lignes:
        rm, hm = bc.normaliser(l["reference"]).split(), bc.normaliser(l["hypothese"]).split()
        for r, h in aligner_mots(rm, hm):
            if r and h and r != h and len(r) >= 3 and len(h) >= 3:
                d = dist_norm(r, h)
                if d <= seuil:
                    e = agg.setdefault((r, h), {"d": d, "n": 0, "fichier": nom_fichier(l["chemin_audio"]), "phrase": l["reference"]})
                    e["n"] += 1
    return sorted(((r, h, e["d"], e["n"], e["fichier"], e["phrase"]) for (r, h), e in agg.items()),
                  key=lambda x: (x[2], -x[3], x[0]))


def run(args):
    refs, conflits = lire_references_grille(args.xlsx, args.feuille)
    if conflits:
        print(f"⚠️  Deux textes différents pour le même fichier dans la grille : {conflits[:5]} — le dernier est retenu.")
    base_brut, ft_brut = lire_details(args.base), lire_details(args.finetune)
    if [nom_fichier(l["chemin_audio"]) for l in base_brut] != [nom_fichier(l["chemin_audio"]) for l in ft_brut]:
        raise SystemExit("Les deux fichiers de résultats ne portent pas sur les mêmes fichiers, dans le même ordre.")

    base, absents, modifiees = appliquer_references(base_brut, refs)
    ft, _, _ = appliquer_references(ft_brut, refs)
    print(f"{len(base)} fichiers ; {modifiees} référence(s) différente(s) de celle utilisée lors du benchmark initial.")
    if absents:
        print(f"⚠️  {len(absents)} fichier(s) absent(s) de la grille, ancienne référence conservée : {absents[:5]}")
    if modifiees == 0:
        print("   (aucune référence modifiée : les chiffres ci-dessous sont ceux d'origine)")

    TB, TF = np.array([comptes(l) for l in base]), np.array([comptes(l) for l in ft])
    cats = np.array([l["categorie"] for l in base])
    groupes = [("TOUTES", np.arange(len(base)))] + [(c, np.where(cats == c)[0]) for c in sorted(set(cats))]

    print(f"\n{'catégorie':<20}{'n':>4} | {'WER base':>9}{'WER ft':>9}{'ΔWER [IC95 %]':>28} | {'CER base':>9}{'CER ft':>9}{'ΔCER [IC95 %]':>28}")
    print("-" * 124)
    for nom, idx in groupes:
        wb, cb = wer_cer_groupe(TB, idx)
        wf, cf = wer_cer_groupe(TF, idx)
        (lw, hw), (lc, hc) = intervalle(TB, TF, idx, args.bootstrap, args.graine)
        txt_w = f"{100*(wf-wb):+6.1f} [{100*lw:+6.1f};{100*hw:+6.1f}]"
        txt_c = f"{100*(cf-cb):+6.1f} [{100*lc:+6.1f};{100*hc:+6.1f}]"
        print(f"{nom:<20}{len(idx):>4} | {wb:>9.4f}{wf:>9.4f}{txt_w:>28} | {cb:>9.4f}{cf:>9.4f}{txt_c:>28}")
    print("\nΔ en points de pourcentage, fine-tuné − base (négatif = le fine-tuné fait mieux).")
    print("Les intervalles ré-échantillonnent des FICHIERS d'un seul locuteur : ils ne disent rien sur d'autres voix.")

    if args.mots_proches:
        liste = mots_proches(base)
        with open(args.mots_proches, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f, delimiter=";", quoting=csv.QUOTE_ALL)
            w.writerow(["mot_reference", "mot_modele", "ecart", "occurrences", "exemple_fichier", "exemple_phrase_reference"])
            for r, h, d, n, fichier, phrase in liste:
                w.writerow([r, h, f"{d:.2f}", n, fichier, phrase])
        print(f"\n{len(liste)} paire(s) de mots proches écrite(s) dans {args.mots_proches}")
        print("À utiliser APRÈS ta passe de correction, pour repérer des graphies oubliées — pas pour copier le modèle.")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--xlsx", required=True, help="grille Excel dont la colonne 'Transcription corrigée' contient les références à utiliser")
    p.add_argument("--feuille", default="Grille collecte")
    p.add_argument("--base", required=True, help="details_base.csv")
    p.add_argument("--finetune", required=True, help="details_finetune.csv")
    p.add_argument("--mots-proches", default=None, help="écrit la liste des mots proches dans ce fichier CSV")
    p.add_argument("--bootstrap", type=int, default=10000)
    p.add_argument("--graine", type=int, default=42)
    run(p.parse_args())


if __name__ == "__main__":
    main()
