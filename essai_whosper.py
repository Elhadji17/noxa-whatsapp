#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Mesure l'essai Whosper déployé sur Modal (voir modal_whosper.py). Trois commandes :

  calibrer : le paquet 'whosper' ne dit pas quelle langue il impose au modèle (Whisper n'a pas de
             code pour le wolof). On essaie plusieurs réglages sur quelques vocaux et on garde celui
             dont les textes ressemblent le plus à ceux de TA grille (obtenus avec le paquet d'origine).
  chaud    : tous les vocaux, un par un, sur un conteneur déjà prêt. Latence (client et serveur),
             fidélité à la grille, qualité contre les références, coût estimé d'un vocal.
  froid    : attend que le conteneur s'éteigne, puis envoie UN vocal, plusieurs fois. Mesure ce que
             vivrait un client dont le message arrive après un moment d'inactivité.

Trois façons de lire les résultats
  - Fidélité : le WER/CER de la nouvelle sortie CONTRE la sortie de ta grille. Proche de zéro = la
    conversion et le réglage reproduisent bien le modèle d'origine. Ce n'est PAS une mesure de qualité.
  - Qualité : WER/CER contre tes références, avec les mêmes formules que d'habitude. Pour mémoire, la
    sortie d'origine donnait 0,7249 / 0,4402 ; la commande 'chaud' recalcule ce chiffre sur la grille.
  - Latence : seules les mesures 'froid' répondent à « combien de temps un client attend-il ? ».

Les tarifs ci-dessous sont ceux relevés en juillet 2026 pour Modal (T4) ; le processeur et la mémoire
sont une hypothèse (1 cœur, 4 Gio). À confirmer sur la page de tarifs de Modal.

USAGE (dans le dossier noxa-whatsapp, à côté de benchmark_corpus_xlsr.py)
  python essai_whosper.py calibrer --xlsx "NOXA_grille_collecte_dataset.xlsx" --audio Vocaux2
  python essai_whosper.py chaud    --xlsx "NOXA_grille_collecte_dataset.xlsx" --audio Vocaux2 --langue fr
  python essai_whosper.py froid    --xlsx "NOXA_grille_collecte_dataset.xlsx" --audio Vocaux2 --langue fr --attente 90
"""

import argparse
import csv
import os
import sys
import time

try:
    import benchmark_corpus_xlsr as bc
except ImportError:
    raise SystemExit("benchmark_corpus_xlsr.py doit être dans le même dossier que ce script.")

APP_NAME = "noxa-whosper-essai"
TARIF_GPU_T4_S = 0.000164
TARIF_CPU_MEM_S = 0.0000131 * 1 + 0.00000222 * 4
TARIF_S = TARIF_GPU_T4_S + TARIF_CPU_MEM_S
REFERENCE_ORIGINALE = (0.7249, 0.4402)     # WER, CER corpus de Whosper (grille) contre les références, mesurés plus tôt
LANGUES_PAR_DEFAUT = "auto,fr,sw,yo,sn,ln,en"


def texte_cellule(valeur):
    if valeur is None or (isinstance(valeur, float) and valeur != valeur):
        return ""
    return str(valeur).strip()


def charger_donnees(xlsx, feuille, dossier_audio, colonne_whosper="Transcription Whosper"):
    import pandas as pd
    df = pd.read_excel(xlsx, sheet_name=feuille)
    if colonne_whosper not in df.columns:
        raise SystemExit(f"Colonne {colonne_whosper!r} introuvable. Colonnes : " + ", ".join(repr(c) for c in df.columns))
    donnees, absents = [], []
    for _, l in df.iterrows():
        if "Exemple fictif" in texte_cellule(l.get("Notes")) or texte_cellule(l.get("Décision dataset")) == "Exclu":
            continue
        nom, grille = texte_cellule(l.get("Fichier audio")), texte_cellule(l.get(colonne_whosper))
        ref = texte_cellule(l.get("Transcription corrigée"))
        if not nom or not grille or not ref:
            continue
        chemin = os.path.join(dossier_audio, nom)
        if not os.path.isfile(chemin):
            absents.append(nom)
            continue
        donnees.append({"fichier": nom, "chemin": chemin, "reference": ref, "grille": grille})
    donnees.sort(key=lambda d: d["fichier"])
    if absents:
        print(f"⚠️  {len(absents)} fichier(s) audio introuvable(s) dans {dossier_audio!r} : {absents[:5]}{' …' if len(absents) > 5 else ''}")
    if not donnees:
        raise SystemExit("Aucun vocal utilisable : vérifie --xlsx et --audio.")
    return donnees


def centile(valeurs, p):
    if not valeurs:
        return float("nan")
    v = sorted(valeurs)
    k = (len(v) - 1) * p / 100
    f = int(k)
    c = min(f + 1, len(v) - 1)
    return v[f] + (v[c] - v[f]) * (k - f)


def resume_latence(valeurs):
    return f"médiane {centile(valeurs, 50):.2f} s · p90 {centile(valeurs, 90):.2f} s · max {max(valeurs):.2f} s" if valeurs else "aucune mesure"


def wer_cer(paires):
    """paires : liste de (reference, hypothese) -> (WER corpus, CER corpus), mêmes formules que le benchmark."""
    lignes = [{"reference": r, "hypothese": h} for r, h in paires]
    return bc.wer_cer_corpus(lignes)


def obtenir_transcripteur():
    try:
        import modal
    except ImportError:
        raise SystemExit("Le paquet 'modal' n'est pas installé : pip install modal  (puis : modal setup)")
    try:
        return modal.Cls.from_name(APP_NAME, "Transcripteur")()
    except Exception as e:
        raise SystemExit(f"Impossible de retrouver l'application {APP_NAME!r} : {e}\n"
                         f"As-tu lancé  modal deploy modal_whosper.py  ?")


def appeler(transcripteur, octets, langue, beam):
    t0 = time.perf_counter()
    try:
        res = transcripteur.transcrire.remote(octets, None if langue in (None, "", "auto") else langue, beam)
    except Exception as e:
        raise SystemExit(f"L'appel à Modal a échoué : {type(e).__name__} : {e}\n"
                         f"Regarde les journaux :  modal app logs {APP_NAME}")
    return res, time.perf_counter() - t0


def lire(chemin):
    with open(chemin, "rb") as f:
        return f.read()


# ---------------------------------------------------------------------------
# calibrer
# ---------------------------------------------------------------------------

def commande_calibrer(args, transcripteur=None):
    donnees = charger_donnees(args.xlsx, args.feuille, args.audio)[:args.n]
    langues = [x.strip() for x in args.langues.split(",") if x.strip()]
    t = transcripteur or obtenir_transcripteur()
    print(f"{len(donnees)} vocaux × {len(langues)} réglages de langue. Le tout premier appel peut être un démarrage à froid (ignoré dans les mesures).")
    appeler(t, lire(donnees[0]["chemin"]), langues[0], args.beam)         # réchauffe le conteneur
    resultats = {}
    for langue in langues:
        paires, erreurs, derniere_erreur = [], 0, ""
        for d in donnees:
            res, _ = appeler(t, lire(d["chemin"]), langue, args.beam)
            if res.get("erreur"):
                erreurs += 1
                derniere_erreur = res["erreur"]
                continue
            paires.append((d["grille"], res["texte"]))
        resultats[langue] = (paires, erreurs, derniere_erreur)
    print(f"\n{'langue':<8}{'réussis':>9}{'WER vs grille':>15}{'CER vs grille':>15}{'identiques':>12}")
    classement = []
    for langue, (paires, erreurs, derniere) in resultats.items():
        if not paires:
            print(f"{langue:<8}{0:>9}   (tous en erreur : {derniere[:90]})")
            continue
        w, c = wer_cer(paires)
        identiques = sum(1 for r, h in paires if bc.normaliser(r) == bc.normaliser(h))
        classement.append((c, langue))
        print(f"{langue:<8}{len(paires):>9}{w:>15.4f}{c:>15.4f}{identiques:>8}/{len(paires)}"
              + (f"   ({erreurs} en erreur)" if erreurs else ""))
    if not classement:
        raise SystemExit("Aucun réglage n'a fonctionné : regarde les messages d'erreur ci-dessus.")
    classement.sort()
    meilleur_c, meilleur = classement[0]
    print(f"\nRéglage qui reproduit le mieux ta grille : --langue {meilleur}  (CER {meilleur_c:.4f} contre la grille)")
    if meilleur_c > 0.30:
        print("⚠️  Aucun réglage ne reproduit vraiment la grille (CER > 0,30). Soit la grille a été produite avec d'autres paramètres "
              "(beam, prompt...), soit la fusion ou la conversion s'écarte du modèle d'origine : ne conclus rien sur la qualité avant d'avoir compris.")
    return meilleur


# ---------------------------------------------------------------------------
# chaud
# ---------------------------------------------------------------------------

def commande_chaud(args, transcripteur=None):
    donnees = charger_donnees(args.xlsx, args.feuille, args.audio)
    t = transcripteur or obtenir_transcripteur()
    print(f"Réchauffement du conteneur ({args.echauffement} appels non comptés)…")
    for _ in range(args.echauffement):
        appeler(t, lire(donnees[0]["chemin"]), args.langue, args.beam)
    lignes, erreurs = [], []
    for d in donnees:
        res, latence = appeler(t, lire(d["chemin"]), args.langue, args.beam)
        if res.get("erreur"):
            erreurs.append((d["fichier"], res["erreur"]))
            continue
        lignes.append({**d, "nouveau": res["texte"], "latence_client_s": latence, "inference_s": res["temps_inference_s"],
                       "decodage_s": res["temps_decodage_s"], "duree_audio_s": res["duree_audio_s"],
                       "froid": res["conteneur_froid"], "backend": res["backend"], "langue_detectee": res.get("langue_detectee")})
    if erreurs:
        print(f"⚠️  {len(erreurs)} vocal(aux) en erreur, ignorés : {erreurs[:3]}")
    if not lignes:
        raise SystemExit("Aucun vocal traité.")
    print(f"\n=== {len(lignes)} vocaux, voie '{lignes[0]['backend']}', langue {args.langue!r}, beam {args.beam} ===")
    print(f"Latence vue du client : {resume_latence([l['latence_client_s'] for l in lignes])}")
    print(f"Inférence côté serveur : {resume_latence([l['inference_s'] for l in lignes])}")
    froids = sum(1 for l in lignes if l["froid"])
    if froids:
        print(f"⚠️  {froids} appel(s) mesuré(s) sur un conteneur froid : les médianes en sont peu affectées, pas le maximum.")
    serveur = [l["inference_s"] + l["decodage_s"] for l in lignes]
    print(f"Durée moyenne des vocaux : {sum(l['duree_audio_s'] for l in lignes) / len(lignes):.1f} s")
    print(f"Coût GPU estimé d'un vocal sur conteneur chaud : {sum(serveur) / len(serveur) * TARIF_S:.5f} $ "
          f"({sum(serveur) / len(serveur):.2f} s × {TARIF_S:.6f} $/s)")

    w_f, c_f = wer_cer([(l["grille"], l["nouveau"]) for l in lignes])
    identiques = sum(1 for l in lignes if bc.normaliser(l["grille"]) == bc.normaliser(l["nouveau"]))
    w_q, c_q = wer_cer([(l["reference"], l["nouveau"]) for l in lignes])
    w_g, c_g = wer_cer([(l["reference"], l["grille"]) for l in lignes])
    print(f"\nFidélité à la grille (WER / CER de la nouvelle sortie contre l'ancienne) : {w_f:.4f} / {c_f:.4f} ; "
          f"{identiques}/{len(lignes)} textes identiques")
    print(f"Qualité contre les références : nouvelle sortie {w_q:.4f} / {c_q:.4f} ; sortie de la grille sur les mêmes fichiers {w_g:.4f} / {c_g:.4f}")
    print(f"(valeur d'origine mesurée plus tôt sur les 52 : {REFERENCE_ORIGINALE[0]} / {REFERENCE_ORIGINALE[1]})")
    ecart = c_q - c_g
    if abs(ecart) > 0.03:
        print(f"⚠️  Écart de CER de {100 * ecart:+.1f} points avec la sortie d'origine : la conversion ou le réglage change le résultat.")
    else:
        print("Écart de CER avec la sortie d'origine inférieur à 3 points : la conversion semble préserver la qualité (sur ces fichiers).")

    chemin = os.path.join(args.out_dir, "essai_whosper_chaud.csv")
    os.makedirs(args.out_dir, exist_ok=True)
    with open(chemin, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f, delimiter=";", quoting=csv.QUOTE_ALL)
        w.writerow(["fichier", "reference", "sortie_grille", "nouvelle_sortie", "latence_client_s", "inference_s", "decodage_s", "duree_audio_s", "langue_detectee"])
        for l in lignes:
            w.writerow([l["fichier"], l["reference"], l["grille"], l["nouveau"], f"{l['latence_client_s']:.3f}",
                        l["inference_s"], l["decodage_s"], l["duree_audio_s"], l["langue_detectee"]])
    print(f"\nDétail : {chemin}")
    return lignes


# ---------------------------------------------------------------------------
# froid
# ---------------------------------------------------------------------------

def commande_froid(args, transcripteur=None, dormir=time.sleep):
    donnees = charger_donnees(args.xlsx, args.feuille, args.audio)
    d = donnees[len(donnees) // 2]
    octets = lire(d["chemin"])
    t = transcripteur or obtenir_transcripteur()
    print(f"Vocal de test : {d['fichier']}. Chaque mesure attend {args.attente} s (le conteneur doit avoir dépassé son délai de veille, "
          f"{args.veille} s dans le déploiement par défaut).")
    mesures = []
    for i in range(1, args.repetitions + 1):
        print(f"  attente {args.attente} s avant la mesure {i}/{args.repetitions}…")
        dormir(args.attente)
        res, latence = appeler(t, octets, args.langue, args.beam)
        if res.get("erreur"):
            print(f"  mesure {i} : erreur {res['erreur']}")
            continue
        mesures.append((latence, res))
        etat = "FROID" if res["conteneur_froid"] else "chaud (pas de démarrage à froid : attente trop courte ?)"
        print(f"  mesure {i} : {latence:.1f} s vue du client — conteneur {etat} ; chargement du modèle {res['chargement_modele_s']} s ; "
              f"inférence {res['temps_inference_s']} s")
    froids = [l for l, r in mesures if r["conteneur_froid"]]
    if not froids:
        raise SystemExit("Aucun démarrage à froid observé : augmente --attente au-delà du délai de veille du déploiement.")
    med = centile(froids, 50)
    print(f"\n=== Démarrage à froid sur {len(froids)} mesure(s) : {resume_latence(froids)} ===")
    cout = (med + args.veille) * TARIF_S
    print(f"Coût estimé d'un vocal arrivant sur un conteneur éteint : ({med:.0f} s + {args.veille} s de veille) × {TARIF_S:.6f} $/s = {cout:.4f} $")
    print("Ce coût disparaît en partie quand plusieurs vocaux arrivent à la suite : ils partagent le même conteneur.")
    return froids


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    def commun(s):
        s.add_argument("--xlsx", required=True)
        s.add_argument("--feuille", default="Grille collecte")
        s.add_argument("--audio", required=True, help="dossier contenant les .ogg (ex. Vocaux2)")
        s.add_argument("--beam", type=int, default=1)

    c = sub.add_parser("calibrer")
    commun(c)
    c.add_argument("--langues", default=LANGUES_PAR_DEFAUT, help="réglages à essayer, séparés par des virgules ('auto' = détection)")
    c.add_argument("--n", type=int, default=12, help="nombre de vocaux utilisés pour le calibrage")
    h = sub.add_parser("chaud")
    commun(h)
    h.add_argument("--langue", default="auto")
    h.add_argument("--echauffement", type=int, default=2)
    h.add_argument("--out-dir", default="essai_whosper")
    f = sub.add_parser("froid")
    commun(f)
    f.add_argument("--langue", default="auto")
    f.add_argument("--attente", type=int, default=90, help="secondes d'attente avant chaque mesure (doit dépasser le délai de veille)")
    f.add_argument("--veille", type=int, default=60, help="délai de veille du déploiement (pour le calcul du coût)")
    f.add_argument("--repetitions", type=int, default=3)
    args = p.parse_args()
    {"calibrer": commande_calibrer, "chaud": commande_chaud, "froid": commande_froid}[args.cmd](args)


if __name__ == "__main__":
    main()
