#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Kit « vocaux français » : fait lire tes enregistrements par les TROIS transcripteurs, puis prépare la mesure.

Pourquoi : ton corpus actuel ne contient presque pas de français pur (2 champs notés sur 37). Or c'est ta vraie plainte : les clients qui
parlent français. Ce kit fabrique la mesure qui manque : 22 phrases françaises réalistes, lues par TOI, transcrites par
  - XLS-R (ton ancien service Render),
  - ton modèle ajusté (ton nouveau service Render),
  - Whisper-FR (OpenAI), appelé comme le fait le webhook.

CE QU'IL FAUT AVANT : lire LISEZMOI.txt (enregistrer les 22 phrases de phrases.csv sous les noms fr01 à fr22).
Ce script doit être dans le même dossier que transcrire_whisper_fr.py (il en réutilise les fonctions).

CE QU'IL PRODUIT (dans le dossier --sortie)
  details_base_fr.csv      transcriptions XLS-R          (même format que details_base.csv)
  details_nouveau_fr.csv   transcriptions du modèle ajusté
  details_whisper_fr.csv   transcriptions Whisper-FR
  grille_francais.xlsx     la grille avec les valeurs attendues, au format que test_extraction_catalogue.py sait lire
  + à l'écran : l'erreur moyenne de chaque moteur, ce que chacun a écrit pour chaque phrase, et la commande suivante.

SÉCURITÉ : les secrets des services et la clé OpenAI se donnent par variables d'environnement (set ...), jamais dans une conversation ;
le script n'en affiche jamais la valeur. REPRISE : un fichier déjà transcrit n'est pas refait ; relance la même commande après une panne.
"""

import argparse
import csv
import io
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

try:
    import transcrire_whisper_fr as tw
except ImportError:
    raise SystemExit("ERREUR : transcrire_kit.py doit être dans le même dossier que transcrire_whisper_fr.py (il en réutilise les fonctions).")

ErreurScript = tw.ErreurScript
EXTENSIONS = (".ogg", ".opus", ".oga", ".m4a", ".mp3", ".wav", ".aac", ".mp4")
TYPES = {".ogg": "audio/ogg", ".opus": "audio/ogg", ".oga": "audio/ogg", ".m4a": "audio/mp4", ".mp4": "audio/mp4", ".mp3": "audio/mpeg", ".wav": "audio/wav", ".aac": "audio/aac"}
CHAMPS_KIT = ["produit", "taille", "couleur", "prix", "zone"]
CATEGORIE = "francais_pur"

MOTEURS = {
    "xlsr": {"nom": "XLS-R", "fichier": "details_base_fr.csv", "source": "xlsr", "condition": "xlsr", "url_env": "XLSR_SERVICE_URL", "secret_env": "XLSR_SERVICE_SECRET"},
    "noxa": {"nom": "Modèle ajusté", "fichier": "details_nouveau_fr.csv", "source": "noxa", "condition": "nouveau", "url_env": "NOXA_SERVICE_URL", "secret_env": "NOXA_SERVICE_SECRET"},
    "whisper": {"nom": "Whisper-FR", "fichier": "details_whisper_fr.csv", "source": "whisper-fr", "condition": "whisperfr"},
}


class ErreurMoteur(Exception):
    """Un moteur est inutilisable (secret refusé, service injoignable) : on l'abandonne, les autres continuent."""


# ---------------------------------------------------------------------------
# Le kit : phrases et valeurs attendues
# ---------------------------------------------------------------------------
def lire_kit(chemin):
    lignes = tw.lire_csv(chemin)
    if not lignes:
        raise ErreurScript("Le fichier %s est vide." % chemin)
    manquantes = [c for c in ["fichier", "phrase"] + CHAMPS_KIT if c not in lignes[0]]
    if manquantes:
        raise ErreurScript("Colonnes manquantes dans %s : %s" % (chemin, ", ".join(manquantes)))
    noms = [l["fichier"].strip() for l in lignes]
    if any(not n for n in noms):
        raise ErreurScript("Une ligne de %s n'a pas de nom de fichier." % chemin)
    doublons = sorted({n for n in noms if noms.count(n) > 1})
    if doublons:
        raise ErreurScript("Nom de fichier en double dans %s : %s" % (chemin, ", ".join(doublons)))
    return [{"fichier": l["fichier"].strip(), "phrase": l["phrase"].strip(), **{c: (l.get(c) or "").strip() for c in CHAMPS_KIT}} for l in lignes]


