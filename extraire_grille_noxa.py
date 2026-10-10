#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Convertit la feuille "Grille collecte" de NOXA_grille_collecte_dataset.xlsx
vers le format manifeste attendu par benchmark_corpus_xlsr.py.

Contexte important (vérifié avant d'écrire ce script, pas supposé) :
  - Sur 54 lignes, une seule est marquée "Audio vérifié individuellement" =
    Oui, et c'est explicitement un "Exemple fictif — à remplacer par vos
    vraies données". Les 53 autres portent toutes la même note : pré-remplies
    depuis le texte du script déjà partagé, PAS depuis une réécoute. Ce script
    les utilise quand même comme vérité terrain, sur confirmation de
    l'utilisateur que le script a été lu mot pour mot sans improvisation à
    l'enregistrement — mais voir l'avertissement affiché en sortie.
  - La ligne "Exemple fictif" (Conv=1, Tour=1) est exclue : ce n'est pas une
    vraie donnée, juste une illustration de ce à quoi une ligne validée
    ressemblerait. La vraie ligne conv1_tour1 (l'autre des deux doublons)
    est conservée normalement.
  - Les lignes "Décision dataset" = "Exclu" sont exclues (cas observé :
    fichier audio manquant, signalé "[FICHIER INTROUVABLE]" dans la grille).

Catégorisation : priorité aux champs critiques (taille/prix entendus) plutôt
qu'au code-switching, parce que c'est précisément le trou qu'aucun des trois
corpus publics testés jusqu'ici ne couvrait avec de vrais chiffres à la
française (WolBanking77 n'avait que des nombres wolof littéraires du type
"fukki junni", pas "42" ou "15 000").

USAGE
  python extraire_grille_noxa.py --xlsx "NOXA_grille_collecte_dataset.xlsx" ^
      --dossier-audio "Vocaux2" --out-dir echantillon_noxa_reel
"""

import argparse
import csv
import os


def texte_cellule(valeur):
    """Convertit une cellule pandas en texte, en traitant correctement les
    cases vides. Piège réel rencontré : une case vide devient NaN (un float),
    et NaN est VRAI en contexte booléen Python ('NaN or "" ' ne donne jamais
    ""), donc str(NaN) produit la chaîne non vide "nan" — faussant tout test
    de présence/absence en aval. Ce helper est utilisé partout où une cellule
    est lue, pas seulement dans categoriser()."""
    if valeur is None:
        return ""
    if isinstance(valeur, float) and valeur != valeur:  # seul cas où x != x : NaN
        return ""
    return str(valeur).strip()


def categoriser(ligne):
    """Priorité : chiffres critiques > code-switching > wolof pur > français.
    Une seule étiquette par segment pour la ventilation du rapport — le détail
    complet (toutes les étiquettes d'origine) reste dans une colonne à part,
    rien n'est perdu."""
    taille = texte_cellule(ligne.get("Taille (entendue)"))
    prix = texte_cellule(ligne.get("Prix (entendu)"))
    if taille or prix:
        return "chiffres_critiques"
    categories = texte_cellule(ligne.get("Catégorie(s)"))
    if "code-switching" in categories.lower():
        return "code_switching"
    if "wolof pur" in categories.lower():
        return "wolof_pur"
    if "français" in categories.lower() or "francais" in categories.lower():
        return "francais"
    return ""


def run(args):
    try:
        import pandas as pd
    except ImportError:
        raise SystemExit("pandas non installé : pip install pandas openpyxl")

    df = pd.read_excel(args.xlsx, sheet_name=args.feuille)
    print(f"{len(df)} ligne(s) lues dans la feuille {args.feuille!r}.")

    # Exclusion 1 : la ligne modèle fictive (texte exact vérifié dans le fichier).
    avant = len(df)
    df = df[~df["Notes"].astype(str).str.contains("Exemple fictif", na=False)]
    print(f"  {avant - len(df)} ligne 'Exemple fictif' exclue.")

    # Exclusion 2 : décision "Exclu" (fichier manquant ou autre raison déjà actée).
    avant = len(df)
    df = df[df["Décision dataset"].astype(str).str.strip() != "Exclu"]
    print(f"  {avant - len(df)} ligne(s) 'Décision dataset = Exclu' exclue(s).")

    # Exclusion 3 : filet de sécurité, référence vide (ne devrait pas arriver).
    avant = len(df)
    df = df[df["Transcription corrigée"].astype(str).str.strip().ne("")
            & df["Transcription corrigée"].notna()]
    print(f"  {avant - len(df)} ligne(s) sans transcription corrigée exclue(s).")

    lignes_manifeste = []
    manquants = []
    for _, l in df.iterrows():
        nom_fichier = str(l["Fichier audio"]).strip()
        chemin_complet = os.path.join(args.dossier_audio, nom_fichier)
        if not os.path.isfile(chemin_complet):
            manquants.append(nom_fichier)
            continue
        lignes_manifeste.append({
            # Chemin ABSOLU, volontairement : contrairement à Urban Bus/
            # WolBanking77/Kallaama, l'audio ici n'est pas copié dans
            # --out-dir mais référencé là où il se trouve déjà. Un chemin
            # relatif serait résolu par benchmark_corpus_xlsr.py PAR RAPPORT
            # AU DOSSIER DU MANIFESTE (--out-dir), pas au dossier courant —
            # d'où un chemin dupliqué du type "out-dir/Vocaux2/..." si on
            # laissait un chemin relatif ici. Bug réel rencontré et corrigé.
            "chemin_audio": os.path.abspath(chemin_complet),
            "reference": texte_cellule(l["Transcription corrigée"]),
            "categorie": categoriser(l),
            "source": "noxa_reel",
            "conv_tour": f"conv{l['Conv']}_tour{l['Tour']}",
            "categories_detail": texte_cellule(l.get("Catégorie(s)")),
            "produit": texte_cellule(l.get("Produit")),
            "taille_entendue": texte_cellule(l.get("Taille (entendue)")),
            "prix_entendu": texte_cellule(l.get("Prix (entendu)")),
            "zone": texte_cellule(l.get("Zone livraison")),
            "qualite_audio": texte_cellule(l.get("Qualité audio")),
            "decision_dataset": texte_cellule(l.get("Décision dataset")),
        })

    if manquants:
        print(f"\n{len(manquants)} fichier(s) référencé(s) dans la grille mais introuvables "
              f"dans {args.dossier_audio!r} — ignorés :")
        for m in manquants:
            print(f"    {m}")

    os.makedirs(args.out_dir, exist_ok=True)
    chemin_manifeste = os.path.join(args.out_dir, "manifeste.csv")
    colonnes = ["chemin_audio", "reference", "categorie", "source", "conv_tour",
                "categories_detail", "produit", "taille_entendue", "prix_entendu",
                "zone", "qualite_audio", "decision_dataset"]
    with open(chemin_manifeste, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=colonnes, delimiter=";", quoting=csv.QUOTE_ALL)
        w.writeheader()
        w.writerows(lignes_manifeste)

    n_chiffres = sum(1 for l in lignes_manifeste if l["categorie"] == "chiffres_critiques")
    print(f"\n{len(lignes_manifeste)} segment(s) écrits dans le manifeste "
          f"(dont {n_chiffres} avec taille/prix entendu — le test le plus attendu).")
    print(f"Manifeste : {chemin_manifeste}")

    print("\n" + "=" * 70)
    print("RAPPEL IMPORTANT (pas une formalité) :")
    print("Cette vérité terrain vient du texte du script d'enregistrement, pas")
    print("d'une réécoute segment par segment validée par un second annotateur,")
    print("comme la grille elle-même le demande. Fiable seulement si le script a")
    print("été lu mot pour mot sans improvisation. Un sondage de 6-8 fichiers")
    print("réécoutés au hasard avant de tirer une conclusion reste recommandé.")
    print("=" * 70)
    print(f"\nÀ utiliser ensuite avec :")
    print(f"  python benchmark_corpus_xlsr.py run --manifest {chemin_manifeste} --out-dir resultats_noxa_reel")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--xlsx", required=True)
    p.add_argument("--feuille", default="Grille collecte")
    p.add_argument("--dossier-audio", required=True, help="dossier contenant les .ogg (ex: Vocaux2)")
    p.add_argument("--out-dir", required=True)
    args = p.parse_args()
    run(args)


if __name__ == "__main__":
    main()
