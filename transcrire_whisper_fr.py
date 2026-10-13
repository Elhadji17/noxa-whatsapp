#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Transcrit tes enregistrements de test avec Whisper-FR EXACTEMENT comme le fait le webhook, puis compare avec XLS-R et ton modèle.

Pourquoi : en production, Whisper-FR n'est appelé que si XLS-R et le modèle ajusté ne renvoient RIEN. Or sur du français ils
renvoient du texte faux (« moo soo »), donc Whisper n'est jamais appelé. Avant de l'ajouter en parallèle, on mesure ce qu'il
donne sur TES enregistrements, y compris le wolof, où il risque d'écrire du français plausible mais faux.

Appel identique à celui du webhook : fichier envoyé comme « audio.ogg » (audio/ogg), model=whisper-1, language=fr.

USAGE (invite de commandes Windows, dans le dossier qui contient tes fichiers details_*.csv)
  set OPENAI_API_KEY=sk-...          (ne la colle jamais dans une conversation)
  python transcrire_whisper_fr.py --base details_base.csv --nouveau details_nouveau.csv --sortie details_whisper.csv
  Option : --limite 5 pour un essai sur 5 fichiers ; --dossier-audio C:\\...\\Vocaux2 si les chemins du CSV ne sont plus bons.

CE QUE ÇA PRODUIT
  - details_whisper.csv, au même format que details_base.csv (colonnes chemin_audio, reference, hypothese, wer, cer...) : tu peux le
    donner à test_extraction_catalogue.py avec  --moteur whisperfr=details_whisper.csv
  - un tableau : erreur moyenne par catégorie (français, mélange, wolof, chiffres) pour Whisper-FR, XLS-R et ton modèle ajusté.
  - la liste des 5 enregistrements en français : ce que chaque moteur a écrit.

REPRISE : si le fichier de sortie existe, les enregistrements déjà transcrits ne sont PAS repayés. Aucune dépendance à installer.
Coût : quelques centimes pour l'ensemble (je n'ai pas de tarif vérifié) ; le script demande confirmation avant le premier appel.
"""

import argparse
import csv
import io
import os
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.request
import uuid
from collections import defaultdict

URL_PAR_DEFAUT = "https://api.openai.com/v1/audio/transcriptions"   # même adresse que le webhook
MODELE_PAR_DEFAUT = "whisper-1"                                       # même modèle que le webhook
LANGUE_PAR_DEFAUT = "fr"                                              # même langue forcée que le webhook
COLONNES = ["source", "categorie", "chemin_audio", "reference", "hypothese", "wer", "cer", "latence_ms", "erreur"]


class ErreurScript(Exception):
    pass


# ---------------------------------------------------------------------------
# Lecture de fichiers (UTF-8 d'abord, puis cp1252 : Excel français)
# ---------------------------------------------------------------------------
def lire_texte(chemin):
    with open(chemin, "rb") as f:
        brut = f.read()
    for enc in ("utf-8-sig", "cp1252"):
        try:
            return brut.decode(enc)
        except UnicodeDecodeError:
            continue
    return brut.decode("latin-1")


def lire_csv(chemin):
    if not os.path.isfile(chemin):
        raise ErreurScript("Fichier introuvable : %s" % chemin)
    texte = lire_texte(chemin)
    delimiteur = ";" if texte[:4096].count(";") > texte[:4096].count(",") else ","
    return list(csv.DictReader(io.StringIO(texte, newline=""), delimiter=delimiteur))


def nom_fichier(chemin):
    """Nom du fichier sans dossier, que le chemin soit de style Windows ou Linux (les CSV contiennent des chemins C:\\...)."""
    return re.split(r"[\\/]", str(chemin or ""))[-1]


# ---------------------------------------------------------------------------
# Mesure d'erreur (même idée que les benchmarks précédents : mots et caractères, sans accents ni ponctuation)
# ---------------------------------------------------------------------------
def normaliser(texte):
    t = "".join(c for c in unicodedata.normalize("NFD", str(texte or "")) if unicodedata.category(c) != "Mn").lower()
    t = re.sub(r"[-'’]", " ", t)
    t = re.sub(r"[^a-z0-9 ]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def distance(a, b):
    """Distance d'édition entre deux séquences (insertions, suppressions, substitutions)."""
    if not a:
        return len(b)
    if not b:
        return len(a)
    precedent = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        courant = [i]
        for j, y in enumerate(b, 1):
            courant.append(min(precedent[j] + 1, courant[j - 1] + 1, precedent[j - 1] + (x != y)))
        precedent = courant
    return precedent[-1]