def trouver_audio(dossier, nom):
    """Fichier audio dont le nom (sans extension) est « nom », sans tenir compte de la casse ; .ogg d'abord."""
    if not os.path.isdir(dossier):
        raise ErreurScript("Dossier audio introuvable : %s" % dossier)
    candidats = []
    for f in os.listdir(dossier):
        racine, ext = os.path.splitext(f)
        if racine.lower() == nom.lower() and ext.lower() in EXTENSIONS:
            candidats.append((EXTENSIONS.index(ext.lower()), f))
    return os.path.join(dossier, sorted(candidats)[0][1]) if candidats else None


# ---------------------------------------------------------------------------
# Les services Render (même appel que le webhook : POST /transcribe, fichier en multipart, secret en Bearer)
# ---------------------------------------------------------------------------
def nettoyer_secret(brut):
    s = (brut or "").strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in "\"'":
        s = s[1:-1].strip()
    return s


def attendre_service(url, delai_max=300.0, pas=5.0, journal=print, nom="service"):
    """Réveille un service Render endormi : GET /health jusqu'à une réponse 200. Renvoie True si le service répond."""
    debut = time.time(); essai = 0
    while True:
        essai += 1
        try:
            with urllib.request.urlopen(urllib.request.Request(url.rstrip("/") + "/health", method="GET"), timeout=60) as r:
                if 200 <= r.status < 300:
                    return True
        except urllib.error.HTTPError:
            pass
        except (urllib.error.URLError, TimeoutError, OSError):
            pass
        if time.time() - debut >= delai_max:
            return False
        if essai == 1:
            journal("   %s : le service ne répond pas encore (il dort peut-être) ; j'attends jusqu'à %d s…" % (nom, delai_max))
        time.sleep(pas)


def transcrire_service(url, secret, contenu, nom_envoi, type_envoi, timeout=180, essais=5, delai=3.0):
    """Texte renvoyé par un service. Lève ErreurMoteur si le secret est refusé ; renvoie (None, message) si ce fichier échoue."""
    corps, type_contenu = tw.fabriquer_multipart({}, nom_envoi, contenu, type_envoi)
    tentative = 0
    while True:
        tentative += 1
        entetes = {"Content-Type": type_contenu}
        if secret:
            entetes["Authorization"] = "Bearer %s" % secret
        req = urllib.request.Request(url.rstrip("/") + "/transcribe", data=corps, method="POST", headers=entetes)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return (json.loads(r.read().decode("utf-8")).get("text") or ""), ""
        except urllib.error.HTTPError as e:
            detail = " ".join(e.read().decode("utf-8", errors="replace").split())[:200]
            if e.code in (401, 403):
                raise ErreurMoteur("secret refusé (HTTP %s) : vérifie la variable d'environnement du secret de ce service" % e.code)
            if e.code == 413:
                return None, "audio trop long pour ce service (%s)" % detail
            if e.code in (408, 409, 429, 500, 502, 503, 504) and tentative < essais:
                time.sleep(delai); delai = min(delai * 2, 60); continue
            return None, "HTTP %s : %s" % (e.code, detail)
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            if tentative < essais:
                time.sleep(delai); delai = min(delai * 2, 60); continue
            return None, "service injoignable : %s" % e


# ---------------------------------------------------------------------------
# Un moteur sur tous les enregistrements (avec reprise)
# ---------------------------------------------------------------------------
def ligne_resultat(code, item, chemin, texte, ms, erreur):
    cfg = MOTEURS[code]
    ok = texte is not None and not erreur
    return {"source": cfg["source"], "categorie": CATEGORIE, "chemin_audio": chemin, "reference": item["phrase"], "hypothese": texte or "",
            "wer": ("%.4f" % tw.wer(item["phrase"], texte)) if ok and tw.wer(item["phrase"], texte) is not None else "",
            "cer": ("%.4f" % tw.cer(item["phrase"], texte)) if ok and tw.cer(item["phrase"], texte) is not None else "",
            "latence_ms": ms, "erreur": erreur}


