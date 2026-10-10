#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Compare EN AVEUGLE le modèle de base et le modèle fine-tuné sur les champs
critiques (produit, taille, couleur, prix, zone), sans dépendre de
l'orthographe des références.

Pourquoi : le WER/CER mélange erreurs de reconnaissance et différences
d'écriture (le modèle écrit en orthographe officielle, les références sont
écrites à l'oreille). Ici, tu juges une seule chose : l'information est-elle
dans le texte ? Reconnaître un mot demande de savoir ce que tu as dit, pas de
savoir l'écrire.

Définitions (les mêmes que lors de ton premier jugement des 37 champs) :
  oui     = l'information est clairement identifiable dans le texte, sans deviner
  partiel = une trace plausible mais ambiguë
  non     = l'information a disparu

PROTOCOLE
  1. run   : produit feuille_ab.csv (pour toi) et cle_secrete.csv (ne l'ouvre pas).
             Pour chaque fichier audio, le modèle A et le modèle B sont tirés au
             sort, de façon ÉQUILIBRÉE (la moitié des fichiers dans chaque sens) ;
             ce tirage est le même pour tous les champs d'un même fichier.
  2. Remplis jugement_A et jugement_B pour chaque ligne (oui / partiel / non),
     sans chercher à deviner quel modèle est lequel.
  3. score : dévoile la clé et calcule le résultat.

Si les deux textes sont identiques pour un fichier, tu devrais donner le même
jugement aux deux : le script s'en sert pour mesurer ta propre constance.

COMPARER DEUX MOTEURS DIFFÉRENTS (ex. XLS-R contre Whosper, dont les transcriptions sont
déjà dans la grille) : voir --b-colonne-xlsx, --nom-base, --nom-b et --uniformiser.
Même uniformisé, un moteur qui écrit « bleu » en français et un autre « bubble » en
graphie phonétique restent reconnaissables : le test est à l'aveugle autant que possible,
pas parfaitement.

USAGE
  python ab_champs_critiques.py run --xlsx "NOXA_grille_collecte_dataset.xlsx" ^
      --base details_base.csv --finetune details_finetune.csv --out-dir ab_champs
  python ab_champs_critiques.py score --feuille ab_champs\\feuille_ab.csv ^
      --cle ab_champs\\cle_secrete.csv [--anciens revue_champs\\Test.csv]
"""

import argparse
import csv
import os
import random
import re
import sys
import unicodedata
from collections import defaultdict
from math import comb

try:
    import numpy as np
except ImportError:
    np = None

CHAMPS = [("Produit", "produit"), ("Taille (entendue)", "taille"), ("Couleur", "couleur"),
          ("Prix (entendu)", "prix"), ("Zone livraison", "zone")]
NORMES = {"oui": "oui", "o": "oui", "yes": "oui", "non": "non", "n": "non", "no": "non",
          "partiel": "partiel", "partielle": "partiel", "p": "partiel"}
POINTS = {"non": 0, "partiel": 1, "oui": 2}


def nom_fichier(chemin):
    return re.split(r"[\\/]", str(chemin))[-1]


def texte_cellule(valeur):
    if valeur is None:
        return ""
    if isinstance(valeur, float) and valeur != valeur:  # NaN
        return ""
    return str(valeur).strip()


def lire_csv(chemin):
    with open(chemin, "rb") as f:
        brut = f.read()
    try:
        contenu = brut.decode("utf-8-sig")
    except UnicodeDecodeError:
        contenu = brut.decode("cp1252", errors="replace")
    lignes = contenu.splitlines()
    delim = ";" if lignes and lignes[0].count(";") >= lignes[0].count(",") else ","
    return list(csv.DictReader(lignes, delimiter=delim))


def hypotheses_par_fichier(chemin_details):
    return {nom_fichier(r["chemin_audio"]): r["hypothese"] for r in lire_csv(chemin_details)}


def hypotheses_depuis_grille(xlsx, feuille, colonne):
    """Hypothèses d'un autre moteur lues dans une colonne de la grille
    (ex. 'Transcription Whosper'), rattachées au fichier audio."""
    import pandas as pd
    df = pd.read_excel(xlsx, sheet_name=feuille)
    if colonne not in df.columns:
        raise SystemExit(f"Colonne {colonne!r} introuvable dans la grille. Colonnes disponibles : "
                         + ", ".join(repr(c) for c in df.columns))
    hyp = {}
    for _, l in df.iterrows():
        if "Exemple fictif" in texte_cellule(l.get("Notes")):
            continue
        nom, texte = texte_cellule(l.get("Fichier audio")), texte_cellule(l.get(colonne))
        if nom and texte:
            hyp[nom] = texte
    return hyp


def uniformiser(texte):
    """Retire ce qui trahirait le moteur sans rien dire de l'information : majuscules,
    ponctuation, apostrophes. L'orthographe des mots, elle, n'est PAS touchée."""
    t = unicodedata.normalize("NFKC", str(texte or "")).lower()
    t = re.sub(r"[^\w\s]", " ", t, flags=re.UNICODE)
    return re.sub(r"\s+", " ", t).strip()


# ---------------------------------------------------------------------------
# run
# ---------------------------------------------------------------------------

def construire_lignes(xlsx, feuille, hyp_base, hyp_ft):
    import pandas as pd
    df = pd.read_excel(xlsx, sheet_name=feuille)
    lignes = []
    for _, l in df.iterrows():
        if "Exemple fictif" in texte_cellule(l.get("Notes")):
            continue  # ligne modèle, pas une vraie donnée
        nom = texte_cellule(l.get("Fichier audio"))
        if nom not in hyp_base or nom not in hyp_ft:
            continue
        for colonne, code in CHAMPS:
            valeur = texte_cellule(l.get(colonne))
            if valeur:
                lignes.append({"fichier": nom, "champ": code, "valeur_attendue": valeur,
                               "phrase_reference": texte_cellule(l.get("Transcription corrigée"))})
    return lignes


def attribuer_ab(fichiers, graine):
    """Un tirage A/B par FICHIER (identique pour tous ses champs), ÉQUILIBRÉ :
    la base est en position A pour la moitié des fichiers (à un près), le
    fine-tuné pour l'autre. Un tirage libre avait donné 17 fichiers contre 12 ;
    or A a été jugé meilleur plus souvent que B (6 lignes contre 2, non
    significatif mais impossible à écarter), ce qui se confond avec l'écart
    entre modèles quand la répartition est déséquilibrée. Renvoie {fichier: modèle en position A}
    et l'ordre dans lequel présenter les fichiers."""
    rng = random.Random(graine)
    ordre = sorted(set(fichiers))
    rng.shuffle(ordre)
    n = len(ordre)
    positions = ["base"] * (n // 2) + ["finetune"] * (n // 2)
    if n % 2:
        positions.append(rng.choice(["base", "finetune"]))
    rng.shuffle(positions)
    return dict(zip(ordre, positions)), ordre


def run(args):
    hyp_base = hypotheses_par_fichier(args.base)
    if args.b_colonne_xlsx:
        hyp_ft = hypotheses_depuis_grille(args.xlsx, args.feuille, args.b_colonne_xlsx)
    elif args.finetune:
        hyp_ft = hypotheses_par_fichier(args.finetune)
    else:
        raise SystemExit("Indique le second moteur : --finetune (fichier de résultats) ou --b-colonne-xlsx (colonne de la grille).")
    if args.uniformiser:
        hyp_base = {k: uniformiser(v) for k, v in hyp_base.items()}
        hyp_ft = {k: uniformiser(v) for k, v in hyp_ft.items()}
    lignes = construire_lignes(args.xlsx, args.feuille, hyp_base, hyp_ft)
    if not lignes:
        raise SystemExit("Aucun champ critique trouvé : vérifie --xlsx, --feuille et les fichiers de résultats.")
    a_en_premier, ordre = attribuer_ab([l["fichier"] for l in lignes], args.graine)
    rang = {f: i for i, f in enumerate(ordre)}
    lignes.sort(key=lambda l: (rang[l["fichier"]], [c for _, c in CHAMPS].index(l["champ"])))

    os.makedirs(args.out_dir, exist_ok=True)
    chemin_feuille = os.path.join(args.out_dir, "feuille_ab.csv")
    colonnes = ["id", "fichier", "champ", "valeur_attendue", "phrase_reference",
                "transcription_A", "transcription_B", "jugement_A", "jugement_B", "commentaire"]
    with open(chemin_feuille, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=colonnes, delimiter=";", quoting=csv.QUOTE_ALL)
        w.writeheader()
        for i, l in enumerate(lignes, start=1):
            textes = {"base": hyp_base[l["fichier"]], "finetune": hyp_ft[l["fichier"]]}
            premier = a_en_premier[l["fichier"]]
            second = "finetune" if premier == "base" else "base"
            w.writerow({"id": i, "fichier": l["fichier"], "champ": l["champ"],
                        "valeur_attendue": l["valeur_attendue"], "phrase_reference": l["phrase_reference"],
                        "transcription_A": textes[premier], "transcription_B": textes[second],
                        "jugement_A": "", "jugement_B": "", "commentaire": ""})

    chemin_cle = os.path.join(args.out_dir, "cle_secrete.csv")
    with open(chemin_cle, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f, delimiter=";", quoting=csv.QUOTE_ALL)
        etiquettes = bool(args.nom_base or args.nom_b)
        w.writerow(["fichier", "A"] + (["nom_base", "nom_b"] if etiquettes else []))
        for fichier in ordre:
            w.writerow([fichier, a_en_premier[fichier]]
                       + ([args.nom_base or "base", args.nom_b or "fine-tuné"] if etiquettes else []))

    identiques = sum(1 for l in lignes if hyp_base[l["fichier"]] == hyp_ft[l["fichier"]])
    par_champ = defaultdict(int)
    for l in lignes:
        par_champ[l["champ"]] += 1
    print(f"{len(lignes)} ligne(s) à juger sur {len(ordre)} fichier(s) : "
          + ", ".join(f"{c} {n}" for c, n in sorted(par_champ.items())))
    print(f"{identiques} ligne(s) où les deux modèles ont écrit exactement le même texte "
          f"(elles servent à mesurer ta constance).")
    print(f"\nFeuille à remplir : {chemin_feuille}")
    print(f"Clé (n'ouvre pas ce fichier avant d'avoir fini) : {chemin_cle}")
    print("\nPour chaque ligne, juge SÉPARÉMENT jugement_A et jugement_B : oui / partiel / non.")
    print("(oui = identifiable sans deviner ; partiel = trace plausible mais ambiguë ; non = disparu)")
    print("Tu juges si l'information est dans le texte, pas si le mot est bien orthographié.")


# ---------------------------------------------------------------------------
# score
# ---------------------------------------------------------------------------

def p_signe_bilateral(k_mieux, k_pire):
    n = k_mieux + k_pire
    if n == 0:
        return 1.0
    k = max(k_mieux, k_pire)
    return min(1.0, 2 * sum(comb(n, i) for i in range(k, n + 1)) / 2 ** n)


def kappa_cohen(a, b):
    """Accord corrigé du hasard entre deux séries de jugements."""
    n = len(a)
    cats = sorted(set(a) | set(b))
    po = sum(x == y for x, y in zip(a, b)) / n
    pe = sum((a.count(c) / n) * (b.count(c) / n) for c in cats)
    return po, ((po - pe) / (1 - pe)) if pe < 1 else float("nan")


def depouiller(feuille, cle):
    """Associe chaque jugement au bon modèle grâce à la clé. Renvoie
    (lignes valides, nombre de lignes incomplètes, valeurs non reconnues)."""
    valides, incompletes, inconnues = [], 0, set()
    for r in feuille:
        brut_a, brut_b = r["jugement_A"].strip(), r["jugement_B"].strip()
        if not brut_a or not brut_b:
            incompletes += 1
            continue
        ja, jb = NORMES.get(brut_a.lower()), NORMES.get(brut_b.lower())
        if ja is None or jb is None:
            inconnues.update(x for x, y in ((brut_a, ja), (brut_b, jb)) if y is None)
            continue
        if r["fichier"] not in cle:
            raise SystemExit(f"Fichier {r['fichier']!r} absent de la clé : feuille et clé ne correspondent pas.")
        if cle[r["fichier"]] == "base":
            base, ft = ja, jb
        else:
            base, ft = jb, ja
        valides.append({"fichier": r["fichier"], "champ": r["champ"], "base": base, "ft": ft,
                        "identique": r["transcription_A"] == r["transcription_B"], "ja": ja, "jb": jb})
    return valides, incompletes, inconnues


def taux(lignes, cle_modele):
    n = len(lignes)
    if not n:
        return 0, 0.0, 0.0
    oui = sum(1 for l in lignes if l[cle_modele] == "oui")
    large = sum(1 for l in lignes if l[cle_modele] in ("oui", "partiel"))
    return n, 100 * oui / n, 100 * large / n


def intervalle_par_fichier(lignes, n_boot=10000, graine=42):
    """Ré-échantillonne des FICHIERS (les champs d'un même fichier ne sont pas
    indépendants). Renvoie les IC95 % de l'écart de score moyen (échelle 0-2)
    et de l'écart de taux de 'oui', fine-tuné moins base."""
    par_fichier = defaultdict(lambda: [0, 0, 0, 0, 0])  # n, pts_base, pts_ft, oui_base, oui_ft
    for l in lignes:
        g = par_fichier[l["fichier"]]
        g[0] += 1; g[1] += POINTS[l["base"]]; g[2] += POINTS[l["ft"]]
        g[3] += l["base"] == "oui"; g[4] += l["ft"] == "oui"
    tab = np.array(list(par_fichier.values()), dtype=float)
    rng = np.random.default_rng(graine)
    ech = rng.integers(0, len(tab), size=(n_boot, len(tab)))
    s = tab[ech].sum(axis=1)
    d_pts = (s[:, 2] - s[:, 1]) / s[:, 0]
    d_oui = 100 * (s[:, 4] - s[:, 3]) / s[:, 0]
    return np.percentile(d_pts, [2.5, 97.5]), np.percentile(d_oui, [2.5, 97.5])


def score(args):
    if np is None:
        raise SystemExit("numpy est nécessaire (installé avec pandas) : pip install numpy")
    feuille = lire_csv(args.feuille)
    lignes_cle = lire_csv(args.cle)
    cle = {r["fichier"]: r["A"] for r in lignes_cle}
    lib0 = (lignes_cle[0].get("nom_base") or "").strip() or "base"
    lib1 = (lignes_cle[0].get("nom_b") or "").strip() or "fine-tuné"
    court1 = "ft" if lib1 == "fine-tuné" else lib1
    w0, w1 = max(11, len(lib0) + 6), max(9, len(court1) + 6)
    lignes, incompletes, inconnues = depouiller(feuille, cle)
    if incompletes:
        print(f"⚠️  {incompletes} ligne(s) sur {len(feuille)} pas (entièrement) jugée(s) — exclues.")
    if inconnues:
        print(f"⚠️  Valeur(s) non reconnue(s), lignes ignorées : {sorted(inconnues)} (attendu : oui / partiel / non)")
    if not lignes:
        raise SystemExit("Aucune ligne complète à dépouiller.")

    print(f"\n{len(lignes)} ligne(s) dépouillée(s). '% oui' = identifiable sans deviner ; "
          f"'% o+p' = en comptant aussi les traces partielles.\n")
    largeur = 56 + (w0 - 11) + (w1 - 9)
    print(f"{'champ':<10}{'n':>4} | {lib0 + ' % oui':>{w0}}{'% o+p':>7} | {court1 + ' % oui':>{w1}}{'% o+p':>7}")
    print("-" * largeur)
    champs = sorted({l["champ"] for l in lignes})
    for nom, sous in [(c, [l for l in lignes if l["champ"] == c]) for c in champs] + [("TOUS", lignes)]:
        n, bo, bl = taux(sous, "base")
        _, fo, fl = taux(sous, "ft")
        if nom == "TOUS":
            print("-" * largeur)
        print(f"{nom:<10}{n:>4} | {bo:>{w0 - 1}.0f}%{bl:>6.0f}% | {fo:>{w1 - 1}.0f}%{fl:>6.0f}%")

    mieux = sum(1 for l in lignes if POINTS[l["ft"]] > POINTS[l["base"]])
    pire = sum(1 for l in lignes if POINTS[l["ft"]] < POINTS[l["base"]])
    egal = len(lignes) - mieux - pire
    print(f"\nChamp par champ : {lib1} meilleur {mieux}, identique {egal}, pire {pire} "
          f"(test de signe bilatéral, égalités écartées : p = {p_signe_bilateral(mieux, pire):.2f})")
    (lp, hp), (lo, ho) = intervalle_par_fichier(lignes)
    d_pts = sum(POINTS[l["ft"]] - POINTS[l["base"]] for l in lignes) / len(lignes)
    d_oui = taux(lignes, "ft")[1] - taux(lignes, "base")[1]
    print(f"Écart de score moyen (0 = non, 1 = partiel, 2 = oui), {court1} − {lib0} : {d_pts:+.2f}  IC95 % [{lp:+.2f} ; {hp:+.2f}]")
    print(f"Écart du taux de 'oui', {court1} − {lib0} : {d_oui:+.1f} pts  IC95 % [{lo:+.1f} ; {ho:+.1f}]  (ré-échantillonnage par fichier)")

    # Question simple, plus stable que trois niveaux : l'information est-elle présente (oui ou partiel) ?
    m2 = sum(1 for l in lignes if l["ft"] != "non" and l["base"] == "non")
    p2 = sum(1 for l in lignes if l["ft"] == "non" and l["base"] != "non")
    print(f"Question simple (présent ou non) : {lib1} meilleur {m2}, pire {p2} "
          f"(test de signe : p = {p_signe_bilateral(m2, p2):.2f})")

    # Biais de position, indépendant du modèle : si une position l'emporte, l'écart entre modèles peut en venir.
    a_mieux = sum(1 for l in lignes if POINTS[l["ja"]] > POINTS[l["jb"]])
    b_mieux = sum(1 for l in lignes if POINTS[l["jb"]] > POINTS[l["ja"]])
    fichiers = {l["fichier"] for l in lignes}
    base_en_a = sum(1 for f in fichiers if cle[f] == "base")
    print(f"Position : A jugé meilleur sur {a_mieux} ligne(s), B sur {b_mieux} "
          f"(test de signe : p = {p_signe_bilateral(a_mieux, b_mieux):.2f}) ; "
          f"{lib0} en position A pour {base_en_a} fichier(s) sur {len(fichiers)}.")

    ident = [l for l in lignes if l["identique"]]
    if ident:
        desaccords = sum(1 for l in ident if l["ja"] != l["jb"])
        print(f"\nTa constance : sur {len(ident)} ligne(s) où les deux textes étaient identiques, "
              f"{desaccords} jugement(s) différent(s) entre A et B.")

    if args.anciens:
        anciens = {}
        for r in lire_csv(args.anciens):
            v = NORMES.get(r.get("recuperable", "").strip().lower())
            if v:
                anciens[(r["fichier"], r["champ"])] = v
        comparables = [(anciens[(l["fichier"], l["champ"])], l["base"]) for l in lignes
                       if (l["fichier"], l["champ"]) in anciens]
        if comparables:
            memes = sum(1 for a, b in comparables if a == b)
            desc0 = "modèle de base" if lib0 == "base" else f"modèle {lib0}"
            print(f"Ton premier jugement du {desc0}, re-jugé à l'aveugle : identique dans "
                  f"{memes} cas sur {len(comparables)} ({100*memes/len(comparables):.0f} %).")
            anc, nouv = [a for a, _ in comparables], [b for _, b in comparables]
            _, k3 = kappa_cohen(anc, nouv)
            po2, k2 = kappa_cohen([x != "non" for x in anc], [x != "non" for x in nouv])
            plus_severe = sum(1 for a, b in comparables if POINTS[b] < POINTS[a])
            plus_indulgent = sum(1 for a, b in comparables if POINTS[b] > POINTS[a])
            print(f"  kappa à 3 niveaux : {k3:.2f} ; question simple (présent ou non) : accord {100*po2:.0f} %, kappa {k2:.2f}")
            print(f"  re-jugement plus sévère {plus_severe} fois, plus indulgent {plus_indulgent} fois")
            print("  matrice (lignes = premier jugement, colonnes = jugement à l'aveugle) :")
            print(f"  {'':<9}" + "".join(f"{c:>9}" for c in ("oui", "partiel", "non")))
            for a in ("oui", "partiel", "non"):
                print(f"  {a:<9}" + "".join(f"{sum(1 for x, y in comparables if x == a and y == b):>9}" for b in ("oui", "partiel", "non")))

    print("\nLecture : 37 champs d'une seule voix, jugés par une seule personne. Un écart qui "
          "n'exclut pas zéro n'est pas démontré ; un écart qui l'exclut vaut pour CES enregistrements.")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="Préparer la feuille à l'aveugle")
    r.add_argument("--xlsx", required=True)
    r.add_argument("--feuille", default="Grille collecte")
    r.add_argument("--base", required=True, help="details_base.csv")
    r.add_argument("--finetune", default=None, help="résultats du second moteur (details_finetune.csv)")
    r.add_argument("--b-colonne-xlsx", default=None, help="OU colonne de la grille contenant les transcriptions du second moteur, ex. 'Transcription Whosper'")
    r.add_argument("--nom-base", default=None, help="libellé du premier moteur dans les résultats, ex. XLS-R")
    r.add_argument("--nom-b", default=None, help="libellé du second moteur dans les résultats, ex. Whosper")
    r.add_argument("--uniformiser", action="store_true", help="affiche les textes en minuscules sans ponctuation : recommandé pour comparer deux moteurs de styles différents")
    r.add_argument("--out-dir", required=True)
    r.add_argument("--graine", type=int, default=7)
    r.set_defaults(func=run)
    s = sub.add_parser("score", help="Dévoiler la clé et calculer")
    s.add_argument("--feuille", required=True)
    s.add_argument("--cle", required=True)
    s.add_argument("--anciens", default=None, help="ton ancien fichier de jugements (colonne 'recuperable'), pour mesurer ta constance")
    s.set_defaults(func=score)
    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
