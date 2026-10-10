#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Mesure la "Critical Field Accuracy" sur les vocaux NOXA : pour chaque champ
critique attendu (taille, prix, produit, couleur, zone), l'information
est-elle récupérable depuis la transcription XLS-R — pas juste "le WER
est-il bas" ?

POURQUOI CE N'EST PAS AUTOMATISABLE PROPREMENT
XLS-R (wav2vec2) sort du texte phonétique en graphie wolof : son vocabulaire
ne contient très probablement aucun chiffre. "44" n'apparaîtra donc jamais
tel quel dans l'hypothèse — au mieux une tentative phonétique du mot
("quarante-quatre" prononcé, retranscrit à l'oreille wolof). Juger si
"kaaraankàt" correspond à "quarante-quatre" est un jugement humain, à
l'oreille, par quelqu'un qui parle wolof — pas quelque chose qu'un script
peut deviner de façon fiable. Ce script prépare donc la grille, mais ne
décide jamais lui-même si un champ est récupérable : c'est marqué à la main,
sur le même principe que le test à l'aveugle Claude/GPT-4o/Gemini déjà fait
dans ce projet (run = préparation, score = calcul après remplissage).

USAGE
  Étape 1 — préparer la grille de revue :
    python champs_critiques.py run --xlsx "NOXA_grille_collecte_dataset.xlsx" ^
        --resultats resultats_noxa_reel\\details.csv --out-dir revue_champs

  Étape 2 — remplir la colonne "recuperable" dans revue_champs\\grille_revue.csv
  avec, pour chaque ligne : oui / partiel / non
  (oui = l'info est clairement là, même mal orthographiée ; partiel = une
  trace phonétique plausible mais incertaine ; non = l'info a disparu)

  Étape 3 — calculer le résultat :
    python champs_critiques.py score --grille revue_champs\\grille_revue.csv
"""

import argparse
import csv
import os
from collections import defaultdict

CHAMPS = [
    ("Produit", "produit"),
    ("Taille (entendue)", "taille"),
    ("Couleur", "couleur"),
    ("Prix (entendu)", "prix"),
    ("Zone livraison", "zone"),
]


def texte_cellule(valeur):
    """Même piège NaN que dans extraire_grille_noxa.py — NaN est vrai en
    contexte booléen Python, donc str(NaN) donnerait la chaîne non vide
    'nan' sans ce garde-fou."""
    if valeur is None:
        return ""
    if isinstance(valeur, float) and valeur != valeur:
        return ""
    return str(valeur).strip()


def lire_resultats(chemin_resultats):
    """Indexe les résultats du benchmark par nom de fichier audio (pas le
    chemin complet, qui diffère selon la machine/le run)."""
    index = {}
    with open(chemin_resultats, "rb") as f:
        brut = f.read()
    try:
        contenu = brut.decode("utf-8-sig")
    except UnicodeDecodeError:
        contenu = brut.decode("cp1252", errors="replace")
    lignes = contenu.splitlines()
    premiere = lignes[0] if lignes else ""
    delim = ";" if premiere.count(";") >= premiere.count(",") else ","
    for row in csv.DictReader(lignes, delimiter=delim):
        nom = os.path.basename(row.get("chemin_audio", ""))
        if nom:
            index[nom] = row
    return index


def run(args):
    try:
        import pandas as pd
    except ImportError:
        raise SystemExit("pandas non installé : pip install pandas openpyxl")

    df = pd.read_excel(args.xlsx, sheet_name=args.feuille)
    resultats = lire_resultats(args.resultats)
    print(f"{len(df)} ligne(s) dans la grille, {len(resultats)} résultat(s) de benchmark chargés.")

    lignes_revue = []
    sans_resultat = []
    for _, l in df.iterrows():
        nom_fichier = texte_cellule(l.get("Fichier audio"))
        if not nom_fichier:
            continue
        res = resultats.get(nom_fichier)
        if res is None:
            sans_resultat.append(nom_fichier)
            continue
        for nom_colonne, code_champ in CHAMPS:
            valeur_attendue = texte_cellule(l.get(nom_colonne))
            if not valeur_attendue:
                continue  # ce champ n'était pas mentionné dans ce vocal
            lignes_revue.append({
                "fichier": nom_fichier,
                "champ": code_champ,
                "valeur_attendue": valeur_attendue,
                "reference_complete": texte_cellule(l.get("Transcription corrigée")),
                "hypothese_asr": res.get("hypothese", ""),
                "wer_segment": res.get("wer", ""),
                "recuperable": "",  # à remplir : oui / partiel / non
                "commentaire": "",
            })

    if sans_resultat:
        print(f"\n{len(sans_resultat)} fichier(s) de la grille sans résultat de benchmark correspondant "
              f"(pas dans {args.resultats!r}) — ignoré(s) :")
        for n in sans_resultat:
            print(f"    {n}")

    os.makedirs(args.out_dir, exist_ok=True)
    chemin_grille = os.path.join(args.out_dir, "grille_revue.csv")
    colonnes = ["fichier", "champ", "valeur_attendue", "reference_complete",
                "hypothese_asr", "wer_segment", "recuperable", "commentaire"]
    with open(chemin_grille, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=colonnes, delimiter=";", quoting=csv.QUOTE_ALL)
        w.writeheader()
        w.writerows(lignes_revue)

    par_champ = defaultdict(int)
    for l in lignes_revue:
        par_champ[l["champ"]] += 1
    print(f"\n{len(lignes_revue)} ligne(s) à juger, réparties ainsi :")
    for code_champ, n in sorted(par_champ.items()):
        print(f"  {code_champ:<10} {n}")

    print(f"\nGrille à remplir : {chemin_grille}")
    print("Pour chaque ligne, colonne 'recuperable' : oui / partiel / non")
    print("(oui = l'info est clairement là même mal orthographiée ; partiel = trace")
    print(" phonétique plausible mais incertaine ; non = l'info a disparu)")


def lire_csv_flexible(chemin):
    """Même tolérance que dans les autres scripts du projet : encodage
    UTF-8/Windows-1252, séparateur ';' ou ',' détecté automatiquement."""
    with open(chemin, "rb") as f:
        brut = f.read()
    try:
        contenu = brut.decode("utf-8-sig")
    except UnicodeDecodeError:
        contenu = brut.decode("cp1252", errors="replace")
    lignes_brutes = contenu.splitlines()
    premiere = lignes_brutes[0] if lignes_brutes else ""
    delim = ";" if premiere.count(";") >= premiere.count(",") else ","
    return list(csv.DictReader(lignes_brutes, delimiter=delim))


NORMES_RECUPERABLE = {"oui": "oui", "o": "oui", "yes": "oui",
                       "non": "non", "n": "non", "no": "non",
                       "partiel": "partiel", "partielle": "partiel", "p": "partiel"}


def score(args):
    lignes = lire_csv_flexible(args.grille)

    non_remplies = [l for l in lignes if not l.get("recuperable", "").strip()]
    valides = [l for l in lignes if l.get("recuperable", "").strip()]
    if non_remplies:
        print(f"⚠️  {len(non_remplies)} ligne(s) sur {len(lignes)} pas encore jugée(s) "
              f"(colonne 'recuperable' vide) — exclues du calcul ci-dessous.")

    inconnues = set()
    par_champ = defaultdict(lambda: {"oui": 0, "partiel": 0, "non": 0})
    for l in valides:
        brut_val = l["recuperable"].strip().lower()
        norm = NORMES_RECUPERABLE.get(brut_val)
        if norm is None:
            inconnues.add(l["recuperable"])
            continue
        par_champ[l["champ"]][norm] += 1

    if inconnues:
        print(f"⚠️  Valeur(s) non reconnue(s) dans 'recuperable', ignorée(s) : {sorted(inconnues)}")
        print("   Attendu : oui / partiel / non (insensible à la casse)")

    print(f"\n{'champ':<10}{'n':>5}{'oui':>6}{'partiel':>9}{'non':>6}{'% oui':>8}{'% oui+partiel':>16}")
    print("-" * 60)
    total = {"oui": 0, "partiel": 0, "non": 0}
    for champ, compte in sorted(par_champ.items()):
        n = sum(compte.values())
        pct_oui = 100 * compte["oui"] / n if n else 0
        pct_large = 100 * (compte["oui"] + compte["partiel"]) / n if n else 0
        print(f"{champ:<10}{n:>5}{compte['oui']:>6}{compte['partiel']:>9}{compte['non']:>6}"
              f"{pct_oui:>7.0f}%{pct_large:>15.0f}%")
        for k in total: total[k] += compte[k]
    n_total = sum(total.values())
    if n_total:
        print("-" * 60)
        pct_oui = 100 * total["oui"] / n_total
        pct_large = 100 * (total["oui"] + total["partiel"]) / n_total
        print(f"{'TOUS':<10}{n_total:>5}{total['oui']:>6}{total['partiel']:>9}{total['non']:>6}"
              f"{pct_oui:>7.0f}%{pct_large:>15.0f}%")

    print("\nLecture : '% oui' = information clairement récupérable telle quelle.")
    print("'% oui+partiel' = en comptant aussi les traces phonétiques plausibles mais incertaines —")
    print("une fourchette haute, pas un chiffre à prendre pour argent comptant côté sécurité : un champ")
    print("'partiel' reste un champ à faire confirmer par écrit dans NOXA, pas un champ fiable en soi.")


def exemples(args):
    """Construit le tableau de typologie d'erreurs : pour chaque croisement
    (champ critique × jugement), montre de VRAIES paires valeur attendue /
    hypothèse XLS-R tirées de la grille déjà jugée — pas des exemples
    inventés. Distingue déjà, via la colonne 'recuperable' existante :
      non     -> l'information a disparu (équivalent de "nombre disparu")
      partiel -> une trace phonétique plausible mais incertaine
                 (équivalent de "nombre/taille phonétisé(e)")
      oui     -> l'information est bien conservée
    Pas besoin d'une nouvelle colonne de catégorisation : cette distinction
    existe déjà dans tes jugements oui/partiel/non."""
    lignes = lire_csv_flexible(args.grille)
    valides = [l for l in lignes if l.get("recuperable", "").strip()]

    par_cle = defaultdict(list)
    for l in valides:
        norm = NORMES_RECUPERABLE.get(l["recuperable"].strip().lower())
        if norm is None:
            continue
        par_cle[(l["champ"], norm)].append(l)

    if args.champ:
        cles = [k for k in par_cle if k[0] == args.champ]
    else:
        cles = list(par_cle.keys())
    if args.recuperable:
        cles = [k for k in cles if k[1] == args.recuperable.lower()]

    ordre_jugement = {"non": 0, "partiel": 1, "oui": 2}
    for champ, jugement in sorted(cles, key=lambda k: (k[0], ordre_jugement.get(k[1], 9))):
        groupe = par_cle[(champ, jugement)]
        titre = f"{champ} / {jugement}  ({len(groupe)} cas)"
        print(f"\n{titre}\n{'=' * len(titre)}")
        for l in groupe[:args.max_par_groupe]:
            commentaire = f"  — {l['commentaire']}" if l.get("commentaire", "").strip() else ""
            print(f"  [{l['fichier']}] {l['valeur_attendue']!r} -> {l['hypothese_asr']!r}{commentaire}")
        if len(groupe) > args.max_par_groupe:
            print(f"  ... et {len(groupe) - args.max_par_groupe} de plus "
                  f"(--max-par-groupe pour en voir davantage)")

    print(f"\n{'-'*70}")
    print("Lecture : les cas 'non' montrent ce qui disparaît complètement de la")
    print("transcription — c'est le groupe le plus informatif pour cibler un futur")
    print("fine-tuning. Les cas 'partiel' montrent ce qu'XLS-R entend phonétiquement")
    print("mais déforme — utile pour juger si un modèle de langage en aval pourrait")
    print("les récupérer, ou si c'est trop déformé pour ça.")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="Préparer la grille de revue")
    r.add_argument("--xlsx", required=True)
    r.add_argument("--feuille", default="Grille collecte")
    r.add_argument("--resultats", required=True, help="details.csv produit par benchmark_corpus_xlsr.py")
    r.add_argument("--out-dir", required=True)
    r.set_defaults(func=run)

    s = sub.add_parser("score", help="Calculer le résultat une fois la grille remplie")
    s.add_argument("--grille", required=True)
    s.set_defaults(func=score)

    e = sub.add_parser("exemples", help="Voir de vrais exemples par champ et par jugement (typologie d'erreurs)")
    e.add_argument("--grille", required=True)
    e.add_argument("--champ", default=None, help="limiter à un champ (taille, prix, produit, couleur, zone)")
    e.add_argument("--recuperable", default=None, help="limiter à un jugement (oui, partiel, non)")
    e.add_argument("--max-par-groupe", type=int, default=10)
    e.set_defaults(func=exemples)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