def traiter_moteur(code, a_traiter, deja, appel, chemin_sortie, ordre, journal=print):
    """a_traiter : [(item, chemin)]. deja : {nom: ligne}. appel(contenu, nom_envoi, type_envoi) -> (texte, erreur). Écrit le CSV après CHAQUE fichier."""
    lignes = dict(deja)

    def dans_l_ordre():
        return [lignes[n] for n in ordre if n in lignes]
    for i, (item, chemin) in enumerate(a_traiter, 1):
        ext = os.path.splitext(chemin)[1].lower()
        with open(chemin, "rb") as f:
            contenu = f.read()
        debut = time.time()
        texte, erreur = appel(contenu, "audio" + (ext if ext != ".opus" else ".ogg"), TYPES.get(ext, "application/octet-stream"))
        ms = int(1000 * (time.time() - debut))
        lignes[item["fichier"]] = ligne_resultat(code, item, chemin, texte, ms, erreur)
        tw.ecrire_sortie(chemin_sortie, dans_l_ordre())
        if i % 5 == 0:
            journal("   … %d / %d" % (i, len(a_traiter)))
    return lignes


def ecrire_grille(chemin, audio):
    """Grille Excel au format de « Grille collecte » (les colonnes que test_extraction_catalogue.py lit).
    audio : [(item, chemin du fichier)]. Le nom de fichier écrit est le NOM RÉEL (extension comprise) : c'est lui que le script d'extraction
    rapproche des fichiers de résultats ; un nom différent ferait écarter l'enregistrement en silence."""
    try:
        import pandas as pd
    except ImportError:
        raise ErreurScript("pandas est requis pour écrire la grille Excel (pip install pandas openpyxl).")
    lignes = [{"Fichier audio": os.path.basename(chemin_audio), "Transcription corrigée": it["phrase"], "Produit": it["produit"], "Taille (entendue)": it["taille"],
               "Couleur": it["couleur"], "Prix (entendu)": it["prix"], "Zone livraison": it["zone"], "Notes": "Kit français (lu par le testeur)",
               "Transcription Whosper": "(non transcrit : kit français)"} for it, chemin_audio in audio]
    try:
        pd.DataFrame(lignes).to_excel(chemin, sheet_name="Grille collecte", index=False)
    except ImportError:
        raise ErreurScript("openpyxl est requis pour écrire la grille Excel (pip install openpyxl).")


def commande_extraction(sortie, disponibles):
    j = lambda n: os.path.join(sortie, n)
    moteurs = [("--moteur nouveau=" + j("details_nouveau_fr.csv"), "noxa" in disponibles), ("--moteur whisperfr=" + j("details_whisper_fr.csv"), "whisper" in disponibles)]
    conds = {(True, True, True): "nouveau+xlsr,nouveau+xlsr+whisperfr,nouveau+xlsr+whisperfr+etiq,whisperfr",
             (True, False, True): "xlsr+whisperfr,xlsr+whisperfr+etiq,whisperfr", (True, True, False): "nouveau+xlsr"}.get(("xlsr" in disponibles, "noxa" in disponibles, "whisper" in disponibles))
    if not conds or "xlsr" not in disponibles:
        return ""
    return 'python test_extraction_catalogue.py --xlsx "%s" --base "%s" --out-dir extraction_fr --repetitions 3 %s --conditions %s' % (
        j("grille_francais.xlsx"), j("details_base_fr.csv"), " ".join(c for c, actif in moteurs if actif), conds)


