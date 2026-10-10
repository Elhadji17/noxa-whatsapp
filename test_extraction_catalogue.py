#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Mesure ce que le VRAI système récupère, sans juge humain.

Pour chaque vocal, on donne à Claude la transcription automatique et le
catalogue, on lui demande d'en extraire produit / couleur / taille / prix / zone,
et on compare automatiquement aux valeurs attendues de la grille. Quatre
conditions, sur exactement les mêmes champs :

  reference : le texte EXACT de la phrase (CONTRÔLE : doit être proche de 100 %.
              Sinon, c'est la consigne ou la notation qui limite, pas le moteur.)
  xlsr      : transcription XLS-R seule
  whosper   : transcription Whosper seule
  deux      : les deux transcriptions, sans dire laquelle vient de quel moteur
  vide      : AUCUN texte (CONTRÔLE inverse : doit donner « absent » partout. Tout « juste »
              ici vient du catalogue ou du hasard, pas de la transcription.)

Chaque champ reçoit un résultat :
  juste    = exactement les valeurs attendues
  partiel  = au moins une valeur attendue, mais pas toutes ou avec une valeur en trop
  absent   = Claude répond null (il ne devine pas)
  faux     = une valeur, mais aucune de celles attendues
Le résultat « faux » est le plus coûteux en vrai : une mauvaise valeur affirmée est
pire qu'une valeur absente. Le rapport indique aussi combien de « faux » ont été
affirmés avec une confiance haute.

Limites à garder en tête
  - Le catalogue donné à Claude (articles, couleurs, zones) est celui du projet ; il
    pèse sur le résultat. La liste de zones contient celles du test : c'est plus
    facile que dans la vraie vie, pour toutes les conditions à égalité. Modifiable
    avec --catalogue ; la condition « vide » mesure ce que le catalogue seul donne.
  - Même voix, mêmes 52 vocaux que d'habitude : seulement des champs, pas des clients.
  - Le comparateur de produits traite « chaussures » comme générique : répondre un
    modèle précis quand le client dit « chaussures » compte comme partiel, pas juste.
  - Les valeurs attendues viennent de la grille : si une cellule est fausse, la note l'est.

AJOUTER UN MOTEUR (par exemple un modèle ajusté ailleurs) :
  --moteur nouveau=details_nouveau.csv --conditions nouveau,nouveau+whosper,nouveau+xlsr,nouveau+xlsr+whosper
Chaque nouvelle condition est comparée à xlsr, whosper et deux. Les réponses déjà obtenues pour les conditions
existantes restent valables (même consigne, même cache) tant que tu gardes le même --out-dir et --repetitions.

Si le modèle refuse le paramètre 'temperature' (c'est le cas de claude-sonnet-5-5), les réponses sont
échantillonnées au hasard : deux lancements peuvent différer. Utilise alors --repetitions 3 : le rapport
montre la dispersion entre répétitions, et les comparaisons portent sur la moyenne. Une réponse illisible
est comptée « absent » ; le rapport l'annonce et en affiche le texte brut (reponses_illisibles.txt).

USAGE  (Windows, invite de commandes)
  set ANTHROPIC_API_KEY=sk-ant-...
  python test_extraction_catalogue.py --xlsx "NOXA_grille_collecte_dataset.xlsx" --base details_base.csv --out-dir extraction --limite 3
  python test_extraction_catalogue.py --xlsx "NOXA_grille_collecte_dataset.xlsx" --base details_base.csv --out-dir extraction
  (le premier lancement, avec --limite 3, sert à vérifier que tout marche pour quelques centimes ;
   --dry-run affiche les consignes sans rien envoyer ; les réponses sont gardées dans
   extraction\\cache_reponses.json : relancer ne repaie pas les appels déjà faits.)
"""

import argparse
import csv
import hashlib
import json
import os
import random
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.request
from collections import defaultdict
from math import comb

try:
    import numpy as np
except ImportError:
    np = None

CHAMPS = [("Produit", "produit"), ("Taille (entendue)", "taille"), ("Couleur", "couleur"),
          ("Prix (entendu)", "prix"), ("Zone livraison", "zone")]
CODES = [c for _, c in CHAMPS]
CONDITIONS = ["reference", "xlsr", "whosper", "deux", "vide"]
MOTEURS_DE_BASE = ["xlsr", "whosper"]
NOMS_RESERVES = set(CONDITIONS) | {"deux"}


def condition_valide(cond, moteurs_extra):
    """Condition de base, moteur supplémentaire seul, ou combinaison de 2 à 3 moteurs ('a+b', 'a+b+c')."""
    if cond in CONDITIONS:
        return True
    noms = MOTEURS_DE_BASE + list(moteurs_extra)
    if cond in noms:
        return True
    if "+" in cond:
        membres = cond.split("+")
        return 2 <= len(membres) <= 3 and len(set(membres)) == len(membres) and all(m in noms for m in membres)
    return False

CATALOGUE_DEFAUT = {
    "produits": ["Air Force One", "Samba", "Moccasin Cuir Dakar Classic", "Chaussure de ville Ndakarou",
                 "Sandale Cuir Samarra", "Chaussure Richelieu Luxe Officer", "Chaussure de sport Taggat Yaram",
                 "veste", "pantalon jean", "basket", "sac"],
    "couleurs": ["blanc", "bleu", "rouge", "noir", "blanc pure", "marron"],
    "zones": ["Médina", "Parcelles Assainies", "Mermoz", "Unité 15", "Pikine",
              "Yoff", "Ouakam", "Grand Dakar", "Colobane", "Rufisque"],
}


# ---------------------------------------------------------------------------
# Normalisation des valeurs (même fonctions pour les valeurs attendues et extraites)
# ---------------------------------------------------------------------------

def sans_accents(s):
    return "".join(c for c in unicodedata.normalize("NFD", str(s)) if unicodedata.category(c) != "Mn")


def separer_valeurs(valeur):
    """Liste de valeurs à partir d'une chaîne 'a / b', d'une liste, d'un nombre ou de None."""
    if valeur is None:
        return []
    if isinstance(valeur, (list, tuple, set)):
        sortie = []
        for v in valeur:
            sortie.extend(separer_valeurs(v))
        return sortie
    if isinstance(valeur, bool):
        return []
    if isinstance(valeur, (int, float)):
        return [str(int(valeur)) if float(valeur).is_integer() else str(valeur)]
    texte = re.sub(r"\(.*?\)", "", str(valeur))        # « bleu (à changer) » -> « bleu »
    return [p.strip() for p in re.split(r"[/;,]", texte) if p.strip()]


UNITES = {"zero": 0, "un": 1, "une": 1, "deux": 2, "trois": 3, "quatre": 4, "cinq": 5, "six": 6, "sept": 7,
          "huit": 8, "neuf": 9, "dix": 10, "onze": 11, "douze": 12, "treize": 13, "quatorze": 14,
          "quinze": 15, "seize": 16}
DIZAINES = {"vingt": 20, "trente": 30, "quarante": 40, "cinquante": 50, "soixante": 60}


def nombre_depuis_mots(texte):
    """'dix-sept mille' -> 17000 ; None si un mot n'est pas un nombre français."""
    t = sans_accents(str(texte).lower())
    t = re.sub(r"quatre[\s-]+vingts?", "quatrevingt", t)
    jetons = [j for j in re.split(r"[\s\-]+", t) if j and j != "et"]
    if not jetons:
        return None
    total = courant = 0
    for j in jetons:
        if j in UNITES:
            courant += UNITES[j]
        elif j in DIZAINES:
            courant += DIZAINES[j]
        elif j == "quatrevingt":
            courant += 80
        elif j in ("cent", "cents"):
            courant = (courant or 1) * 100
        elif j == "mille":
            total += (courant or 1) * 1000
            courant = 0
        else:
            return None
    return total + courant


def normaliser_prix(valeur):
    """Entier en FCFA, ou None. Accepte 17000, '17.000 FCFA', '17 000', '17 mille', 'dix-sept mille'."""
    if valeur is None or isinstance(valeur, bool):
        return None
    if isinstance(valeur, (int, float)):
        return int(valeur)
    t = str(valeur).lower()
    m = re.search(r"\d{1,3}(?:[ .,\u00a0\u202f]\d{3})+|\d+", t)
    if m:
        n = int(re.sub(r"[ .,\u00a0\u202f]", "", m.group(0)))
        return n * 1000 if ("mille" in t and n < 1000) else n
    return nombre_depuis_mots(re.sub(r"fcfa|francs?|cfa|\bf\b", "", t))


TAILLES_LETTRES = {"xs", "s", "m", "l", "xl", "xxl", "xxxl"}


def normaliser_tailles(valeur):
    sortie = set()
    for v in separer_valeurs(valeur):
        t = re.sub(r"\b(pointure|taille|du|de|en)\b", "", sans_accents(v.lower())).strip()
        if re.fullmatch(r"\d{2}", t) or t in TAILLES_LETTRES:
            sortie.add(t)
            continue
        n = nombre_depuis_mots(t)
        if n is not None and 30 <= n <= 50:
            sortie.add(str(n))
            continue
        m = re.search(r"\b(\d{2})\b", t)
        if m:
            sortie.add(m.group(1))
        elif t in TAILLES_LETTRES:
            sortie.add(t)
    return sortie


FEMININS = {"noire": "noir", "noires": "noir", "blanche": "blanc", "blanches": "blanc", "bleue": "bleu",
            "rouges": "rouge", "marrons": "marron", "bleus": "bleu", "noirs": "noir"}


def normaliser_couleurs(valeur):
    sortie = set()
    for v in separer_valeurs(valeur):
        t = re.sub(r"\s+", " ", sans_accents(v.lower())).strip()
        sortie.add(FEMININS.get(t, t))
    return sortie


MOTS_CLES_PRODUIT = [("ndakarou", "ndakarou"), ("mocassin", "mocassin"), ("moccasin", "mocassin"),
                     ("richelieu", "richelieu"), ("taggat", "taggat"), ("sandale", "sandale"),
                     ("samarra", "sandale"), ("air force", "air force"), ("samba", "samba"),
                     ("veste", "veste"), ("pantalon", "pantalon"), ("jean", "pantalon"),
                     ("basket", "basket"), ("sac", "sac"), ("chaussure", "chaussure")]
CHAUSSURES = {"ndakarou", "mocassin", "richelieu", "taggat", "sandale", "basket", "samba", "air force"}


def cle_produit(texte):
    """Clé canonique : le mot le plus SPÉCIFIQUE gagne (« Chaussure de ville Ndakarou » -> ndakarou)."""
    t = sans_accents(str(texte).lower())
    for motif, cle in MOTS_CLES_PRODUIT:
        if re.search(rf"\b{re.escape(motif)}s?\b", t):
            return cle
    return re.sub(r"\s+", " ", t).strip()


def normaliser_produits(valeur):
    return {cle_produit(v) for v in separer_valeurs(valeur)}


def cle_zone(texte):
    t = re.sub(r"\s+", " ", sans_accents(str(texte).lower())).strip()
    return re.sub(r"^(a|au|aux|vers|chez)\s+", "", t)


def normaliser_zones(valeur):
    return {cle_zone(v) for v in separer_valeurs(valeur)}


def normaliser_prix_ensemble(valeur):
    sortie = set()
    for v in separer_valeurs(valeur) if not isinstance(valeur, (int, float)) else [valeur]:
        n = normaliser_prix(v)
        if n is not None:
            sortie.add(n)
    return sortie


def normaliser(champ, valeur):
    return {"produit": normaliser_produits, "taille": normaliser_tailles, "couleur": normaliser_couleurs,
            "prix": normaliser_prix_ensemble, "zone": normaliser_zones}[champ](valeur)


def egaux(champ, a, b):
    if champ == "zone":
        return a == b or (len(a) >= 5 and a in b) or (len(b) >= 5 and b in a)
    return a == b


def comparer(champ, attendu, extrait):
    """-> 'juste' | 'partiel' | 'absent' | 'faux'"""
    if not extrait:
        return "absent"
    communs = {x for x in attendu if any(egaux(champ, x, e) for e in extrait)}
    en_trop = {e for e in extrait if not any(egaux(champ, x, e) for x in attendu)}
    if attendu and len(communs) == len(attendu) and not en_trop:
        return "juste"
    if communs:
        return "partiel"
    if champ == "produit" and any(("chaussure" in (x, e)) and ({x, e} - {"chaussure"}) <= CHAUSSURES
                                   for x in attendu for e in extrait):
        return "partiel"                       # bonne catégorie, précision différente
    return "faux"


# ---------------------------------------------------------------------------
# Consigne et lecture de la réponse
# ---------------------------------------------------------------------------

SYSTEME = """Tu es le module d'extraction d'un assistant de boutique WhatsApp à Dakar. Tu reçois la TRANSCRIPTION AUTOMATIQUE d'un message vocal d'un client, en wolof, en français ou un mélange des deux. Cette transcription est imparfaite : mots déformés, écrits phonétiquement, parfois coupés ou inventés.

Ta tâche : repérer ce que le client dit sur cinq éléments : produit, couleur, taille (pointure ou lettre), prix, zone de livraison.

Règles :
- Appuie-toi sur le catalogue pour retrouver un mot déformé (un mot qui ressemble phonétiquement à un article, une couleur ou une zone du catalogue).
- Ne devine pas. Si un élément n'apparaît pas de façon reconnaissable dans la transcription, mets null. Une mauvaise valeur est pire qu'une valeur absente.
- produit : le nom du catalogue si le client vise un article précis, sinon le nom courant de l'objet (par exemple « casquette »).
- couleur : une ou plusieurs couleurs, en français.
- taille : pointure en chiffres (par exemple 46) ou taille de vêtement en lettres (de S à XXL). S'il y en a plusieurs, donne-les toutes.
- prix : nombre entier en FCFA (par exemple 9500).
- zone : la zone de livraison, telle qu'écrite dans la liste si elle y figure.
- Pour chaque élément donne ta confiance : "haute", "moyenne" ou "faible".

Réponds UNIQUEMENT par un objet JSON de cette forme, sans aucun autre texte :
{"produit": {"valeur": ..., "confiance": ...}, "couleur": {"valeur": ..., "confiance": ...}, "taille": {"valeur": ..., "confiance": ...}, "prix": {"valeur": ..., "confiance": ...}, "zone": {"valeur": ..., "confiance": ...}}
Quand un élément est absent : {"valeur": null, "confiance": null}."""


def bloc_catalogue(cat):
    return ("CATALOGUE\nArticles : " + " ; ".join(cat["produits"]) +
            "\nCouleurs : " + " ; ".join(cat["couleurs"]) +
            "\nZones de livraison courantes : " + " ; ".join(cat["zones"]))


def uniformiser(texte):
    t = unicodedata.normalize("NFKC", str(texte or "")).lower()
    t = re.sub(r"[^\w\s]", " ", t, flags=re.UNICODE)
    return re.sub(r"\s+", " ", t).strip()


def construire_consigne(cat, textes):
    """textes : liste d'une à trois transcriptions. Renvoie le message utilisateur."""
    if len(textes) == 1:
        corps = f"TRANSCRIPTION AUTOMATIQUE DU MESSAGE :\n<<<\n{textes[0]}\n>>>"
    elif len(textes) == 2:
        corps = ("DEUX TRANSCRIPTIONS AUTOMATIQUES DU MÊME MESSAGE (deux systèmes différents ; "
                 "aucune n'est présumée plus fiable : croise-les) :\n"
                 f"Transcription A :\n<<<\n{textes[0]}\n>>>\nTranscription B :\n<<<\n{textes[1]}\n>>>")
    else:
        corps = ("TROIS TRANSCRIPTIONS AUTOMATIQUES DU MÊME MESSAGE (trois systèmes différents ; "
                 "aucune n'est présumée plus fiable : croise-les) :\n"
                 + "\n".join(f"Transcription {lettre} :\n<<<\n{t}\n>>>" for lettre, t in zip("ABC", textes)))
    return bloc_catalogue(cat) + "\n\n" + corps + "\n\nRéponds uniquement par l'objet JSON demandé, sans explication."


def extraire_objet_json(texte):
    """Premier objet JSON valide du texte qui contient au moins un des champs attendus.
    Tolère du texte avant ou après, des balises ```json et des accolades dans une explication."""
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", texte.strip(), flags=re.IGNORECASE)
    decodeur = json.JSONDecoder()
    for m in re.finditer(r"\{", t):
        try:
            obj, _ = decodeur.raw_decode(t[m.start():])
        except ValueError:
            continue
        if isinstance(obj, dict) and any(c in obj for c in CODES):
            return obj
    return None


def parser_reponse(texte):
    """-> {champ: {'valeur': ..., 'confiance': ...}} ou None si illisible."""
    if not texte:
        return None
    obj = extraire_objet_json(texte)
    if obj is None:
        return None
    sortie = {}
    for c in CODES:
        v = obj.get(c)
        if isinstance(v, dict):
            conf = v.get("confiance")
            sortie[c] = {"valeur": v.get("valeur"),
                         "confiance": conf.strip().lower() if isinstance(conf, str) else None}
        else:
            sortie[c] = {"valeur": v, "confiance": None}
    return sortie


# ---------------------------------------------------------------------------
# Appel de l'API (bibliothèque standard uniquement), avec cache
# ---------------------------------------------------------------------------

def appeler_api(system, user, modele, cle, url, max_tokens=4000, temperature=0.0, essais=6,
                delai_initial=2.0, journal=print, etat=None):
    """etat : dictionnaire partagé entre les appels. Quand le modèle refuse 'temperature', on le
    note dedans pour ne plus jamais la renvoyer (sinon chaque appel serait doublé)."""
    if etat is not None and "temperature" in etat:
        temperature = etat["temperature"]
    corps = {"model": modele, "max_tokens": max_tokens, "system": system,
             "messages": [{"role": "user", "content": user}]}
    if temperature is not None:
        corps["temperature"] = temperature
    tentative, delai = 0, delai_initial
    while True:
        tentative += 1
        req = urllib.request.Request(
            url, data=json.dumps(corps).encode("utf-8"), method="POST",
            headers={"x-api-key": cle, "anthropic-version": "2023-06-01", "content-type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                donnees = json.loads(r.read().decode("utf-8"))
            arret = donnees.get("stop_reason")
            if arret not in (None, "end_turn", "stop_sequence"):
                types = sorted({str(b.get("type")) for b in donnees.get("content", [])})
                journal(f"  (attention : la réponse s'est arrêtée sur {arret!r} ; blocs reçus : {types} ; max_tokens = {max_tokens})")
            return "".join(b.get("text", "") for b in donnees.get("content", []) if b.get("type") == "text")
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", errors="replace")
            if e.code == 400 and "temperature" in detail.lower() and "temperature" in corps:
                journal("  (le modèle refuse 'temperature' : retirée, on réessaie)")
                del corps["temperature"]
                if etat is not None:
                    etat["temperature"] = None
                continue
            if e.code in (401, 403):
                raise SystemExit(f"Clé API refusée (HTTP {e.code}) : {detail[:200]}")
            if e.code == 404:
                raise SystemExit(f"Modèle ou adresse introuvable (HTTP 404) : {detail[:200]}")
            if e.code in (408, 409, 429, 500, 502, 503, 504, 529) and tentative < essais:
                try:
                    attente = float(e.headers.get("retry-after") or delai)
                except ValueError:
                    attente = delai
                time.sleep(min(attente, 60))
                delai = min(delai * 2, 60)
                continue
            raise SystemExit(f"Erreur API HTTP {e.code} : {detail[:300]}")
        except (urllib.error.URLError, TimeoutError) as e:
            if tentative < essais:
                time.sleep(delai)
                delai = min(delai * 2, 60)
                continue
            raise SystemExit(f"Réseau indisponible : {e}")


class Cache:
    def __init__(self, chemin):
        self.chemin = chemin
        self.d = {}
        if os.path.isfile(chemin):
            with open(chemin, encoding="utf-8") as f:
                self.d = json.load(f)

    @staticmethod
    def cle(modele, temperature, system, user, rep=0):
        elements = [modele, temperature, system, user] + ([rep] if rep else [])
        return hashlib.sha256(json.dumps(elements, ensure_ascii=False).encode("utf-8")).hexdigest()

    def get(self, k):
        return self.d.get(k)

    def put(self, k, texte):
        self.d[k] = texte
        with open(self.chemin, "w", encoding="utf-8") as f:
            json.dump(self.d, f, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Données
# ---------------------------------------------------------------------------

def nom_fichier(chemin):
    return re.split(r"[\\/]", str(chemin))[-1]


def texte_cellule(valeur):
    if valeur is None or (isinstance(valeur, float) and valeur != valeur):
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


def charger_donnees(xlsx, feuille, chemin_base, colonne_whosper, moteurs=None):
    """moteurs : {nom: chemin d'un CSV au format details (colonnes chemin_audio, hypothese)}."""
    import pandas as pd
    base = {nom_fichier(r["chemin_audio"]): r["hypothese"] for r in lire_csv(chemin_base)}
    extra = {nom: {nom_fichier(r["chemin_audio"]): r["hypothese"] for r in lire_csv(chemin)}
             for nom, chemin in (moteurs or {}).items()}
    sans_extra = 0
    df = pd.read_excel(xlsx, sheet_name=feuille)
    if colonne_whosper not in df.columns:
        raise SystemExit(f"Colonne {colonne_whosper!r} introuvable. Colonnes : " + ", ".join(repr(c) for c in df.columns))
    donnees = []
    for _, l in df.iterrows():
        if "Exemple fictif" in texte_cellule(l.get("Notes")):
            continue
        nom = texte_cellule(l.get("Fichier audio"))
        whosper = texte_cellule(l.get(colonne_whosper))
        if nom not in base or not whosper:
            continue
        attendus = {}
        for colonne, code in CHAMPS:
            cellule = texte_cellule(l.get(colonne))
            if cellule:
                attendus[code] = {"brut": cellule, "valeurs": normaliser(code, cellule)}
        if attendus and any(nom not in h for h in extra.values()):
            sans_extra += 1          # on ne signale que les vocaux qui auraient été testés
            continue
        if attendus:
            donnees.append({"fichier": nom, "reference": texte_cellule(l.get("Transcription corrigée")),
                            "xlsr": base[nom], "whosper": whosper, "attendus": attendus,
                            "moteurs": {n: h[nom] for n, h in extra.items()}})
    if sans_extra:
        print(f"⚠️  {sans_extra} vocal(aux) écarté(s) : absent(s) d'un des fichiers donnés avec --moteur "
              f"(les conditions doivent porter sur les mêmes vocaux).")
    donnees.sort(key=lambda d: d["fichier"])
    return donnees


def texte_moteur(nom, d):
    return d[nom] if nom in MOTEURS_DE_BASE else d["moteurs"][nom]


def textes_pour(condition, d, graine, uniformise):
    f = uniformiser if uniformise else (lambda x: x)
    if condition == "reference":
        return [d["reference"]], ""
    if condition == "vide":
        return ["(aucun texte reconnu)"], ""
    if condition == "xlsr":
        return [f(d["xlsr"])], ""
    if condition == "whosper":
        return [f(d["whosper"])], ""
    if condition == "deux":
        a_en_premier = random.Random(f"{graine}-{d['fichier']}").choice(["xlsr", "whosper"])
        ordre = [a_en_premier, "whosper" if a_en_premier == "xlsr" else "xlsr"]
        return [f(d[ordre[0]]), f(d[ordre[1]])], f"A={ordre[0]},B={ordre[1]}"
    if "+" in condition:                      # combinaison : ordre tiré au sort, par vocal, de façon reproductible
        ordre = condition.split("+")
        random.Random(f"{graine}-{d['fichier']}-{condition}").shuffle(ordre)
        return [f(texte_moteur(m, d)) for m in ordre], ",".join(f"{lettre}={m}" for lettre, m in zip("ABC", ordre))
    return [f(texte_moteur(condition, d))], ""      # moteur supplémentaire, seul


# ---------------------------------------------------------------------------
# Exécution et rapport
# ---------------------------------------------------------------------------

def p_signe(a, b):
    n = a + b
    if n == 0:
        return 1.0
    k = max(a, b)
    return min(1.0, 2 * sum(comb(n, i) for i in range(k, n + 1)) / 2 ** n)


def executer(args, client):
    cat = dict(CATALOGUE_DEFAUT)
    if args.catalogue:
        with open(args.catalogue, encoding="utf-8") as f:
            cat.update(json.load(f))
    moteurs_extra = {}
    for spec in (getattr(args, "moteur", None) or []):
        if "=" not in spec:
            raise SystemExit(f"--moteur attend NOM=chemin.csv (reçu : {spec!r})")
        nom_m, chemin_m = spec.split("=", 1)
        nom_m = nom_m.strip()
        if not nom_m or nom_m in NOMS_RESERVES or any(ch in nom_m for ch in "+,= "):
            raise SystemExit(f"Nom de moteur invalide ou réservé : {nom_m!r} (évite {', '.join(sorted(NOMS_RESERVES))}, '+', ',' et les espaces)")
        if nom_m in moteurs_extra:
            raise SystemExit(f"Moteur {nom_m!r} donné deux fois.")
        moteurs_extra[nom_m] = chemin_m.strip()
    donnees = charger_donnees(args.xlsx, args.feuille, args.base, args.whosper_colonne, moteurs_extra)
    if args.limite:
        donnees = donnees[:args.limite]
    if not donnees:
        raise SystemExit("Aucun vocal avec des champs attendus : vérifie --xlsx, --base et --whosper-colonne.")
    conditions = [c.strip() for c in args.conditions.split(",") if c.strip()]
    for c in conditions:
        if not condition_valide(c, moteurs_extra):
            raise SystemExit(f"Condition inconnue {c!r} (attendu : {', '.join(CONDITIONS)}, un moteur donné avec --moteur, "
                             f"ou une combinaison 'a+b' / 'a+b+c' de xlsr, whosper et de ces moteurs)")

    if args.dry_run:
        d = donnees[0]
        print(f"=== SYSTÈME ===\n{SYSTEME}\n")
        for c in conditions:
            textes, ordre = textes_pour(c, d, args.graine, args.uniformiser)
            print(f"=== CONSIGNE, condition {c!r}, fichier {d['fichier']} {ordre} ===\n{construire_consigne(cat, textes)}\n")
        reps = max(1, getattr(args, "repetitions", 1))
        print(f"(--dry-run : rien n'a été envoyé. {len(donnees)} vocaux x {len(conditions)} conditions x {reps} répétition(s) "
              f"= {len(donnees) * len(conditions) * reps} appels au maximum ; la condition « vide » n'en demande qu'un par répétition.)")
        return None

    os.makedirs(args.out_dir, exist_ok=True)
    cache = Cache(os.path.join(args.out_dir, "cache_reponses.json"))
    appels = depuis_cache = illisibles = 0
    detail_illisibles = []
    resultats = []
    reps = max(1, getattr(args, "repetitions", 1))
    for numero_rep in range(reps):
        for cond in conditions:
            for d in donnees:
                textes, ordre = textes_pour(cond, d, args.graine, args.uniformiser)
                user = construire_consigne(cat, textes)
                k = Cache.cle(args.modele, args.temperature, SYSTEME, user, numero_rep)
                brut = cache.get(k)
                if brut is None:
                    brut = client(SYSTEME, user)
                    cache.put(k, brut)
                    appels += 1
                    if args.pause:
                        time.sleep(args.pause)
                else:
                    depuis_cache += 1
                analyse = parser_reponse(brut)
                if analyse is None:
                    illisibles += 1
                    detail_illisibles.append((numero_rep + 1, cond, d["fichier"], brut))
                for code, att in d["attendus"].items():
                    rep = analyse[code] if analyse else {"valeur": None, "confiance": None}
                    extrait = normaliser(code, rep["valeur"])
                    resultats.append({"condition": cond, "fichier": d["fichier"], "champ": code,
                                      "attendu": att["brut"], "extrait": json.dumps(rep["valeur"], ensure_ascii=False),
                                      "resultat": comparer(code, att["valeurs"], extrait),
                                      "confiance": rep["confiance"] or "", "ordre": ordre,
                                      "format_illisible": "oui" if analyse is None else "", "rep": numero_rep + 1})
    if detail_illisibles:
        with open(os.path.join(args.out_dir, "reponses_illisibles.txt"), "w", encoding="utf-8") as f:
            for numero_rep, cond, fichier, brut in detail_illisibles:
                f.write(f"--- répétition {numero_rep}, condition {cond}, fichier {fichier} ---\n{brut}\n\n")
    chemin = os.path.join(args.out_dir, "resultats_extraction.csv")
    colonnes = ["condition", "fichier", "champ", "attendu", "extrait", "resultat", "confiance", "ordre", "format_illisible", "rep"]
    with open(chemin, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=colonnes, delimiter=";", quoting=csv.QUOTE_ALL)
        w.writeheader()
        w.writerows(resultats)
    rapport(resultats, conditions, appels, depuis_cache, illisibles, chemin, detail_illisibles, reps)
    return resultats


def moyennes_par_champ(lignes):
    """{(fichier, champ): (part de 'juste', part de 'faux')} moyennée sur les répétitions."""
    acc = defaultdict(lambda: [0.0, 0.0, 0])
    for r in lignes:
        a = acc[(r["fichier"], r["champ"])]
        a[0] += r["resultat"] == "juste"
        a[1] += r["resultat"] == "faux"
        a[2] += 1
    return {k: (v[0] / v[2], v[1] / v[2]) for k, v in acc.items()}


def rapport(resultats, conditions, appels, depuis_cache, illisibles, chemin, detail_illisibles=None, reps=1):
    par_cond = defaultdict(list)
    for r in resultats:
        par_cond[r["condition"]].append(r)
    n_champs = len(par_cond[conditions[0]]) // reps
    suite_reps = f" × {reps} répétitions (les effectifs ci-dessous les cumulent)" if reps > 1 else ""
    print(f"\n{n_champs} champs par condition{suite_reps}. Appels API : {appels} ; réponses réutilisées (cache, ou consigne identique) : {depuis_cache} ; "
          f"réponses illisibles (comptées 'absent') : {illisibles}.")
    if detail_illisibles:
        print(f"\n⚠️  {illisibles} réponse(s) illisible(s) : elles sont comptées 'absent', donc elles TIRENT LES RÉSULTATS VERS LE BAS. "
              f"Texte brut de {min(3, len(detail_illisibles))} d'entre elles (toutes dans reponses_illisibles.txt) :")
        for numero_rep, cond, fichier, brut in detail_illisibles[:3]:
            print(f"  [rép. {numero_rep}, {cond}, {fichier}] {brut[:300]!r}{' …' if len(brut) > 300 else ''}")
    for cond in conditions:
        lignes = par_cond[cond]
        print(f"\n=== Condition : {cond} ===")
        print(f"{'champ':<9}{'n':>4} | {'juste':>6}{'partiel':>9}{'absent':>8}{'faux':>6} | faux sûrs d'eux")
        for champ in CODES + ["TOUS"]:
            sous = [r for r in lignes if champ == "TOUS" or r["champ"] == champ]
            if not sous:
                continue
            c = {k: sum(1 for r in sous if r["resultat"] == k) for k in ("juste", "partiel", "absent", "faux")}
            sur = sum(1 for r in sous if r["resultat"] == "faux" and r["confiance"] == "haute")
            if champ == "TOUS":
                print("-" * 62)
            print(f"{champ:<9}{len(sous):>4} | {c['juste']:>6}{c['partiel']:>9}{c['absent']:>8}{c['faux']:>6} | {sur:>3}")
        if reps > 1:
            par_rep = [100 * sum(1 for r in lignes if r["rep"] == i and r["resultat"] == "juste") / max(1, sum(1 for r in lignes if r["rep"] == i))
                       for i in range(1, reps + 1)]
            print(f"  'juste' par répétition : " + "  ".join(f"{x:.0f} %" for x in par_rep)
                  + f"   (dispersion : {max(par_rep) - min(par_rep):.0f} points ; c'est le bruit d'échantillonnage du modèle)")

    if "reference" in par_cond:
        manques = [r for r in par_cond["reference"] if r["resultat"] != "juste"]
        print(f"\n--- CONTRÔLE (texte exact) : {len(par_cond['reference']) - len(manques)}/{len(par_cond['reference'])} justes. "
              f"Les {len(manques)} autres, à lire : si la consigne ou la notation y est pour quelque chose, "
              f"ce n'est pas le moteur qu'il faut blâmer ---")
        for r in manques:
            print(f"  {r['fichier']:<16}{r['champ']:<8} attendu={r['attendu']!r:<26} extrait={r['extrait']}  -> {r['resultat']}")

    paires = [(a, b) for a, b in [("xlsr", "whosper"), ("xlsr", "deux"), ("whosper", "deux")]
              if a in par_cond and b in par_cond]
    bases_ref = [b for b in ("xlsr", "whosper", "deux") if b in par_cond]
    nouvelles = [c for c in conditions if c in par_cond and c not in ("reference", "vide", "xlsr", "whosper", "deux")]
    paires += [(b, c) for c in nouvelles for b in bases_ref]
    if paires:
        print("\n=== Comparaisons (même champs ; écart en points de %, IC95 % par ré-échantillonnage de fichiers) ===")
    for a, b in paires:
        ma, mb = moyennes_par_champ(par_cond[a]), moyennes_par_champ(par_cond[b])
        cles = sorted(ma)
        mieux = sum(1 for k in cles if mb[k][0] > ma[k][0] + 1e-9)
        pire = sum(1 for k in cles if mb[k][0] < ma[k][0] - 1e-9)
        moy = " (en moyenne sur les répétitions)" if reps > 1 else ""
        txt = f"  {b} contre {a} : 'juste' meilleur {mieux} fois, pire {pire} fois{moy} (test de signe p = {p_signe(mieux, pire):.2f})"
        if np is not None:
            fich = defaultdict(lambda: [0.0, 0.0, 0.0, 0.0, 0.0])
            for k in cles:
                g = fich[k[0]]
                g[0] += 1
                g[1] += ma[k][0]; g[2] += mb[k][0]
                g[3] += ma[k][1]; g[4] += mb[k][1]
            tab = np.array(list(fich.values()), dtype=float)
            ech = np.random.default_rng(42).integers(0, len(tab), size=(10000, len(tab)))
            s = tab[ech].sum(axis=1)
            dj, df_ = 100 * (s[:, 2] - s[:, 1]) / s[:, 0], 100 * (s[:, 4] - s[:, 3]) / s[:, 0]
            tj, tf = tab.sum(axis=0), tab.sum(axis=0)
            obs_j = 100 * (tj[2] - tj[1]) / tj[0]
            obs_f = 100 * (tf[4] - tf[3]) / tf[0]
            txt += (f"\n      écart de 'juste' : {obs_j:+.1f} pts [{np.percentile(dj, 2.5):+.1f} ; {np.percentile(dj, 97.5):+.1f}]"
                    f"   écart de 'faux' : {obs_f:+.1f} pts [{np.percentile(df_, 2.5):+.1f} ; {np.percentile(df_, 97.5):+.1f}]")
        print(txt)
    if "vide" in par_cond:
        v = par_cond["vide"]
        print(f"\n--- CONTRÔLE INVERSE (aucun texte) : {sum(r['resultat'] == 'juste' for r in v)} juste(s), "
              f"{sum(r['resultat'] == 'faux' for r in v)} faux, {sum(r['resultat'] == 'absent' for r in v)} absent(s) sur {len(v)} champs. "
              f"Tout « juste » ou « faux » ici est inventé à partir du catalogue seul. ---")
    print(f"\nDétail ligne par ligne : {chemin}")
    print("Lecture : une seule voix, 52 vocaux. Un écart dont l'intervalle contient zéro n'est pas démontré.")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--xlsx", required=True)
    p.add_argument("--feuille", default="Grille collecte")
    p.add_argument("--base", required=True, help="details_base.csv (transcriptions XLS-R)")
    p.add_argument("--whosper-colonne", default="Transcription Whosper", help="colonne de la grille avec les transcriptions Whosper")
    p.add_argument("--out-dir", required=True)
    p.add_argument("--conditions", default=",".join(CONDITIONS),
                   help="sous-ensemble de : " + ", ".join(CONDITIONS) + " ; plus, avec --moteur, le nom d'un moteur (seul) ou une "
                        "combinaison 'a+b' / 'a+b+c' (ex. nouveau,nouveau+whosper,nouveau+xlsr+whosper)")
    p.add_argument("--moteur", action="append", default=[], metavar="NOM=CHEMIN",
                   help="ajoute un moteur : CSV au format details (colonnes chemin_audio, hypothese). Répétable.")
    p.add_argument("--modele", default=os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5-5"))
    p.add_argument("--api-url", default="https://api.anthropic.com/v1/messages")
    p.add_argument("--temperature", type=float, default=0.0, help="0 par défaut ; si le modèle la refuse (cas de claude-sonnet-5-5), elle est retirée et les réponses sont échantillonnées : utilise alors --repetitions")
    p.add_argument("--repetitions", type=int, default=1, help="nombre de fois où chaque consigne est posée (3 conseillé si la temperature est refusée)")
    p.add_argument("--max-tokens", type=int, default=4000, help="longueur maximale de la réponse (réflexion éventuelle du modèle comprise)")
    p.add_argument("--catalogue", default=None, help="JSON {produits, couleurs, zones} pour remplacer le catalogue par défaut")
    p.add_argument("--uniformiser", action="store_true", help="minuscules sans ponctuation pour les deux moteurs (écriture identique)")
    p.add_argument("--limite", type=int, default=0, help="ne traiter que les N premiers vocaux (essai à bas coût)")
    p.add_argument("--pause", type=float, default=0.0, help="secondes entre deux appels")
    p.add_argument("--graine", type=int, default=7)
    p.add_argument("--dry-run", action="store_true", help="affiche les consignes sans rien envoyer")
    args = p.parse_args()

    client = None
    if not args.dry_run:
        cle = os.environ.get("ANTHROPIC_API_KEY")
        if not cle:
            raise SystemExit("Variable ANTHROPIC_API_KEY absente. Dans l'invite de commandes : set ANTHROPIC_API_KEY=sk-ant-...")
        etat = {"temperature": args.temperature}

        def client(system, user):
            return appeler_api(system, user, args.modele, cle, args.api_url, max_tokens=args.max_tokens,
                               temperature=args.temperature, etat=etat)
    executer(args, client)


if __name__ == "__main__":
    main()