def wer(reference, hypothese):
    """Taux d'erreur sur les mots ; None si la référence est vide. Peut dépasser 1 (insertions)."""
    ref = normaliser(reference).split()
    if not ref:
        return None
    return distance(ref, normaliser(hypothese).split()) / len(ref)


def cer(reference, hypothese):
    ref = normaliser(reference)
    if not ref:
        return None
    return distance(ref, normaliser(hypothese)) / len(ref)


# ---------------------------------------------------------------------------
# La clé OpenAI : l'invite de commandes Windows garde les espaces et les guillemets tapés après « set », ce qui la rend invalide
# ---------------------------------------------------------------------------
def preparer_cle(brute):
    """Renvoie (clé nettoyée, remarques). Ne modifie que les espaces et guillemets AUTOUR de la clé."""
    remarques = []
    cle = brute.strip()
    if cle != brute:
        remarques.append("espaces ou retour à la ligne autour de la clé : retirés (avec « set », rien après la clé)")
    if len(cle) >= 2 and cle[0] == cle[-1] and cle[0] in "\"'":
        cle = cle[1:-1].strip()
        remarques.append("guillemets autour de la clé : retirés (avec « set », ne mets pas de guillemets)")
    if re.search(r"\s", cle):
        remarques.append("ESPACE OU RETOUR À LA LIGNE AU MILIEU de la clé : copie cassée, recopie-la en une seule ligne")
    if "..." in cle or "…" in cle:
        remarques.append("la clé contient « ... » : c'est l'exemple de la documentation, pas une vraie clé")
    elif not cle.startswith("sk-"):
        remarques.append("la clé ne commence pas par « sk- » : ce n'est pas une clé OpenAI")
    elif len(cle) < 40:
        remarques.append("la clé est très courte (%d caractères ; une clé OpenAI en compte plus de 50) : copie incomplète ?" % len(cle))
    return cle, remarques


def resume_cle(cle):
    """Début et fin de la clé, jamais la clé entière : de quoi reconnaître laquelle est lue sans la divulguer."""
    return "%d caractères, commence par « %s », finit par « …%s »" % (len(cle), cle[:8], cle[-4:] if len(cle) >= 12 else "")


# ---------------------------------------------------------------------------
# Appel Whisper : requête multipart identique à celle du webhook
# ---------------------------------------------------------------------------
def fabriquer_multipart(champs, nom, contenu, type_contenu):
    frontiere = "----noxa" + uuid.uuid4().hex
    corps = b""
    for cle, valeur in champs.items():
        corps += ('--%s\r\nContent-Disposition: form-data; name="%s"\r\n\r\n%s\r\n' % (frontiere, cle, valeur)).encode("utf-8")
    corps += ('--%s\r\nContent-Disposition: form-data; name="file"; filename="%s"\r\nContent-Type: %s\r\n\r\n' % (frontiere, nom, type_contenu)).encode("utf-8") + contenu + b"\r\n"
    corps += ("--%s--\r\n" % frontiere).encode("utf-8")
    return corps, "multipart/form-data; boundary=%s" % frontiere


