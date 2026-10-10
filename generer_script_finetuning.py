#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Génère un script d'enregistrement pour le test de fine-tuning ciblé (1-2h),
couvrant systématiquement le vocabulaire commercial faible identifié par
champs_critiques.py : produits, couleurs, tailles, prix, zones.

Vocabulaire source : catalogue AD Shoes vu dans les vrais logs du pilote NOXA,
et les 52 vocaux existants (produits/couleurs déjà utilisés) — pas inventé.

Les TAILLES sont écrites EN TOUTES LETTRES (français), pas en chiffres :
le modèle XLS-R n'a aucun chiffre dans son vocabulaire de sortie (vérifié
sur son vocab.json et son modèle de langue), donc l'entraîner sur "42"
n'aurait aucun effet — seul "quarante-deux" peut être appris comme un mot
normal de son vocabulaire existant.

Stratégie de couverture : pas un produit croisement complet (ça ferait des
milliers de combinaisons) — chaque produit, couleur, taille, prix et zone
apparaît au moins une fois, réparti sur des phrases courtes variées,
construites à partir de formulations déjà entendues dans les vrais vocaux
(code-switching naturel, pas du wolof littéraire).

USAGE
  python generer_script_finetuning.py --n-phrases 120 --out script_finetuning.csv

Le fichier produit est un script à LIRE ET ENREGISTRER, pas encore des
transcriptions validées. Relis chaque phrase avant d'enregistrer — certaines
combinaisons générées automatiquement peuvent sonner moins naturelles que ce
que tu dirais spontanément ; corrige le texte librement avant de l'enregistrer,
l'essentiel est de garder le mot cible (produit/couleur/taille/prix/zone).
"""

import argparse
import csv
import random

PRODUITS = [
    "Air Force One", "Samba", "Moccasin Cuir Dakar Classic",
    "Chaussure de ville Ndakarou", "Sandale Cuir Samarra",
    "Chaussure Richelieu Luxe Officer", "Chaussure de sport Taggat Yaram",
    "veste", "pantalon jean", "basket", "sac",
]

COULEURS = ["blanc", "bleu", "rouge", "noir", "blanc pure", "marron"]

TAILLES_LETTRES = ["trente-neuf", "quarante", "quarante-et-un", "quarante-deux",
                    "quarante-trois", "quarante-quatre", "quarante-cinq"]

PRIX_LETTRES = ["dix mille", "douze mille", "quinze mille", "dix-sept mille",
                "vingt mille", "vingt-deux mille", "vingt-huit mille"]

ZONES = ["Médina", "Parcelles Assainies", "Mermoz", "Unité 15", "Pikine",
         "Yoff", "Ouakam", "Grand Dakar", "Colobane", "Rufisque"]

# Gabarits inspirés directement des formulations déjà entendues dans les 52
# vocaux réels (code-switching, pas de wolof littéraire inventé).
GABARITS_PRODUIT = [
    "Am ngeen {produit} yi ?",
    "Salam, am ngeen {produit} ?",
    "Bëgg naa {produit}, am na ?",
]
GABARITS_COULEUR = [
    "{produit} bi, am na ci couleur {couleur} ?",
    "Am ngeen ko ci {couleur} ?",
    "{couleur} bi mom am na ?",
]
GABARITS_TAILLE = [
    "Taille {taille} am na ci ?",
    "Pointure {taille}, am na ?",
    "{produit} bi, am na taille {taille} ?",
]
GABARITS_PRIX = [
    "Prix bi {prix} la, d'accord ?",
    "Waaw, total bi {prix} FCFA la.",
    "Combien {produit} bi ? -- {prix} la.",
]
GABARITS_ZONE = [
    "Meun ngeen ma ko livrer {zone} ?",
    "Mangi nekk {zone}, meun ngeen ma ko livrer ?",
    "Livrer ma ko ci {zone}.",
]


def generer(n_phrases, seed):
    rng = random.Random(seed)
    lignes = []

    def ajouter(gabarit, champ_principal, valeur, **kwargs):
        texte = gabarit.format(**kwargs)
        lignes.append({"phrase": texte, "champ_cible": champ_principal, "valeur_cible": valeur})

    # Couverture garantie : chaque valeur de chaque catégorie apparaît au
    # moins une fois, avant toute répétition aléatoire.
    for produit in PRODUITS:
        ajouter(rng.choice(GABARITS_PRODUIT), "produit", produit, produit=produit)
    for couleur in COULEURS:
        produit = rng.choice(PRODUITS)
        ajouter(rng.choice(GABARITS_COULEUR), "couleur", couleur, produit=produit, couleur=couleur)
    for taille in TAILLES_LETTRES:
        produit = rng.choice(PRODUITS)
        ajouter(rng.choice(GABARITS_TAILLE), "taille", taille, produit=produit, taille=taille)
    for prix in PRIX_LETTRES:
        produit = rng.choice(PRODUITS)
        ajouter(rng.choice(GABARITS_PRIX), "prix", prix, produit=produit, prix=prix)
    for zone in ZONES:
        ajouter(rng.choice(GABARITS_ZONE), "zone", zone, zone=zone)

    # Complète jusqu'à n_phrases avec des combinaisons supplémentaires
    # aléatoires, pour varier davantage sans exploser le volume.
    categories = [
        (GABARITS_PRODUIT, "produit", lambda: {"produit": rng.choice(PRODUITS)}),
        (GABARITS_COULEUR, "couleur", lambda: {"produit": rng.choice(PRODUITS), "couleur": rng.choice(COULEURS)}),
        (GABARITS_TAILLE, "taille", lambda: {"produit": rng.choice(PRODUITS), "taille": rng.choice(TAILLES_LETTRES)}),
        (GABARITS_PRIX, "prix", lambda: {"produit": rng.choice(PRODUITS), "prix": rng.choice(PRIX_LETTRES)}),
        (GABARITS_ZONE, "zone", lambda: {"zone": rng.choice(ZONES)}),
    ]
    phrases_deja_vues = {l["phrase"] for l in lignes}
    # Borne de sécurité : au-delà d'un nombre raisonnable d'essais, le pool de
    # combinaisons uniques restantes est probablement épuisé — on s'arrête
    # proprement plutôt que de boucler indéfiniment.
    tentatives_max = n_phrases * 50
    tentatives = 0
    while len(lignes) < n_phrases and tentatives < tentatives_max:
        tentatives += 1
        gabarits, champ, kwargs_fn = rng.choice(categories)
        kwargs = kwargs_fn()
        valeur = kwargs.get(champ, kwargs.get("produit"))
        gabarit = rng.choice(gabarits)
        phrase_candidate = gabarit.format(**kwargs)
        if phrase_candidate in phrases_deja_vues:
            continue  # on retire, sans compter cette tentative comme une phrase ajoutée
        phrases_deja_vues.add(phrase_candidate)
        lignes.append({"phrase": phrase_candidate, "champ_cible": champ, "valeur_cible": valeur})

    if len(lignes) < n_phrases:
        print(f"Avertissement : seulement {len(lignes)} phrases UNIQUES possibles avec le vocabulaire actuel "
              f"et les gabarits existants (demandé : {n_phrases}). Ajoute des gabarits ou du vocabulaire "
              f"dans le script pour aller au-delà sans répéter des phrases identiques.")

    if n_phrases < len(lignes):
        print(f"Avertissement : {n_phrases} demandé(s), mais {len(lignes)} nécessaires pour garantir "
              f"qu'au moins une phrase couvre chaque produit/couleur/taille/prix/zone. "
              f"Les {len(lignes)} phrases de couverture sont toutes générées quand même, sans troncature.")
        return lignes
    return lignes


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--n-phrases", type=int, default=120)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--out", default="script_finetuning.csv")
    args = p.parse_args()

    lignes = generer(args.n_phrases, args.seed)
    with open(args.out, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["phrase", "champ_cible", "valeur_cible"],
                            delimiter=";", quoting=csv.QUOTE_ALL)
        w.writeheader()
        w.writerows(lignes)

    par_champ = {}
    for l in lignes:
        par_champ[l["champ_cible"]] = par_champ.get(l["champ_cible"], 0) + 1
    print(f"{len(lignes)} phrase(s) générée(s) dans {args.out} :")
    for champ, n in sorted(par_champ.items()):
        print(f"  {champ:<10} {n}")
    print("\nRelis et ajuste chaque phrase avant d'enregistrer — garde le mot cible,")
    print("change la formulation si elle sonne artificielle à l'oreille.")


if __name__ == "__main__":
    main()
