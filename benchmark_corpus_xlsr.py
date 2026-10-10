#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Expérience 1 : mesurer XLS-R (le service de production, sur Render) contre des
extraits de corpus wolof déjà publiés — Kallaama, Urban Bus, WolBanking77 —
AVANT de construire quoi que ce soit sur les vidéos YouTube.

Ne télécharge PAS les corpus : ce script part du principe qu'un échantillon
(100-200 segments par corpus, comme discuté) a déjà été extrait manuellement
et placé dans un dossier, avec un manifeste CSV décrivant chaque segment.

USAGE
  1) Prépare un manifeste par corpus (voir manifeste_exemple.csv) :
       chemin_audio;reference;categorie;source
     - chemin_audio : chemin vers le fichier (wav/mp3/flac...), relatif au
       manifeste ou absolu
     - reference : la transcription de vérité terrain fournie par le corpus
     - categorie : optionnel — "chiffres", "noms_propres", "code_switching",
       "phrase_courte", "salutation", ou vide. Sert à ventiler les résultats,
       exactement le découpage proposé pour cette expérience.
     - source : optionnel — nom du corpus (kallaama, urban_bus, wolbanking77...)

  2) Lance :
       set XLSR_SERVICE_URL=https://noxa-xlsr-asr.onrender.com
       set ASR_SERVICE_SHARED_SECRET=ton_secret
       python benchmark_corpus_xlsr.py run --manifest kallaama.csv --manifest urban_bus.csv --out-dir resultats

  3) Le rapport (WER/CER global, par corpus, par catégorie) s'affiche et
     s'enregistre dans resultats/rapport.csv et resultats/details.csv.

LIMITES CONNUES
  - Le service XLS-R est séquentiel (voir le benchmark de concurrence de ce
    projet) : ce script appelle donc les segments un par un, pas en parallèle,
    pour ne pas fausser la mesure de latence ni saturer le service de
    production. Prévois du temps pour 100-200 segments par corpus.
  - WER/CER sont calculés après une normalisation minimale (minuscules,
    ponctuation retirée, espaces multiples réduits) — pas de normalisation
    linguistique poussée du wolof (contractions, variantes orthographiques).
    Un score élevé peut donc surestimer légèrement le vrai taux d'erreur.
  - Ce script mesure XLS-R seul. Il ne teste PAS le pipeline de correction par
    LLM (Expérience 2, à faire séparément une fois cette base établie).