def transcrire(contenu, cle, url=URL_PAR_DEFAUT, modele=MODELE_PAR_DEFAUT, langue=LANGUE_PAR_DEFAUT, essais=6, delai=2.0):
    """Texte renvoyé par Whisper pour ces octets audio. Lève ErreurScript si la clé est refusée ; renvoie None après trop d'échecs."""
    champs = {"model": modele}
    if langue:
        champs["language"] = langue
    corps, type_contenu = fabriquer_multipart(champs, "audio.ogg", contenu, "audio/ogg")
    tentative = 0
    while True:
        tentative += 1
        req = urllib.request.Request(url, data=corps, method="POST", headers={"Authorization": "Bearer %s" % cle, "Content-Type": type_contenu})
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                import json
                return json.loads(r.read().decode("utf-8")).get("text") or ""
        except urllib.error.HTTPError as e:
            detail = " ".join(e.read().decode("utf-8", errors="replace").split())[:500]
            if e.code in (401, 403):
                raise ErreurScript("Clé OpenAI refusée (HTTP %s).\n   Réponse d'OpenAI : %s\n   À vérifier : (1) que la clé est copiée en entier, sur une seule ligne ; (2) qu'elle n'a pas été supprimée ni régénérée sur "
                                   "platform.openai.com (API keys) ; (3) qu'il n'y a ni guillemets ni espace après « set OPENAI_API_KEY= » ; (4) qu'elle appartient à un projet "
                                   "qui a accès à l'audio (Whisper)." % (e.code, detail))
            if e.code in (408, 409, 429, 500, 502, 503, 504) and tentative < essais:
                try:
                    attente = float(e.headers.get("retry-after") or delai)
                except ValueError:
                    attente = delai
                time.sleep(min(attente, 60))
                delai = min(delai * 2, 60)
                continue
            return None
        except (urllib.error.URLError, TimeoutError):
            if tentative < essais:
                time.sleep(delai)
                delai = min(delai * 2, 60)
                continue
            return None


# ---------------------------------------------------------------------------
# Programme
# ---------------------------------------------------------------------------
def trouver_audio(chemin_csv, dossier_audio):
    """Chemin réel du fichier audio : celui du CSV s'il existe, sinon le même nom dans --dossier-audio (recherche récursive)."""
    if chemin_csv and os.path.isfile(chemin_csv):
        return chemin_csv
    if dossier_audio:
        cible = nom_fichier(chemin_csv).lower()
        for racine, _, fichiers in os.walk(dossier_audio):
            for f in fichiers:
                if f.lower() == cible:
                    return os.path.join(racine, f)
    return None


def ecrire_sortie(chemin, lignes):
    with open(chemin, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLONNES, extrasaction="ignore")
        w.writeheader()
        for l in lignes:
            w.writerow(l)


def moyenne(valeurs):
    valeurs = [v for v in valeurs if v is not None]
    return sum(valeurs) / len(valeurs) if valeurs else None


def pc(v):
    return "—" if v is None else "%.0f %%" % (100 * v)


def tableau(lignes_base, whisper, nouveau, sortie=print):
    moteurs = [("Whisper-FR", lambda cle: whisper.get(cle)), ("XLS-R", lambda cle: None)]
    par_cat = defaultdict(lambda: defaultdict(lambda: {"wer": [], "cer": [], "vide": 0, "n": 0}))
    for l in lignes_base:
        cle = nom_fichier(l["chemin_audio"]).lower(); ref = l.get("reference", ""); cat = l.get("categorie") or "?"
        sources = {"Whisper-FR": whisper.get(cle), "XLS-R": l.get("hypothese", ""), "Modèle ajusté": (nouveau or {}).get(cle)}
        for nom, hyp in sources.items():
            if hyp is None:
                continue
            for k in (cat, "TOUS"):
                acc = par_cat[k][nom]
                acc["n"] += 1; acc["wer"].append(wer(ref, hyp)); acc["cer"].append(cer(ref, hyp)); acc["vide"] += (not normaliser(hyp))
    noms = [n for n in ("Whisper-FR", "XLS-R", "Modèle ajusté") if any(n in par_cat[k] for k in par_cat)]
    sortie("\nERREUR MOYENNE SUR LES MOTS (plus c'est bas, mieux c'est) — mots, sans accents ni ponctuation")
    sortie("%-20s %4s " % ("catégorie", "n") + " ".join("%-16s" % n for n in noms))
    for k in sorted(par_cat, key=lambda c: (c == "TOUS", c)):
        sortie("%-20s %4d " % (k, max(par_cat[k][n]["n"] for n in par_cat[k])) + " ".join("%-16s" % pc(moyenne(par_cat[k][n]["wer"])) if n in par_cat[k] else "%-16s" % "—" for n in noms))
    sortie("\nERREUR MOYENNE SUR LES CARACTÈRES")
    for k in sorted(par_cat, key=lambda c: (c == "TOUS", c)):
        sortie("%-20s %4s " % (k, "") + " ".join("%-16s" % pc(moyenne(par_cat[k][n]["cer"])) if n in par_cat[k] else "%-16s" % "—" for n in noms))
    sortie("\nTranscriptions vides : " + ", ".join("%s %d" % (n, par_cat["TOUS"][n]["vide"]) for n in noms if n in par_cat["TOUS"]))