def principal(argv=None, sortie=print):
    p = argparse.ArgumentParser(description="Fait lire les enregistrements du kit français par XLS-R, le modèle ajusté et Whisper-FR.")
    p.add_argument("--kit", default="phrases.csv"); p.add_argument("--dossier-audio", required=True); p.add_argument("--sortie", default="resultats_fr")
    p.add_argument("--moteurs", default="xlsr,noxa,whisper", help="parmi xlsr, noxa, whisper (séparés par des virgules)")
    p.add_argument("--xlsr-url", default=""); p.add_argument("--noxa-url", default=""); p.add_argument("--url-whisper", default=tw.URL_PAR_DEFAUT)
    p.add_argument("--limite", type=int, default=0, help="ne traiter que les N premières phrases (essai)")
    p.add_argument("--attente-reveil", type=float, default=300.0, help="secondes d'attente maximale pour réveiller un service endormi")
    p.add_argument("--oui", action="store_true", help="ne pas demander confirmation")
    a = p.parse_args(argv)
    choisis = [m.strip() for m in a.moteurs.split(",") if m.strip()]
    inconnus = [m for m in choisis if m not in MOTEURS]
    if inconnus or not choisis:
        raise ErreurScript("--moteurs : valeurs possibles xlsr, noxa, whisper (reçu : %s)" % a.moteurs)
    items = lire_kit(a.kit)
    if a.limite:
        items = items[:a.limite]
    ordre = [it["fichier"] for it in items]
    audio, manquants = [], []
    for it in items:
        c = trouver_audio(a.dossier_audio, it["fichier"])
        (audio.append((it, c)) if c else manquants.append(it["fichier"]))
    sortie("%d phrase(s) dans %s : %d enregistrement(s) trouvé(s) dans %s, %d manquant(s)." % (len(items), a.kit, len(audio), a.dossier_audio, len(manquants)))
    if manquants:
        sortie("   manquants : %s%s (nomme-les fr01.ogg, fr02.ogg…)" % (", ".join(manquants[:8]), " …" if len(manquants) > 8 else ""))
    if not audio:
        raise ErreurScript("Aucun enregistrement trouvé : mets les fichiers fr01.ogg, fr02.ogg… dans %s." % a.dossier_audio)
    os.makedirs(a.sortie, exist_ok=True)
    # --- configuration de chaque moteur, sans jamais afficher un secret
    config, indisponibles = {}, {}
    for code in choisis:
        cfg = MOTEURS[code]
        if code == "whisper":
            cle, remarques = tw.preparer_cle(os.environ.get("OPENAI_API_KEY", ""))
            if not cle:
                indisponibles[code] = "variable OPENAI_API_KEY absente (Windows : set OPENAI_API_KEY=sk-...)"; continue
            sortie("Whisper-FR : clé lue : %s" % tw.resume_cle(cle))
            for rq in remarques:
                sortie("   ATTENTION : %s" % rq)
            config[code] = {"cle": cle}
        else:
            url = (a.xlsr_url if code == "xlsr" else a.noxa_url) or os.environ.get(cfg["url_env"], "")
            if not url.strip():
                indisponibles[code] = "adresse du service absente (variable %s ou option --%s-url)" % (cfg["url_env"], code); continue
            secret = nettoyer_secret(os.environ.get(cfg["secret_env"]) or os.environ.get("ASR_SERVICE_SHARED_SECRET", ""))
            config[code] = {"url": url.strip().rstrip("/"), "secret": secret}
            sortie("%s : service %s, secret %s." % (cfg["nom"], config[code]["url"], "fourni" if secret else "ABSENT (le service refusera sans doute)"))
    for code, raison in indisponibles.items():
        sortie("   %s ignoré : %s" % (MOTEURS[code]["nom"], raison))
    if not config:
        raise ErreurScript("Aucun moteur utilisable.")
    # --- reprise
    deja, a_faire = {}, {}
    for code in config:
        chemin = os.path.join(a.sortie, MOTEURS[code]["fichier"])
        lues = {}
        if os.path.isfile(chemin):
            for l in tw.lire_csv(chemin):
                if not l.get("erreur"):
                    lues[tw.nom_fichier(l["chemin_audio"]).lower()] = l
        deja[code] = {it["fichier"]: lues[os.path.basename(c).lower()] for it, c in audio if os.path.basename(c).lower() in lues}
        a_faire[code] = [(it, c) for it, c in audio if it["fichier"] not in deja[code]]
    total = sum(len(v) for v in a_faire.values())
    sortie("À faire : %s." % ", ".join("%s %d" % (MOTEURS[c]["nom"], len(v)) for c, v in a_faire.items()))
    if total and "whisper" in a_faire and a_faire["whisper"]:
        sortie("Whisper-FR : %d appel(s) à OpenAI (quelques centimes, tarif non vérifié)." % len(a_faire["whisper"]))
    if total and not a.oui:
        if not sys.stdin.isatty():
            raise ErreurScript("Exécution non interactive : ajoute --oui pour confirmer.")
        if input("Continuer ? [o/N] ").strip().lower() not in ("o", "oui", "y", "yes"):
            raise ErreurScript("Arrêt demandé : aucun appel n'a été fait.")
    # --- un moteur après l'autre (évite de réveiller deux fois les services)
    resultats, arretes = {}, {}
    for code in config:
        cfg = MOTEURS[code]; chemin_sortie = os.path.join(a.sortie, cfg["fichier"])
        if not a_faire[code]:
            resultats[code] = deja[code]; continue
        sortie("\n%s : %d enregistrement(s)…" % (cfg["nom"], len(a_faire[code])))
        try:
            if code == "whisper":
                def appel(contenu, nom_envoi, type_envoi, c=config[code]):
                    try:
                        t = tw.transcrire(contenu, c["cle"], a.url_whisper, tw.MODELE_PAR_DEFAUT, tw.LANGUE_PAR_DEFAUT, nom_envoi=nom_envoi, type_envoi=type_envoi)
                    except ErreurScript as e:
                        raise ErreurMoteur(str(e).split("\n")[0])
                    return (t, "") if t is not None else (None, "transcription échouée après plusieurs essais")
            else:
                if not attendre_service(config[code]["url"], a.attente_reveil, journal=sortie, nom=cfg["nom"]):
                    raise ErreurMoteur("le service ne répond pas (même après %d s) : vérifie l'adresse, ou réveille-le puis relance" % a.attente_reveil)

                def appel(contenu, nom_envoi, type_envoi, c=config[code]):
                    return transcrire_service(c["url"], c["secret"], contenu, nom_envoi, type_envoi)
            resultats[code] = traiter_moteur(code, a_faire[code], deja[code], appel, chemin_sortie, ordre, journal=sortie)
        except ErreurMoteur as e:
            arretes[code] = str(e); resultats[code] = {n: l for n, l in deja[code].items()}
            sortie("   %s ABANDONNÉ : %s" % (cfg["nom"], e))
            if os.path.isfile(chemin_sortie) is False and resultats[code]:
                tw.ecrire_sortie(chemin_sortie, [resultats[code][n] for n in ordre if n in resultats[code]])
    disponibles = [c for c in config if c not in arretes and all(it["fichier"] in resultats.get(c, {}) and not resultats[c][it["fichier"]].get("erreur") for it, _ in audio)]
    # --- grille Excel pour le script d'extraction
    ecrire_grille(os.path.join(a.sortie, "grille_francais.xlsx"), audio)
    # --- comparaison
    sortie("\nERREUR MOYENNE (plus c'est bas, mieux c'est) sur %d phrase(s) en français :" % len(audio))
    sortie("%-16s %6s %6s %8s %8s" % ("moteur", "faits", "échecs", "mots", "caractères"))
    for code in config:
        L = [l for l in resultats.get(code, {}).values() if not l.get("erreur")]; ech = sum(1 for l in resultats.get(code, {}).values() if l.get("erreur"))
        w = tw.moyenne([float(l["wer"]) if l["wer"] != "" else None for l in L]); c = tw.moyenne([float(l["cer"]) if l["cer"] != "" else None for l in L])
        sortie("%-16s %6d %6d %8s %8s%s" % (MOTEURS[code]["nom"], len(L), ech, tw.pc(w), tw.pc(c), "   (ABANDONNÉ : %s)" % arretes[code] if code in arretes else ""))
    sortie("\nCE QUE CHAQUE MOTEUR A ÉCRIT :")
    for it, _ in audio:
        sortie("  %s  dit : %s" % (it["fichier"], it["phrase"]))
        for code in config:
            l = resultats.get(code, {}).get(it["fichier"])
            sortie("      %-14s: %s" % (MOTEURS[code]["nom"], (l["hypothese"] if l and not l.get("erreur") else "(échec)" if l else "(non fait)")))
    # La commande n'est proposée que si TOUS les moteurs demandés sont complets : sinon la comparaison perdrait un moteur sans le dire.
    incomplets = [c for c in choisis if c not in disponibles]
    cmd = "" if incomplets else commande_extraction(a.sortie, disponibles)
    sortie("\nFichiers écrits dans %s : %s, grille_francais.xlsx" % (a.sortie, ", ".join(MOTEURS[c]["fichier"] for c in config)))
    if cmd:
        sortie("\nPour mesurer ce que Claude en tire, lance (même invite de commandes, avec ANTHROPIC_API_KEY définie) :\n  " + cmd)
    elif incomplets:
        sortie("\nLa commande d'extraction n'est pas proposée : %s n'est pas complet. Corrige puis relance la même commande (rien de ce qui est fait n'est refait)." % ", ".join(MOTEURS[c]["nom"] for c in incomplets))
    else:
        sortie("\nPas de commande d'extraction pour ce choix de moteurs : elle demande XLS-R et au moins un autre moteur.")
    return 0


if __name__ == "__main__":
    for flux in (sys.stdout, sys.stderr):
        try:
            flux.reconfigure(encoding="utf-8")
        except Exception:
            pass
    try:
        sys.exit(principal())
    except ErreurScript as e:
        print("ERREUR : %s" % e)
        sys.exit(2)