"""

import argparse
import csv
import os
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.request
from collections import defaultdict

# ---------------------------------------------------------------------------
# Appel au service XLS-R — même contrat que les scripts précédents du projet
# (benchmark_xlsr_render.py) : POST /transcribe, secret partagé en Bearer.
# ---------------------------------------------------------------------------

def transcrire(chemin_audio, service_url, secret, timeout=90):
    with open(chemin_audio, "rb") as f:
        donnees = f.read()
    boundary = "----NoxaBenchmarkBoundary"
    nom_fichier = os.path.basename(chemin_audio)
    corps = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{nom_fichier}"\r\n'
        f"Content-Type: application/octet-stream\r\n\r\n"
    ).encode("utf-8") + donnees + f"\r\n--{boundary}--\r\n".encode("utf-8")

    headers = {"Content-Type": f"multipart/form-data; boundary={boundary}"}
    if secret:
        headers["Authorization"] = f"Bearer {secret}"

    req = urllib.request.Request(f"{service_url.rstrip('/')}/transcribe", data=corps,
                                  headers=headers, method="POST")
    debut = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            import json
            data = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"HTTP {e.code}: {e.read().decode('utf-8', errors='replace')[:300]}")
    except urllib.error.URLError as e:
        raise RuntimeError(f"Réseau: {e.reason}")
    latence_ms = int((time.time() - debut) * 1000)
    return data.get("text", "") or "", latence_ms, data

# ---------------------------------------------------------------------------
# Normalisation légère + WER / CER (distance de Levenshtein)
# ---------------------------------------------------------------------------

def normaliser(texte):
    texte = unicodedata.normalize("NFKC", str(texte or ""))
    texte = texte.lower()
    texte = re.sub(r"[^\w\sàâäéèêëîïôöùûüç']", " ", texte, flags=re.UNICODE)
    texte = re.sub(r"\s+", " ", texte).strip()
    return texte


def distance_levenshtein(a, b):
    """Distance d'édition standard entre deux séquences (liste de mots ou de
    caractères). O(len(a)*len(b)) — largement suffisant pour des phrases."""
    n, m = len(a), len(b)
    if n == 0: return m
    if m == 0: return n
    prev = list(range(m + 1))
    for i in range(1, n + 1):
        curr = [i] + [0] * m
        for j in range(1, m + 1):
            cout = 0 if a[i - 1] == b[j - 1] else 1
            curr[j] = min(prev[j] + 1, curr[j - 1] + 1, prev[j - 1] + cout)
        prev = curr
    return prev[m]


def wer(reference, hypothese):
    ref_mots = normaliser(reference).split()
    hyp_mots = normaliser(hypothese).split()
    if not ref_mots:
        return 0.0 if not hyp_mots else 1.0
    return distance_levenshtein(ref_mots, hyp_mots) / len(ref_mots)


def cer(reference, hypothese):
    ref_car = list(normaliser(reference).replace(" ", ""))
    hyp_car = list(normaliser(hypothese).replace(" ", ""))
    if not ref_car:
        return 0.0 if not hyp_car else 1.0
    return distance_levenshtein(ref_car, hyp_car) / len(ref_car)

# ---------------------------------------------------------------------------
# Lecture du manifeste (séparateur ; ou , détecté automatiquement, comme dans
# le script ab_test_wolof.py — même discipline que le reste du projet)
# ---------------------------------------------------------------------------

def lire_manifeste(chemin):
    with open(chemin, "rb") as f:
        brut = f.read()
    if brut[:2] in (b"\xff\xfe", b"\xfe\xff"):
        contenu = brut.decode("utf-16")
    else:
        try:
            contenu = brut.decode("utf-8-sig")
        except UnicodeDecodeError:
            contenu = brut.decode("cp1252", errors="replace")
    lignes = contenu.splitlines()
    if lignes and re.fullmatch(r"sep=.", lignes[0].strip()):
        contenu = "\n".join(lignes[1:])
        lignes = contenu.splitlines()
    premiere = lignes[0] if lignes else ""
    comptes = {";": premiere.count(";"), ",": premiere.count(",")}
    delim = max(comptes, key=comptes.get)
    if comptes[delim] == 0:
        raise SystemExit(f"Séparateur non reconnu dans {chemin!r} (ni ';' ni ',' dans l'en-tête).")
    lecteur = csv.DictReader(lignes, delimiter=delim)
    requis = {"chemin_audio", "reference"}
    manquants = requis - set(lecteur.fieldnames or [])
    if manquants:
        raise SystemExit(f"{chemin} : colonnes manquantes {manquants}. Attendu au minimum : chemin_audio;reference")
    return list(lecteur), os.path.dirname(os.path.abspath(chemin))

# ---------------------------------------------------------------------------
# Commande `run`
# ---------------------------------------------------------------------------

def run(args):
    service_url = os.environ.get("XLSR_SERVICE_URL")
    secret = os.environ.get("ASR_SERVICE_SHARED_SECRET")
    if not service_url:
        raise SystemExit("Variable XLSR_SERVICE_URL absente. set XLSR_SERVICE_URL=https://...")

    os.makedirs(args.out_dir, exist_ok=True)
    details = []
    total = 0
    for chemin_manifeste in args.manifest:
        lignes, base_dir = lire_manifeste(chemin_manifeste)
        nom_corpus_defaut = os.path.splitext(os.path.basename(chemin_manifeste))[0]
        total += len(lignes)
        print(f"{chemin_manifeste} : {len(lignes)} segment(s)")

    n = 0
    for chemin_manifeste in args.manifest:
        lignes, base_dir = lire_manifeste(chemin_manifeste)
        nom_corpus_defaut = os.path.splitext(os.path.basename(chemin_manifeste))[0]
        for ligne in lignes:
            n += 1
            chemin_audio = ligne["chemin_audio"]
            if not os.path.isabs(chemin_audio):
                chemin_audio = os.path.join(base_dir, chemin_audio)
            source = (ligne.get("source") or nom_corpus_defaut).strip()
            categorie = (ligne.get("categorie") or "").strip() or "non_categorise"
            reference = ligne["reference"]

            print(f"[{n}/{total}] {source} / {categorie} — {os.path.basename(chemin_audio)}", flush=True)

            if not os.path.isfile(chemin_audio):
                print(f"    fichier introuvable, ignoré : {chemin_audio}")
                details.append({"source": source, "categorie": categorie, "chemin_audio": chemin_audio,
                                 "reference": reference, "hypothese": "", "wer": "", "cer": "",
                                 "latence_ms": "", "erreur": "fichier introuvable"})
                continue

            try:
                hypothese, latence_ms, _ = transcrire(chemin_audio, service_url, secret)
                w, c = wer(reference, hypothese), cer(reference, hypothese)
                details.append({"source": source, "categorie": categorie, "chemin_audio": chemin_audio,
                                 "reference": reference, "hypothese": hypothese,
                                 "wer": f"{w:.4f}", "cer": f"{c:.4f}", "latence_ms": latence_ms, "erreur": ""})
            except Exception as e:
                print(f"    échec : {e}")
                details.append({"source": source, "categorie": categorie, "chemin_audio": chemin_audio,
                                 "reference": reference, "hypothese": "", "wer": "", "cer": "",
                                 "latence_ms": "", "erreur": str(e)[:200]})
            time.sleep(args.sleep)

    colonnes = ["source", "categorie", "chemin_audio", "reference", "hypothese", "wer", "cer", "latence_ms", "erreur"]
    chemin_details = os.path.join(args.out_dir, "details.csv")
    with open(chemin_details, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=colonnes, delimiter=";", quoting=csv.QUOTE_ALL)
        w.writeheader()
        w.writerows(details)

    rapport = generer_rapport(details)
    chemin_rapport = os.path.join(args.out_dir, "rapport.csv")
    with open(chemin_rapport, "w", encoding="utf-8-sig", newline="") as f:
        if rapport:
            w = csv.DictWriter(f, fieldnames=list(rapport[0].keys()), delimiter=";", quoting=csv.QUOTE_ALL)
            w.writeheader()
            w.writerows(rapport)

    afficher_rapport(rapport)
    print(f"\nDétail ligne par ligne : {chemin_details}")
    print(f"Rapport agrégé         : {chemin_rapport}")


def wer_cer_corpus(lignes):
    """WER/CER agrégés au niveau du corpus : somme des erreurs d'édition sur
    tous les segments, divisée par la somme des mots/caractères de référence
    sur tous les segments — PAS la moyenne des ratios individuels.
    Différence concrète observée sur Kallaama : un segment de 5 mots de
    référence avec 1 seule substitution pèse pour 1.0 dans mean(WER_i), le
    même poids qu'un segment de 50 mots avec 25 erreurs (WER 0.5) — alors
    qu'au niveau du corpus entier, 1 erreur sur 5 mots compte pour beaucoup
    moins que 25 erreurs sur 50. Les segments courts n'écrasent donc plus la
    mesure ici."""
    total_err_mots = total_mots = total_err_car = total_car = 0
    for l in lignes:
        ref_mots = normaliser(l["reference"]).split()
        hyp_mots = normaliser(l["hypothese"]).split()
        total_err_mots += distance_levenshtein(ref_mots, hyp_mots)
        total_mots += len(ref_mots)
        ref_car = list(normaliser(l["reference"]).replace(" ", ""))
        hyp_car = list(normaliser(l["hypothese"]).replace(" ", ""))
        total_err_car += distance_levenshtein(ref_car, hyp_car)
        total_car += len(ref_car)
    wer_corpus = total_err_mots / total_mots if total_mots else 0.0
    cer_corpus = total_err_car / total_car if total_car else 0.0
    return wer_corpus, cer_corpus


def generer_rapport(details):
    groupes = defaultdict(list)
    for d in details:
        if d["erreur"]:
            continue
        groupes[(d["source"], "TOUTES")].append(d)
        groupes[(d["source"], d["categorie"])].append(d)
        groupes[("TOUS", "TOUTES")].append(d)

    rapport = []
    for (source, categorie), lignes in sorted(groupes.items()):
        wers = [float(l["wer"]) for l in lignes]
        cers = [float(l["cer"]) for l in lignes]
        lats = [int(l["latence_ms"]) for l in lignes]
        wer_corpus, cer_corpus = wer_cer_corpus(lignes)
        rapport.append({
            "source": source, "categorie": categorie, "n": len(lignes),
            "wer_moyen": f"{sum(wers)/len(wers):.4f}",
            "cer_moyen": f"{sum(cers)/len(cers):.4f}",
            "wer_corpus": f"{wer_corpus:.4f}",
            "cer_corpus": f"{cer_corpus:.4f}",
            "latence_moy_ms": f"{sum(lats)/len(lats):.0f}",
        })
    return rapport


def afficher_rapport(rapport):
    print(f"\n{'source':<14}{'categorie':<16}{'n':>5}{'WER moy':>10}{'CER moy':>10}{'WER corp.':>11}{'CER corp.':>11}{'latence ms':>12}")
    print("-" * 90)
    for r in rapport:
        print(f"{r['source']:<14}{r['categorie']:<16}{r['n']:>5}{r['wer_moyen']:>10}{r['cer_moyen']:>10}"
              f"{r['wer_corpus']:>11}{r['cer_corpus']:>11}{r['latence_moy_ms']:>12}")
    print("\nLecture : 'WER/CER moy' = moyenne des ratios par segment (un segment court à 100%")
    print("d'erreur pèse autant qu'un long) ; 'WER/CER corp.' = erreurs totales / mots-caractères")
    print("totaux de référence sur tout le corpus (un segment court pèse proportionnellement à sa")
    print("taille réelle). Les deux peuvent diverger nettement — regarde les deux avant de conclure.")
    print("Compare aux vocaux NOXA réels pour juger si ces corpus publics sont représentatifs,")
    print("avant d'investir dans autre chose.")

# ---------------------------------------------------------------------------
# Commande `exemple` — génère un manifeste vide prêt à remplir
# ---------------------------------------------------------------------------

def exemple(args):
    contenu = (
        "chemin_audio;reference;categorie;source\n"
        "audio/seg_0001.wav;taille 42 ci couleur noir;chiffres;kallaama\n"
        "audio/seg_0002.wav;nanga def dinama dem marse;salutation;urban_bus\n"
        "audio/seg_0003.wav;dama bëgg retiré xaalis bi;code_switching;wolbanking77\n"
    )
    with open(args.out, "w", encoding="utf-8-sig", newline="") as f:
        f.write(contenu)
    print(f"Manifeste d'exemple écrit : {args.out}")
    print("Colonnes : chemin_audio;reference;categorie;source — categorie et source sont optionnelles.")


def main():
    p = argparse.ArgumentParser(description="Expérience 1 : XLS-R contre des corpus wolof existants")
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="Lancer le benchmark")
    r.add_argument("--manifest", action="append", required=True, help="un ou plusieurs fichiers manifeste (répétable)")
    r.add_argument("--out-dir", default="resultats")
    r.add_argument("--sleep", type=float, default=1.0, help="pause entre deux appels (s) — service séquentiel")
    r.set_defaults(func=run)

    e = sub.add_parser("exemple", help="Générer un manifeste vide à remplir")
    e.add_argument("--out", default="manifeste_exemple.csv")
    e.set_defaults(func=exemple)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