def principal(argv=None, sortie=print):
    p = argparse.ArgumentParser(description="Transcrit les enregistrements de test avec Whisper-FR, comme le webhook.")
    p.add_argument("--base", required=True, help="details_base.csv (chemins audio, références, transcriptions XLS-R)")
    p.add_argument("--nouveau", default="", help="details_nouveau.csv (transcriptions du modèle ajusté), pour la comparaison")
    p.add_argument("--sortie", default="details_whisper.csv")
    p.add_argument("--dossier-audio", default="", help="dossier où chercher les fichiers audio si les chemins du CSV ne sont plus valides")
    p.add_argument("--limite", type=int, default=0, help="ne traiter que les N premiers fichiers (essai)")
    p.add_argument("--url-api", default=URL_PAR_DEFAUT)
    p.add_argument("--modele", default=MODELE_PAR_DEFAUT)
    p.add_argument("--langue", default=LANGUE_PAR_DEFAUT, help="langue forcée (fr comme le webhook) ; vide pour la détection automatique")
    p.add_argument("--oui", action="store_true", help="ne pas demander confirmation avant les appels")
    a = p.parse_args(argv)
    cle, remarques_cle = preparer_cle(os.environ.get("OPENAI_API_KEY", ""))
    base = lire_csv(a.base)
    for col in ("chemin_audio", "reference"):
        if base and col not in base[0]:
            raise ErreurScript("Colonne manquante dans %s : %s" % (a.base, col))
    if a.limite:
        base = base[:a.limite]
    noms = [nom_fichier(l["chemin_audio"]).lower() for l in base]
    doublons = sorted({n for n in noms if noms.count(n) > 1})
    if doublons:
        raise ErreurScript("Plusieurs enregistrements portent le même nom de fichier (%s) : ils s'écraseraient. Renomme-les." % ", ".join(doublons[:5]))
    deja = {}
    if os.path.isfile(a.sortie):
        for l in lire_csv(a.sortie):
            if not l.get("erreur"):
                deja[nom_fichier(l["chemin_audio"]).lower()] = l
    a_faire, introuvables = [], []
    for l in base:
        reel = trouver_audio(l["chemin_audio"], a.dossier_audio)
        if reel is None:
            introuvables.append(nom_fichier(l["chemin_audio"]))
        elif nom_fichier(l["chemin_audio"]).lower() not in deja:
            a_faire.append((l, reel))
    sortie("%d enregistrement(s) dans %s : %d déjà transcrit(s), %d à transcrire, %d introuvable(s)." % (len(base), a.base, len(deja), len(a_faire), len(introuvables)))
    if introuvables:
        sortie("   introuvables : %s%s — passe --dossier-audio si les chemins du CSV ne sont plus bons." % (", ".join(introuvables[:5]), " …" if len(introuvables) > 5 else ""))
    if len(introuvables) == len(base):
        raise ErreurScript("Aucun fichier audio trouvé. Indique le dossier avec --dossier-audio.")
    if a_faire:
        if not cle:
            raise ErreurScript("Variable d'environnement OPENAI_API_KEY absente. Windows : set OPENAI_API_KEY=sk-...")
        sortie("Clé lue dans OPENAI_API_KEY : %s" % resume_cle(cle))
        for rq in remarques_cle:
            sortie("   ATTENTION : %s" % rq)
        taille = sum(os.path.getsize(reel) for _, reel in a_faire)
        sortie("Envoi de %d fichier(s) (%.1f Mo) à %s (modèle %s, langue %s). Coût : quelques centimes, tarif non vérifié." % (len(a_faire), taille / 1e6, a.url_api, a.modele, a.langue or "auto"))
        if not a.oui:
            if not sys.stdin.isatty():
                raise ErreurScript("Exécution non interactive : ajoute --oui pour confirmer les appels.")
            if input("Continuer ? [o/N] ").strip().lower() not in ("o", "oui", "y", "yes"):
                raise ErreurScript("Arrêt demandé : aucun appel n'a été fait.")
    lignes = {k: v for k, v in deja.items()}

    def dans_l_ordre():
        return [lignes[k] for k in noms if k in lignes] + [v for k, v in lignes.items() if k not in noms]
    for i, (l, reel) in enumerate(a_faire, 1):
        with open(reel, "rb") as f:
            contenu = f.read()
        debut = time.time()
        texte = transcrire(contenu, cle, a.url_api, a.modele, a.langue)
        ms = int(1000 * (time.time() - debut))
        cle_f = nom_fichier(l["chemin_audio"]).lower()
        lignes[cle_f] = {"source": "whisper-fr", "categorie": l.get("categorie", ""), "chemin_audio": l["chemin_audio"], "reference": l["reference"],
                         "hypothese": texte or "", "wer": "" if texte is None or wer(l["reference"], texte) is None else "%.4f" % wer(l["reference"], texte),
                         "cer": "" if texte is None or cer(l["reference"], texte) is None else "%.4f" % cer(l["reference"], texte), "latence_ms": ms,
                         "erreur": "" if texte is not None else "transcription échouée (voir message d'erreur)"}
        ecrire_sortie(a.sortie, dans_l_ordre())      # écrit après CHAQUE fichier : une interruption ne fait rien perdre
        if i % 10 == 0:
            sortie("  … %d / %d" % (i, len(a_faire)))
    if not os.path.isfile(a.sortie):
        ecrire_sortie(a.sortie, [])
    ok = {k: v["hypothese"] for k, v in lignes.items() if not v.get("erreur")}
    echecs = sum(1 for v in lignes.values() if v.get("erreur"))
    if echecs:
        sortie("ATTENTION : %d transcription(s) en échec (ligne « erreur » du CSV) ; relance la commande pour les refaire." % echecs)
    nouveau = {}
    if a.nouveau:
        for l in lire_csv(a.nouveau):
            nouveau[nom_fichier(l["chemin_audio"]).lower()] = l.get("hypothese", "")
    tableau(base, ok, nouveau, sortie)
    sortie("\nLES ENREGISTREMENTS EN FRANÇAIS (ce que chaque moteur a écrit) :")
    for l in base:
        if (l.get("categorie") or "") == "francais":
            cle_f = nom_fichier(l["chemin_audio"]).lower()
            sortie("  %s\n     dit        : %s\n     Whisper-FR : %s\n     XLS-R      : %s%s" % (nom_fichier(l["chemin_audio"]), l["reference"], ok.get(cle_f, "(absent)"), l.get("hypothese", ""),
                                                                    ("\n     ajusté     : %s" % nouveau[cle_f]) if cle_f in nouveau else ""))
    sortie("\nFichier écrit : %s. Pour mesurer l'effet sur la lecture des champs par Claude, ajoute à ta commande de test_extraction_catalogue.py :\n"
           "  --moteur whisperfr=%s --conditions nouveau+xlsr,nouveau+xlsr+whisperfr,whisperfr" % (a.sortie, a.sortie))
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
