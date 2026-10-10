#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Vérifie, AVANT tout entraînement, que les transcriptions d'un manifeste sont
compatibles avec le vocabulaire RÉEL du modèle abdouaziiz/wav2vec2-xls-r-300m-wolof-lm.

Vocabulaire confirmé (retrouvé sur un commit du vrai vocab.json du modèle,
jamais retouché depuis d'après l'historique des commits) :
  a à ã b c ç d e é f g h i j k l m n ñ o ó p q r s t u v w x y
  + | (séparateur de mots/espace) + [UNK] + [PAD]
Pas de chiffres, pas de majuscules utilisables (do_lower_case=false dans la
config du tokenizer — donc AUCUNE mise en minuscule automatique), pas de "z".

Normalisation appliquée AVANT comparaison, identique à celle utilisée par
l'auteur du modèle pour préparer ses propres données d'entraînement (visible
dans sa fiche technique) : minuscules + suppression de  " ? . ! - ; : ,
Tout caractère qui reste hors de ce vocabulaire après cette normalisation
deviendrait un [UNK] pendant l'entraînement — silencieusement, sans erreur.

USAGE
  python verifier_vocabulaire.py --manifest echantillon_finetuning\\manifeste.csv
  python verifier_vocabulaire.py --manifest grille_verite_terrain_finetuning.xlsx --colonne-texte "Transcription corrigée" --xlsx --feuille "Grille collecte"
"""

import argparse
import csv
import re
from collections import Counter

VOCAB_CONFIRME = set("aàãbcçdeéëfghijklmnñoópqrstuvwxy")  # pas de z, pas de chiffres, pas de majuscules
CHARS_A_SUPPRIMER = re.compile(r'["?.!\-;:,]')  # régex exacte de l'auteur du modèle


def normaliser(texte):
    texte = str(texte).lower()
    texte = CHARS_A_SUPPRIMER.sub("", texte)
    return texte


def analyser_texte(texte):
    """Retourne l'ensemble des caractères hors vocabulaire après normalisation
    (en ignorant l'espace, qui correspond au séparateur '|' du modèle)."""
    normalise = normaliser(texte)
    hors_vocab = set()
    for c in normalise:
        if c == " ":
            continue
        if c not in VOCAB_CONFIRME:
            hors_vocab.add(c)
    return hors_vocab, normalise


def lire_textes(args):
    if args.xlsx:
        import pandas as pd
        df = pd.read_excel(args.manifest, sheet_name=args.feuille)
        textes = [str(v) for v in df[args.colonne_texte].dropna()]
    else:
        with open(args.manifest, "rb") as f:
            brut = f.read()
        try:
            contenu = brut.decode("utf-8-sig")
        except UnicodeDecodeError:
            contenu = brut.decode("cp1252", errors="replace")
        lignes_brutes = contenu.splitlines()
        premiere = lignes_brutes[0] if lignes_brutes else ""
        delim = ";" if premiere.count(";") >= premiere.count(",") else ","
        lignes = list(csv.DictReader(lignes_brutes, delimiter=delim))
        textes = [l[args.colonne_texte] for l in lignes if l.get(args.colonne_texte, "").strip()]
    return textes


def run(args):
    textes = lire_textes(args)
    print(f"{len(textes)} texte(s) à vérifier.\n")

    compte_hors_vocab = Counter()
    textes_problematiques = []
    for texte in textes:
        hors_vocab, _ = analyser_texte(texte)
        if hors_vocab:
            compte_hors_vocab.update(hors_vocab)
            textes_problematiques.append((texte, hors_vocab))

    if not textes_problematiques:
        print("✅ Tous les textes sont entièrement compatibles avec le vocabulaire du modèle,")
        print("   une fois normalisés (minuscules + ponctuation retirée). Aucun [UNK] attendu.")
        return

    print(f"⚠️  {len(textes_problematiques)} texte(s) sur {len(textes)} contiennent au moins")
    print("   un caractère qui deviendrait [UNK] pendant l'entraînement :\n")

    print("Caractères problématiques, par fréquence :")
    for car, n in compte_hors_vocab.most_common():
        print(f"  {car!r}  ({n} occurrence(s))")

    print(f"\nExemples ({min(10, len(textes_problematiques))} premiers) :")
    for texte, hors_vocab in textes_problematiques[:10]:
        print(f"  {texte!r}")
        print(f"    -> caractère(s) problématique(s) : {sorted(hors_vocab)}")

    print("\nCorrige ces textes (accents mal saisis, chiffres restants, apostrophes")
    print("courbes au lieu de droites, etc.) avant de lancer l'entraînement — sinon")
    print("ces mots deviendront silencieusement des [UNK], sans erreur visible.")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--manifest", required=True)
    p.add_argument("--colonne-texte", default="reference")
    p.add_argument("--xlsx", action="store_true", help="le fichier est un .xlsx (pas un .csv)")
    p.add_argument("--feuille", default="Grille collecte")
    args = p.parse_args()
    run(args)


if __name__ == "__main__":
    main()
