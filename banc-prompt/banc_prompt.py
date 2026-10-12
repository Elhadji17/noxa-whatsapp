#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
BANC D'ESSAI DE PROMPTS — compare plusieurs versions du prompt de ton webhook sur un même jeu de messages.

Il reproduit l'appel de la PRODUCTION : prompt + contexte d'état de la commande, les 10 derniers tours, max_tokens 1024
et AUCUNE température (la production n'en fixe pas ; l'API prend alors sa valeur par défaut, d'où les répétitions).

USAGE
  python banc_prompt.py lancer                        # lance le banc avec les valeurs par défaut (voir ci-dessous)
  python banc_prompt.py lancer --repetitions 1 --limite 10     # petit essai pour vérifier que tout marche
  python banc_prompt.py lancer --juge                 # ajoute un second avis automatique (Claude juge les réponses)
  python banc_prompt.py noter --feuille resultats/feuille_aveugle.csv --cle resultats/cle_secrete.csv
  python banc_prompt.py verifier-langue               # montre comment la détection de langue classe des exemples

PRÉREQUIS : Python 3.8+, aucune bibliothèque à installer. La clé API est lue dans la variable d'environnement
ANTHROPIC_API_KEY (Windows : set ANTHROPIC_API_KEY=sk-ant-...).

CE QUE FAIT « lancer »
  1. lit chaque prompt de  prompts/*.txt  (le nom du fichier = le nom de la variante) ;
  2. lit les messages de  messages_test.csv  ;
  3. fait répondre Claude à chaque message avec chaque variante (et chaque répétition), en cache : relancer ne refait
     jamais un appel déjà fait, et modifier un prompt invalide seulement les appels de CE prompt ;
  4. note chaque réponse automatiquement (langue, longueur, marqueurs, chiffres hors catalogue, mots attendus/interdits) ;
  5. écrit dans  resultats/ : resultats.csv, rapport.txt, feuille_aveugle.csv (à remplir toi-même) et cle_secrete.csv.

LIMITES À GARDER EN TÊTE
  - La langue d'une réponse est estimée avec de petites listes de mots : c'est une heuristique, vérifiable avec
    « verifier-langue » et corrigeable en bas du fichier de cette section (MOTS_WOLOF, SEUILS).
  - Ces tests automatiques ne jugent PAS la qualité du wolof : seule ta relecture (feuille aveugle) le fait.
  - Un petit échantillon ne prouve rien : le rapport donne des comptes et un test des signes, pas des certitudes.
"""

import argparse
import csv
import hashlib
import io
import json
import os
import random
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.request
from collections import Counter, defaultdict
from math import comb

# ---------------------------------------------------------------------------
# Réglages (identiques à ceux du webhook)
# ---------------------------------------------------------------------------
DOSSIER_SCRIPT = os.path.dirname(os.path.abspath(__file__))   # les fichiers du banc sont cherchés À CÔTÉ DU SCRIPT
VERSION = "v6 (11 oct. 2026)"
MODELE_PAR_DEFAUT = "claude-sonnet-5-5"      # même défaut que ANTHROPIC_MODEL dans le webhook
URL_API = "https://api.anthropic.com/v1/messages"
MAX_TOKENS = 1024                            # comme callClaudeChat
HISTORIQUE_MAX = 10                          # le webhook envoie history.slice(-10)
MARQUEURS = ("PHOTO", "PROPOSITION", "COMMANDE", "BESOIN_WOLOF")
REGEX_MARQUEUR = re.compile(r"\[(?:%s)(?::[^\]]*)?\]" % "|".join(MARQUEURS))

# ---------------------------------------------------------------------------
# Détection de la langue d'une réponse (heuristique, à recalibrer si besoin)
# ---------------------------------------------------------------------------
# Mots wolof NON ambigus (sans accents, en minuscules). On a volontairement écarté ceux qui existent aussi en français
# (ma, la, te, bu, nu, si, sa...) pour ne pas prendre du français pour du wolof.
MOTS_WOLOF = set("""
waaw waw deedeet dedet deet am amna amul amnaa jerejef jerejeff nanga def mangi ngi fi dafa begg beggon beug beugg
bokkul ngeen ngen nga yi bi ji ki gi wi li ku ci ak ag wala wax wakh waxal wakhouma waxuma guiss giss gis nako mako ko
kay lool baax neex tay suba leegi legui niari yeup lepp loolu loola naata nata fan naka meun mun bou tamit nak waaye
waye degg deggu degguma deguma xam xamul ndank sant jox joxal bind nekk nekke ban tontul xaar teyeul fey benn bennen
naar nett yeneen dem kanam kaay waxtaan soxla naa na maa dinaa dina dinama yow waay xale nit boo
""".split())
# Mots-outils français (accents retirés). Les noms du métier (taille, couleur, prix, modèle...) sont NEUTRES : on les
# dit en français même dans une phrase en wolof, ils ne doivent pas faire pencher la balance.
MOTS_FRANCAIS = set("""
le la les un une des du de et ou est sont suis etes vous votre vos je tu ton nous notre on il elle ils elles ce cet
cette ces que qui quoi quel quelle quels quelles pour par avec sans dans sur sous en au aux ai avez avons ont pas ne
plus tres bien merci bonjour bonsoir svp voulez veux voudrais souhaitez dites dis donne envoie ici voici voila
pouvez peux peut pouvons comment combien quand alors donc mais car si ca cela mon ma mes votre revenez reviens
verifie verifier vite oui non d'accord accord quartier disponible disponibles
""".split())
SEUIL_FRANCAIS = 0.25     # part de wolof (parmi les mots wolof + mots-outils français) sous laquelle on dit « français »
SEUIL_WOLOF = 0.50        # à partir de là, « wolof » ; entre les deux, « mixte »
MIN_MOTS_LANGUE = 3       # en dessous de ce nombre de mots reconnus, on ne se prononce pas
# Seuils et règle ci-dessous CALIBRÉS le 11 oct. 2026 sur 36 réponses relues à l'aveugle (« langue_ok ») : accord avec le jugement
# humain 35/36 (une fois F08 relabellisé « mixte » d'après la réponse idéale de l'auteur), contre 21/36 avec la règle précédente. La commande « noter » recalcule cet accord à chaque relecture.


def sans_accents(texte):
    return "".join(c for c in unicodedata.normalize("NFD", str(texte)) if unicodedata.category(c) != "Mn")


def squelette(texte):
    """Ne garde que les lettres et chiffres, sans accents ni majuscules : insensible à la ponctuation, aux emojis et aux espaces."""
    return re.sub(r"[\W_]+", "", sans_accents(texte or "").lower())


def jetons(texte):
    return [j for j in re.split(r"[^a-z]+", sans_accents(texte).lower()) if j]


def classer_langue(texte):
    """Renvoie (étiquette, nb_wolof, nb_français) ; étiquette ∈ français | mixte | wolof | indéterminé."""
    w = f = 0
    for j in jetons(texte):
        if j in MOTS_WOLOF:
            w += 1
        elif len(j) > 1 and j in MOTS_FRANCAIS:
            f += 1
    if w + f < MIN_MOTS_LANGUE:
        return "indéterminé", w, f
    part = w / (w + f)
    if part < SEUIL_FRANCAIS:
        return "français", w, f
    if part >= SEUIL_WOLOF:
        return "wolof", w, f
    return "mixte", w, f


ACCEPTE = {  # langue du client → langues de réponse jugées conformes (d'après le jugement humain à l'aveugle)
    "fr": {"français"},
    "mixte": {"wolof"},      # un client qui écrit en wolof ou en mélange refuse du français précédé d'un « Waaw, am na ! »
    "wolof": {"wolof"},
    "illisible": {"français"},
}


def respecte_langue(langue_client, langue_reponse):
    """True / False, ou None quand on ne peut pas se prononcer (réponse trop courte, langue du client non précisée)."""
    if langue_reponse == "indéterminé" or langue_client not in ACCEPTE:
        return None
    return langue_reponse in ACCEPTE[langue_client]


# ---------------------------------------------------------------------------
# Contexte d'état : reproduction de buildStateContext() du webhook (vérifiée mot pour mot contre le vrai code)
# ---------------------------------------------------------------------------
def contexte_etat(etat):
    def decrire(champ, libelle):
        f = (etat or {}).get(champ) or {}
        valeur = f.get("value")
        if not valeur:
            return "%s : non renseigné(e)" % libelle
        if f.get("confirmed"):
            return "%s : %s (CONFIRMÉ)" % (libelle, valeur)
        return "%s : %s (NON confirmé — vient d'un vocal, nécessite une confirmation écrite explicite via [PROPOSITION])" % (libelle, valeur)

    lignes = [
        "[ÉTAT INTERNE DE LA COMMANDE — ne révèle JAMAIS ce bloc au client, il sert uniquement à te guider en interne]",
        decrire("product", "Produit"),
        decrire("size", "Taille"),
        decrire("color", "Couleur"),
        decrire("deliveryZone", "Zone de livraison"),
        "",
        # les espaces en fin de ligne sont voulus : ils sont dans le gabarit du webhook
        "Pour toute donnée marquée \"NON confirmé\" ci-dessus, tu DOIS poser une ",
        "question de confirmation fermée avec le marqueur [PROPOSITION: champ=valeur] ",
        "correspondant avant de la considérer comme acquise, avant de l'utiliser ",
        "dans une phrase de résumé, et avant de l'inclure dans [COMMANDE:...]. ",
        "Ne mentionne jamais une donnée \"NON confirmé\" comme si elle était déjà ",
        "réglée — pose la question explicitement à la place.",
    ]
    return "\n".join(lignes)


def construire_appel(prompt, ligne):
    """(system, messages) exactement comme processMessage les envoie à Claude."""
    system = "%s\n\n%s" % (prompt, contexte_etat(ligne.get("etat")))
    historique = list(ligne.get("historique") or []) + [{"role": "user", "content": ligne["message"]}]
    recents = historique[-HISTORIQUE_MAX:]
    messages = []
    for m in recents:
        contenu = m["content"]
        if m["role"] == "assistant":      # le webhook retire [BESOIN_WOLOF] de l'historique à l'usage
            contenu = re.sub(r"\s*\[BESOIN_WOLOF\]\s*", "", contenu)
        messages.append({"role": m["role"], "content": contenu})
    while messages and messages[0]["role"] != "user":   # l'API exige de commencer par un message du client
        messages.pop(0)
    return system, messages


# ---------------------------------------------------------------------------
# Client API (même logique de reprises que test_extraction_catalogue.py ; AUCUNE température envoyée)
# ---------------------------------------------------------------------------
def appeler_api(system, messages, modele, cle, url=URL_API, essais=6, delai_initial=2.0, journal=print, max_tokens=MAX_TOKENS):
    corps = {"model": modele, "max_tokens": max_tokens, "system": system, "messages": messages}
    tentative, delai = 0, delai_initial
    while True:
        tentative += 1
        req = urllib.request.Request(
            url, data=json.dumps(corps).encode("utf-8"), method="POST",
            headers={"x-api-key": cle, "anthropic-version": "2023-06-01", "content-type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                donnees = json.loads(r.read().decode("utf-8"))
            return "".join(b.get("text", "") for b in donnees.get("content", []) if b.get("type") == "text")
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", errors="replace")
            if e.code in (401, 403):
                raise SystemExit("Clé API refusée (HTTP %s) : %s" % (e.code, detail[:200]))
            if e.code == 404:
                raise SystemExit("Modèle ou adresse introuvable (HTTP 404) : %s" % detail[:200])
            if e.code in (408, 409, 429, 500, 502, 503, 504, 529) and tentative < essais:
                try:
                    attente = float(e.headers.get("retry-after") or delai)
                except ValueError:
                    attente = delai
                time.sleep(min(attente, 60))
                delai = min(delai * 2, 60)
                continue
            raise SystemExit("Erreur API HTTP %s : %s" % (e.code, detail[:300]))
        except (urllib.error.URLError, TimeoutError) as e:
            if tentative < essais:
                time.sleep(delai)
                delai = min(delai * 2, 60)
                continue
            raise SystemExit("Réseau indisponible : %s" % e)


class Cache:
    def __init__(self, chemin):
        self.chemin = chemin
        self.d = {}
        if chemin and os.path.isfile(chemin):
            with open(chemin, encoding="utf-8") as f:
                self.d = json.load(f)

    @staticmethod
    def cle(modele, system, messages, rep):
        brut = json.dumps([modele, system, messages, rep], ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(brut.encode("utf-8")).hexdigest()

    def get(self, k):
        return self.d.get(k)

    def put(self, k, texte):
        self.d[k] = texte
        if self.chemin:
            with open(self.chemin, "w", encoding="utf-8") as f:
                json.dump(self.d, f, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Lecture des prompts et des messages
# ---------------------------------------------------------------------------
class ErreurBanc(Exception):
    pass


def lire_fichier_texte(chemin):
    """UTF-8 (avec ou sans BOM) d'abord ; sinon cp1252, l'encodage qu'Excel français utilise pour « CSV (séparateur : point-virgule) »."""
    with open(chemin, "rb") as f:
        brut = f.read()
    for encodage in ("utf-8-sig", "cp1252"):
        try:
            return brut.decode(encodage)
        except UnicodeDecodeError:
            continue
    return brut.decode("latin-1")


def lire_prompts(dossier, filtre=None):
    if not os.path.isdir(dossier):
        raise ErreurBanc("Dossier de prompts introuvable : %s" % dossier)
    prompts = {}
    for nom in sorted(os.listdir(dossier)):
        if nom.lower().endswith(".txt"):
            variante = nom[:-4]
            if filtre and variante not in filtre:
                continue
            texte = lire_fichier_texte(os.path.join(dossier, nom)).replace("\r\n", "\n")
            if not texte.strip():
                raise ErreurBanc("Le prompt %s est vide." % nom)
            prompts[variante] = texte
    if not prompts:
        raise ErreurBanc("Aucun prompt (*.txt) trouvé dans %s%s." % (dossier, " pour les variantes demandées" if filtre else ""))
    if filtre:
        manquantes = sorted(set(filtre) - set(prompts))
        if manquantes:
            raise ErreurBanc("Variante(s) introuvable(s) : %s. Disponibles : %s" % (", ".join(manquantes), ", ".join(sorted(prompts))))
    return prompts


COLONNES_OBLIGATOIRES = ("id", "categorie", "langue_client", "message")


def lire_json_cellule(valeur, ligne_id, colonne, attendu):
    valeur = (valeur or "").strip()
    if not valeur:
        return attendu()
    try:
        objet = json.loads(valeur)
    except ValueError as e:
        raise ErreurBanc("Message %s : la colonne « %s » n'est pas un JSON valide (%s)." % (ligne_id, colonne, e))
    return objet


def lire_messages(chemin):
    if not os.path.isfile(chemin):
        raise ErreurBanc("Fichier de messages introuvable : %s" % chemin)
    texte_csv = lire_fichier_texte(chemin)
    delimiteur = ";" if texte_csv[:4096].count(";") >= texte_csv[:4096].count(",") else ","
    with io.StringIO(texte_csv, newline="") as f:
        lecteur = csv.DictReader(f, delimiter=delimiteur)
        manquantes = [c for c in COLONNES_OBLIGATOIRES if c not in (lecteur.fieldnames or [])]
        if manquantes:
            raise ErreurBanc("Colonne(s) manquante(s) dans %s : %s" % (chemin, ", ".join(manquantes)))
        lignes, vus = [], set()
        for brut in lecteur:
            if not (brut.get("id") or "").strip():
                continue
            ident = brut["id"].strip()
            if ident in vus:
                raise ErreurBanc("Identifiant de message en double : %s" % ident)
            vus.add(ident)
            if not (brut.get("message") or "").strip():
                raise ErreurBanc("Message %s : la colonne « message » est vide." % ident)
            historique = lire_json_cellule(brut.get("historique"), ident, "historique", list)
            etat = lire_json_cellule(brut.get("etat"), ident, "etat", dict)
            if not isinstance(historique, list) or not all(isinstance(m, dict) and m.get("role") in ("user", "assistant") and "content" in m for m in historique):
                raise ErreurBanc("Message %s : « historique » doit être une liste de {\"role\": \"user\"|\"assistant\", \"content\": ...}." % ident)
            if not isinstance(etat, dict):
                raise ErreurBanc("Message %s : « etat » doit être un objet JSON." % ident)
            lignes.append({
                "id": ident, "categorie": (brut.get("categorie") or "").strip(), "langue_client": (brut.get("langue_client") or "").strip(),
                "message": brut["message"], "historique": historique, "etat": etat,
                "verite": (brut.get("verite") or "").strip(), "attendu": (brut.get("attendu") or "").strip(),
                "doit_un_de": [x.strip() for x in (brut.get("doit_un_de") or "").split("|") if x.strip()],
                "interdit": [x.strip() for x in (brut.get("interdit") or "").split("|") if x.strip()],
                "marqueur_attendu": [x.strip() for x in (brut.get("marqueur_attendu") or "").split("|") if x.strip()],
                "marqueur_interdit": [x.strip() for x in (brut.get("marqueur_interdit") or "").split("|") if x.strip()],
            })
    if not lignes:
        raise ErreurBanc("Aucun message dans %s." % chemin)
    return lignes


# ---------------------------------------------------------------------------
# Notation automatique
# ---------------------------------------------------------------------------
REGEX_NOMBRE = re.compile(r"\d{1,3}(?:[ \u00a0\u202f.]\d{3})+|\d+")


def nombres_du_texte(texte):
    sortie = []
    for m in REGEX_NOMBRE.finditer(texte):
        chiffres = re.sub(r"\D", "", m.group(0))
        if chiffres:
            sortie.append(int(chiffres))
    return sortie


def nombres_autorises(prompt):
    """Tous les nombres de 3 chiffres ou plus du prompt (prix, frais, numéro Wave), et les sommes de deux d'entre eux
    inférieures à 200 000 (prix + livraison). Un prix annoncé hors de cet ensemble est suspect."""
    base = sorted({n for n in nombres_du_texte(prompt) if n >= 100})
    somme = {a + b for a in base for b in base if a + b < 200000}
    return set(base) | somme


def texte_visible(reponse):
    return re.sub(r"\n{3,}", "\n\n", REGEX_MARQUEUR.sub("", reponse)).strip()


def marqueurs_de(reponse):
    return sorted({m.group(0).strip("[]").split(":")[0] for m in REGEX_MARQUEUR.finditer(reponse)})


def contient_un_de(texte, mots):
    t = texte.lower()
    return any(m.lower() in t for m in mots)


def evaluer(ligne, reponse, autorises):
    visible = texte_visible(reponse)
    langue, w, f = classer_langue(visible)
    marqueurs = marqueurs_de(reponse)
    ecrits_avant = set(nombres_du_texte(ligne["message"]))      # un chiffre que le client a lui-même écrit n'est pas « inventé »
    for m in ligne.get("historique") or []:
        ecrits_avant |= set(nombres_du_texte(m["content"]))
    hors = sorted(n for n in set(nombres_du_texte(visible)) if n >= 1000 and n not in autorises and n not in ecrits_avant)
    res = {
        "reponse_visible": visible, "marqueurs": "|".join(marqueurs), "mots": len(visible.split()),
        "langue_reponse": langue, "mots_wolof": w, "mots_francais": f,
        "respecte_langue": respecte_langue(ligne["langue_client"], langue),
        "chiffres_hors_catalogue": "|".join(str(n) for n in hors),
        "mot_attendu_ok": (contient_un_de(visible, ligne["doit_un_de"]) if ligne["doit_un_de"] else None),
        "mot_interdit_ok": (not contient_un_de(visible, ligne["interdit"]) if ligne["interdit"] else None),
        "marqueur_attendu_ok": (all(m in marqueurs for m in ligne["marqueur_attendu"]) if ligne["marqueur_attendu"] else None),
        "marqueur_interdit_ok": (not any(m in marqueurs for m in ligne["marqueur_interdit"]) if ligne["marqueur_interdit"] else None),
    }
    controles = [res["respecte_langue"], res["mot_attendu_ok"], res["mot_interdit_ok"], res["marqueur_attendu_ok"], res["marqueur_interdit_ok"], not hors]
    res["ok_global"] = all(c for c in controles if c is not None)
    res["echecs"] = "|".join(nom for nom, c in (("langue", res["respecte_langue"]), ("mot_attendu", res["mot_attendu_ok"]), ("mot_interdit", res["mot_interdit_ok"]),
                                               ("marqueur_attendu", res["marqueur_attendu_ok"]), ("marqueur_interdit", res["marqueur_interdit_ok"]),
                                               ("chiffre_hors_catalogue", (not hors) if hors else None)) if c is False)
    return res


# ---------------------------------------------------------------------------
# Second avis automatique (optionnel)
# ---------------------------------------------------------------------------
SYSTEME_JUGE = (
    "Tu es un évaluateur rigoureux des réponses d'un assistant WhatsApp pour une boutique de chaussures à Dakar. "
    "Tu ne juges PAS la qualité ni l'orthographe du wolof (tu n'en es pas capable de façon fiable) : tu juges la langue "
    "choisie, la progression de la vente et le respect des faits. Réponds UNIQUEMENT par un objet JSON, sans texte autour."
)


def prompt_juge(ligne, reponse_visible, catalogue):
    return (
        "CATALOGUE DE LA BOUTIQUE (seule source de faits autorisée) :\n%s\n\n"
        "LANGUE DU CLIENT : %s\nMESSAGE REÇU PAR L'ASSISTANT : %s\nCE QUE LE CLIENT A RÉELLEMENT DIT (si connu) : %s\n"
        "ATTENDU (indication du relecteur) : %s\n\nRÉPONSE DE L'ASSISTANT (marqueurs techniques retirés) :\n%s\n\n"
        "Réponds par un JSON avec exactement ces clés : "
        "\"langue_reponse\" (\"français\", \"mixte\" ou \"wolof\"), "
        "\"respecte_langue_client\" (booléen : l'assistant répond-il dans la langue du client ?), "
        "\"avance_la_vente\" (booléen : la réponse fait-elle progresser la commande ou pose-t-elle la bonne question ?), "
        "\"invente_un_fait\" (booléen : prix, produit, promotion ou disponibilité absents du catalogue ?), "
        "\"propose_sans_indice\" (booléen : propose ou affirme-t-il un produit ou une valeur sans aucun indice dans le message ?), "
        "\"commentaire\" (une phrase)."
    ) % (catalogue, ligne["langue_client"] or "non précisée", ligne["message"], ligne["verite"] or "inconnu", ligne["attendu"] or "—", reponse_visible)


def extraire_catalogue(prompt):
    i = prompt.find("PRODUITS :")
    j = prompt.find("GESTION DE L'INTÉRÊT CLIENT", i if i >= 0 else 0)
    if i >= 0 and j > i:
        return prompt[i:j].strip()
    return prompt[:3000]


def lire_verdict(texte):
    m = re.search(r"\{.*\}", texte or "", flags=re.DOTALL)
    if not m:
        return None
    try:
        v = json.loads(m.group(0))
    except ValueError:
        return None
    if not isinstance(v, dict):
        return None
    return v


# ---------------------------------------------------------------------------
# Statistiques et rapport
# ---------------------------------------------------------------------------
def test_des_signes(gagne, perdu):
    """p-value bilatérale exacte du test des signes (égalités écartées)."""
    n = gagne + perdu
    if n == 0:
        return 1.0
    k = min(gagne, perdu)
    return min(1.0, 2 * sum(comb(n, i) for i in range(k + 1)) / (2 ** n))


def pourcent(a, b):
    return "—" if not b else "%d %%" % round(100 * a / b)


def moyenne(valeurs):
    valeurs = list(valeurs)
    return sum(valeurs) / len(valeurs) if valeurs else 0.0


def fabriquer_rapport(resultats, variantes, repetitions, contamines=None):
    L = []
    ajouter = L.append
    par_variante = defaultdict(list)
    for r in resultats:
        par_variante[r["variante"]].append(r)
    ajouter("=" * 78)
    ajouter("RAPPORT DU BANC D'ESSAI DE PROMPTS")
    ajouter("=" * 78)
    ids = sorted({r["id"] for r in resultats})
    ajouter("%d variantes × %d messages × %d répétition(s) = %d réponses." % (len(variantes), len(ids), repetitions, len(resultats)))
    if contamines:
        ajouter("%d message(s) écarté(s), car leur texte figure comme exemple dans un prompt comparé : %s" % (len(contamines), ", ".join(sorted(contamines))))
    ajouter("")
    ajouter("1. VUE D'ENSEMBLE (par variante)")
    ajouter("%-22s %7s %9s %9s %8s %9s %9s" % ("variante", "réponses", "langue ok", "tout ok", "mots", "prix hors", "marq. ✗"))
    for v in variantes:
        rs = par_variante[v]
        evalu = [r for r in rs if r["respecte_langue"] is not None]
        marq_pb = [r for r in rs if r["marqueur_attendu_ok"] is False or r["marqueur_interdit_ok"] is False]
        ajouter("%-22s %7d %9s %9s %8.1f %9d %9d" % (v, len(rs), pourcent(sum(1 for r in evalu if r["respecte_langue"]), len(evalu)),
                                                    pourcent(sum(1 for r in rs if r["ok_global"]), len(rs)), moyenne(r["mots"] for r in rs),
                                                    sum(1 for r in rs if r["chiffres_hors_catalogue"]), len(marq_pb)))
    ajouter("  langue ok : part des réponses dont la langue correspond à celle du client (hors réponses trop courtes pour être classées)")
    ajouter("  tout ok   : toutes les vérifications applicables au message sont passées")
    ajouter("  prix hors : réponses annonçant un montant absent du catalogue (et de ses sommes prix + livraison), hors chiffres déjà écrits par le client")
    ajouter("  marq. ✗   : marqueur attendu absent ou marqueur interdit présent")
    ajouter("")
    ajouter("2. LANGUE DE LA RÉPONSE SELON LA LANGUE DU CLIENT")
    for lc in ("fr", "mixte", "wolof", "illisible"):
        if not any(r["langue_client"] == lc for r in resultats):
            continue
        ajouter("  client : %s" % lc)
        for v in variantes:
            c = Counter(r["langue_reponse"] for r in par_variante[v] if r["langue_client"] == lc)
            total = sum(c.values())
            ajouter("    %-22s français %-6s mixte %-6s wolof %-6s indéterminé %-6s (n=%d)" % (
                v, pourcent(c["français"], total), pourcent(c["mixte"], total), pourcent(c["wolof"], total), pourcent(c["indéterminé"], total), total))
    ajouter("  Règle de langue, calibrée sur ta relecture à l'aveugle (35 accords sur 36 ; 21 avec l'ancienne règle) : pour un client « mixte » ou")
    ajouter("  « wolof », seule une réponse principalement EN WOLOF est jugée conforme (du français précédé d'un « Waaw, am na ! » ne l'est pas) ; pour")
    ajouter("  un client « fr » ou un vocal « illisible », seule une réponse en français l'est. Pour changer une étiquette : messages_test.csv, puis")
    ajouter("  relance « lancer » (tout est en cache : instantané et gratuit).")
    ajouter("")
    ajouter("3. « TOUT OK » PAR CATÉGORIE DE MESSAGE")
    categories = sorted({r["categorie"] for r in resultats})
    ajouter("%-14s " % "catégorie" + " ".join("%-18s" % v[:18] for v in variantes))
    for cat in categories:
        cellules = []
        for v in variantes:
            rs = [r for r in par_variante[v] if r["categorie"] == cat]
            cellules.append("%-18s" % ("%s (n=%d)" % (pourcent(sum(1 for r in rs if r["ok_global"]), len(rs)), len(rs))))
        ajouter("%-14s " % cat + " ".join(cellules))
    ajouter("")
    ajouter("4. COMPARAISONS DEUX À DEUX (message par message, moyenne des répétitions ; test des signes)")
    def score_msg(v, mid):
        rs = [r for r in par_variante[v] if r["id"] == mid]
        return moyenne(1.0 if r["ok_global"] else 0.0 for r in rs)
    for i, a in enumerate(variantes):
        for b in variantes[i + 1:]:
            g = sum(1 for m in ids if score_msg(b, m) > score_msg(a, m))
            p = sum(1 for m in ids if score_msg(b, m) < score_msg(a, m))
            ajouter("  %s → %s : %s fait mieux sur %d messages, moins bien sur %d, égal sur %d ; p = %.3f" % (a, b, b, g, p, len(ids) - g - p, test_des_signes(g, p)))
    ajouter("  (p petit = différence peu probable par hasard ; avec ~40 messages, ne tire aucune conclusion de p > 0,10)")
    ajouter("")
    ajouter("5. ÉCHECS À REGARDER (jusqu'à 40)")
    echecs = [r for r in resultats if not r["ok_global"]]
    for r in echecs[:40]:
        ajouter("  [%s] %s rép.%d : %s | %s" % (r["variante"], r["id"], r["rep"], r["echecs"], r["reponse_visible"].replace("\n", " ")[:110]))
    if len(echecs) > 40:
        ajouter("  … et %d autres dans resultats.csv" % (len(echecs) - 40))
    if any(r.get("juge_langue") for r in resultats):
        ajouter("")
        ajouter("6. SECOND AVIS AUTOMATIQUE (Claude juge ; ne juge pas le wolof)")
        ajouter("%-22s %12s %13s %13s %14s" % ("variante", "langue ok", "avance vente", "invente fait", "propose s.indice"))
        for v in variantes:
            rs = [r for r in par_variante[v] if r.get("juge_langue")]
            ajouter("%-22s %12s %13s %13s %14s" % (v, pourcent(sum(1 for r in rs if r["juge_respecte"] is True), len(rs)), pourcent(sum(1 for r in rs if r["juge_avance_vente"] is True), len(rs)),
                                                    pourcent(sum(1 for r in rs if r["juge_invente"] is True), len(rs)), pourcent(sum(1 for r in rs if r["juge_propose_sans_indice"] is True), len(rs))))
        accord = [r for r in resultats if r.get("juge_langue") in ("français", "mixte", "wolof") and r["langue_reponse"] != "indéterminé"]
        ajouter("  accord entre la détection par mots et le juge sur la langue de la réponse : %s (n=%d)" % (pourcent(sum(1 for r in accord if r["juge_langue"] == r["langue_reponse"]), len(accord)), len(accord)))
    ajouter("")
    ajouter("RAPPEL : ces vérifications automatiques ne jugent ni le naturel ni l'exactitude du wolof. Relis feuille_aveugle.csv.")
    return "\n".join(L)


# ---------------------------------------------------------------------------
# Feuille de relecture à l'aveugle
# ---------------------------------------------------------------------------
COLONNES_FEUILLE = ["ligne", "message", "ce_que_le_client_a_dit", "reponse", "langue_ok (o/n)", "avance_vente (o/n)", "naturel (1-5)", "commentaire", "jeu"]
COLONNES_CLE = ["ligne", "id", "variante", "rep", "jeu"]


def fabriquer_feuille(resultats, variantes, echantillon, graine, rep_choisie=1):
    ids = sorted({r["id"] for r in resultats})
    rng = random.Random(graine)
    nb_msgs = max(1, min(len(ids), echantillon // max(1, len(variantes))))
    choisis = rng.sample(ids, nb_msgs)
    feuille, cle, n = [], [], 0
    lignes_cle = []
    for mid in choisis:
        groupe = [r for r in resultats if r["id"] == mid and r["rep"] == rep_choisie]
        rng.shuffle(groupe)
        for r in groupe:
            n += 1
            feuille.append({"ligne": n, "message": r["message"], "ce_que_le_client_a_dit": r["verite"], "reponse": r["reponse_visible"],
                            "langue_ok (o/n)": "", "avance_vente (o/n)": "", "naturel (1-5)": "", "commentaire": ""})
            cle.append({"ligne": n, "id": mid, "variante": r["variante"], "rep": r["rep"]})
            lignes_cle.append([n, mid, r["variante"], r["rep"], hashlib.sha256(r["reponse_visible"].encode("utf-8")).hexdigest()])
    # « jeu » : empreinte du contenu exact de la feuille. Elle est écrite à la fois dans la feuille et dans sa clé, pour que
    # « noter » refuse d'associer une feuille à une clé d'un AUTRE lancement (les notes iraient aux mauvaises variantes).
    jeu = hashlib.sha256(json.dumps(lignes_cle, ensure_ascii=False).encode("utf-8")).hexdigest()[:10]
    for ligne_f, ligne_c in zip(feuille, cle):
        ligne_f["jeu"] = jeu
        ligne_c["jeu"] = jeu
    return feuille, cle


def message_verrou(chemin):
    return ("Impossible d'écrire %s : accès refusé.\n  Sous Windows, c'est presque toujours que le fichier est ouvert dans Excel. Ferme-le puis relance :\n"
            "  rien n'est perdu, les réponses sont en cache (la relance ne coûte rien)." % chemin)


def ecrire_csv(chemin, lignes, colonnes):
    try:
        with open(chemin, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=colonnes, delimiter=";", quoting=csv.QUOTE_ALL, extrasaction="ignore")
            w.writeheader()
            for l in lignes:
                w.writerow(l)
    except PermissionError:
        raise ErreurBanc(message_verrou(chemin))


def ecrire_texte(chemin, texte):
    try:
        with open(chemin, "w", encoding="utf-8") as f:
            f.write(texte)
    except PermissionError:
        raise ErreurBanc(message_verrou(chemin))


def feuille_contient_des_notes(chemin):
    """True si la feuille existe et qu'une seule case de notation est remplie (dans le doute, on la protège)."""
    if not os.path.isfile(chemin):
        return False
    try:
        lignes = lire_csv_libre(chemin)
    except Exception:
        return True
    colonnes = [c for c in (lignes[0].keys() if lignes else []) if c and c.split(" ")[0] in ("langue_ok", "avance_vente", "naturel", "commentaire")]
    return any((l.get(c) or "").strip() for l in lignes for c in colonnes)


def ecrire_feuille_et_cle(dossier, feuille, cle, sortie=print):
    """Écrit la feuille et sa clé COMME UN COUPLE, sans jamais écraser une feuille déjà annotée.
    - feuille existante identique aux nouveaux résultats (annotée ou non) : on n'y touche pas, rien n'est écrit — en particulier
      pas d'erreur si elle est ouverte dans Excel ;
    - feuille existante annotée mais différente : conservée INTACTE avec sa clé ; le nouveau couple porte un horodatage ;
    - feuille existante vierge mais différente, ou absente : écrite (remplacée)."""
    nom_f, nom_c = "feuille_aveugle.csv", "cle_secrete.csv"
    chemin_f = os.path.join(dossier, nom_f)
    def normaliser(texte):
        # Excel (« CSV » ANSI) remplace chaque emoji par « ? », peut réécrire les retours à la ligne d'une cellule et rogner les
        # espaces : on ne compare donc que les LETTRES et les CHIFFRES, sans accents ni majuscules.
        return squelette(texte)
    nouvelle = [(normaliser(l["message"]), normaliser(l["reponse"])) for l in feuille]
    if os.path.isfile(chemin_f):
        try:
            existante = [(normaliser(l.get("message")), normaliser(l.get("reponse"))) for l in lire_csv_libre(chemin_f)]
        except Exception:
            existante = None
        if existante == nouvelle:
            annotee = feuille_contient_des_notes(chemin_f)
            sortie("La feuille existante (%s) correspond déjà à ces résultats : elle est conservée telle quelle%s, rien n'a été réécrit." % (nom_f, ", avec tes notes" if annotee else ""))
            return nom_f, nom_c
    if feuille_contient_des_notes(chemin_f):
        horodatage = time.strftime("%Y%m%d-%H%M%S")
        nom_f, nom_c = "feuille_aveugle_%s.csv" % horodatage, "cle_secrete_%s.csv" % horodatage
        sortie("ATTENTION : %s contient déjà des notes, elle est conservée INTACTE (avec sa clé cle_secrete.csv)." % "feuille_aveugle.csv")
        sortie("Les résultats ont changé : une NOUVELLE feuille a été écrite : %s (clé : %s)." % (nom_f, nom_c))
        sortie("Pour la noter : python banc_prompt.py noter --feuille resultats\\%s --cle resultats\\%s" % (nom_f, nom_c))
    chemin_f, chemin_c = os.path.join(dossier, nom_f), os.path.join(dossier, nom_c)
    tmp_f, tmp_c = chemin_f + ".tmp", chemin_c + ".tmp"
    try:
        ecrire_csv(tmp_f, feuille, COLONNES_FEUILLE)
        ecrire_csv(tmp_c, cle, COLONNES_CLE)
        try:
            os.replace(tmp_f, chemin_f)
        except PermissionError:
            raise ErreurBanc(message_verrou(chemin_f))
        try:
            os.replace(tmp_c, chemin_c)
        except PermissionError:
            raise ErreurBanc(message_verrou(chemin_c) + "\n  ATTENTION : la feuille a été réécrite mais pas sa clé : relance « lancer » avant de noter.")
    finally:
        for t in (tmp_f, tmp_c):
            if os.path.exists(t):
                os.remove(t)
    return nom_f, nom_c


def lire_csv_libre(chemin):
    texte_csv = lire_fichier_texte(chemin)
    delimiteur = ";" if texte_csv[:4096].count(";") >= texte_csv[:4096].count(",") else ","
    with io.StringIO(texte_csv, newline="") as f:
        return list(csv.DictReader(f, delimiter=delimiteur))


def oui_non(valeur):
    v = (valeur or "").strip().lower()
    if v in ("o", "oui", "y", "yes", "1", "vrai", "true"):
        return True
    if v in ("n", "non", "no", "0", "faux", "false"):
        return False
    return None


def noter(chemin_feuille, chemin_cle, chemin_messages=None):
    for chemin in (chemin_feuille, chemin_cle):
        if not os.path.isfile(chemin):
            raise ErreurBanc("Fichier introuvable : %s\n  Ces fichiers sont créés par « python banc_prompt.py lancer » : le banc a-t-il été lancé jusqu'au bout ?" % chemin)
    feuille, cle = lire_csv_libre(chemin_feuille), {r["ligne"]: r for r in lire_csv_libre(chemin_cle)}
    cols = {c.split(" ")[0]: c for c in (feuille[0].keys() if feuille else [])}
    for requis in ("ligne", "langue_ok", "avance_vente", "naturel"):
        if requis not in cols:
            raise ErreurBanc("La feuille n'a pas la colonne « %s »." % requis)
    jeux_feuille = {(l.get("jeu") or "").strip() for l in feuille} - {""}
    jeux_cle = {(r.get("jeu") or "").strip() for r in cle.values()} - {""}
    if jeux_feuille and jeux_cle and jeux_feuille != jeux_cle:
        raise ErreurBanc("Cette feuille et cette clé ne viennent PAS du même lancement (empreintes %s et %s) : tes notes seraient attribuées aux mauvaises variantes.\n"
                         "  Utilise la clé qui porte le même horodatage que la feuille (cle_secrete_AAAAMMJJ-HHMMSS.csv pour feuille_aveugle_AAAAMMJJ-HHMMSS.csv)."
                         % (",".join(sorted(jeux_feuille)), ",".join(sorted(jeux_cle))))
    sans_controle = not (jeux_feuille and jeux_cle)
    par_var = defaultdict(lambda: {"langue": [], "vente": [], "naturel": [], "n": 0})
    remplies = 0
    for l in feuille:
        k = cle.get((l["ligne"] or "").strip())
        if not k:
            continue
        v = par_var[k["variante"]]
        v["n"] += 1
        lo, av = oui_non(l[cols["langue_ok"]]), oui_non(l[cols["avance_vente"]])
        try:
            nat = float((l[cols["naturel"]] or "").replace(",", "."))
        except ValueError:
            nat = None
        if lo is not None:
            v["langue"].append(lo)
        if av is not None:
            v["vente"].append(av)
        if nat is not None and 1 <= nat <= 5:
            v["naturel"].append(nat)
        if lo is not None or av is not None or nat is not None:
            remplies += 1
    if not remplies:
        raise ErreurBanc("Aucune note trouvée dans %s (%d lignes à noter).\n  Ouvre-la dans Excel, remplis « langue_ok (o/n) », « avance_vente (o/n) » et « naturel (1-5) », enregistre en CSV, puis relance « noter »." % (chemin_feuille, len(feuille)))
    sortie = ["RELECTURE À L'AVEUGLE — résultats par variante", "%-22s %6s %12s %14s %12s" % ("variante", "lignes", "langue ok", "avance vente", "naturel (/5)")]
    for nom in sorted(par_var):
        v = par_var[nom]
        sortie.append("%-22s %6d %12s %14s %12s" % (nom, v["n"], pourcent(sum(v["langue"]), len(v["langue"])), pourcent(sum(v["vente"]), len(v["vente"])),
                                                    "%.2f" % moyenne(v["naturel"]) if v["naturel"] else "—"))
    sortie.append("")
    sortie.append("Les variantes n'étaient pas visibles pendant ta relecture : ces chiffres sont donc les moins biaisés du banc.")
    if sans_controle:
        sortie.append("(Feuille ou clé sans empreinte de contrôle, venant d'une version plus ancienne du script : je ne peux pas vérifier qu'elles vont ensemble.)")
    if chemin_messages and os.path.isfile(chemin_messages):
        try:
            langues = {m["id"]: m["langue_client"] for m in lire_messages(chemin_messages)}
        except ErreurBanc:
            langues = {}
        lignes_cmp = []
        for l in feuille:
            k = cle.get((l["ligne"] or "").strip())
            lo = oui_non(l.get(cols["langue_ok"]))
            if not k or lo is None or k["id"] not in langues or langues[k["id"]] not in ACCEPTE:
                continue
            etiquette = classer_langue(l.get("reponse") or "")[0]
            lignes_cmp.append((l["ligne"], k["id"], langues[k["id"]], etiquette, lo, respecte_langue(langues[k["id"]], etiquette)))
        if lignes_cmp:
            accords = sum(1 for _, _, _, _, lo, regle in lignes_cmp if regle is not None and regle == lo)
            sortie.append("")
            sortie.append("ACCORD ENTRE LA DÉTECTION AUTOMATIQUE ET TON JUGEMENT SUR LA LANGUE (colonne langue_ok)")
            sortie.append("  %d / %d lignes (une réponse trop courte pour être classée compte comme un désaccord)." % (accords, len(lignes_cmp)))
            desaccords = [c for c in lignes_cmp if c[5] is None or c[5] != c[4]]
            for ligne, mid, lc, et, lo, regle in desaccords[:15]:
                sortie.append("   ligne %s (%s, client %s) : détectée « %s », ton verdict : %s, la règle disait : %s" % (
                    ligne, mid, lc, et, "conforme" if lo else "non conforme", "non classable" if regle is None else ("conforme" if regle else "non conforme")))
            sortie.append("  Un accord faible veut dire que la détection ne reflète pas ton critère : ajuste ACCEPTE et les seuils en haut du script.")
    return "\n".join(sortie)


# ---------------------------------------------------------------------------
# Exécution
# ---------------------------------------------------------------------------
def estimer(prompts, lignes, repetitions):
    nb = len(prompts) * len(lignes) * repetitions
    caracteres = sum(len(construire_appel(p, l)[0]) + sum(len(m["content"]) for m in construire_appel(p, l)[1]) for p in prompts.values() for l in lignes) * repetitions
    return nb, caracteres


def verifier_fichiers(args):
    manquants = []
    if not os.path.isdir(args.prompts):
        manquants.append("le dossier de prompts : %s" % args.prompts)
    if not os.path.isfile(args.messages):
        manquants.append("le fichier de messages : %s" % args.messages)
    if manquants:
        raise ErreurBanc(
            "Introuvable : %s.\n  Le banc attend, À CÔTÉ de banc_prompt.py (%s), un dossier « prompts » contenant A_actuel.txt,\n"
            "  B_correctif_langue.txt et C_restructure.txt, ainsi que le fichier messages_test.csv.\n"
            "  Si tu as téléchargé les fichiers un par un, range-les ainsi (ou utilise banc-prompt.zip, qui garde l'arborescence)."
            % (" ; ".join(manquants), DOSSIER_SCRIPT))


def exemples_du_prompt(prompt):
    """Les phrases de client données en EXEMPLE dans un prompt : les lignes « Client : … »."""
    return {squelette(m.group(1)) for m in re.finditer(r"(?m)^Client\s*:\s*(.+?)\s*$", prompt) if squelette(m.group(1))}


def messages_contamines(prompts, lignes):
    """{id du message : [variantes]} pour les messages dont le texte (ou ce que le client a réellement dit, pour un vocal) figure déjà
    comme exemple dans le prompt d'une variante : tester cette variante dessus reviendrait à lui montrer la réponse avant l'examen."""
    contamines = {}
    for variante, prompt in prompts.items():
        exemples = exemples_du_prompt(prompt)
        for l in lignes:
            if any(t and squelette(t) in exemples for t in (l["message"], l["verite"])):
                contamines.setdefault(l["id"], []).append(variante)
    return contamines


def lancer(args, client=None, entree=input, sortie=print):
    sortie("banc_prompt %s" % VERSION)
    verifier_fichiers(args)
    prompts = lire_prompts(args.prompts, [v.strip() for v in args.variantes.split(",")] if args.variantes else None)
    lignes = lire_messages(args.messages)
    if args.limite:
        lignes = lignes[:args.limite]
    contamines = {} if getattr(args, "inclure_contamines", False) else messages_contamines(prompts, lignes)
    if contamines:
        sortie("%d message(s) EXCLU(S) : leur texte figure déjà comme EXEMPLE dans un prompt comparé, les tester serait de la triche :" % len(contamines))
        for mid in sorted(contamines):
            sortie("   %-5s (exemple dans : %s)" % (mid, ", ".join(sorted(contamines[mid]))))
        lignes = [l for l in lignes if l["id"] not in contamines]
        if not lignes:
            raise ErreurBanc("Tous les messages sont exclus parce qu'ils figurent comme exemples dans les prompts comparés. Ajoute des messages inédits à messages_test.csv.")
    variantes = list(prompts)
    cle_api = os.environ.get("ANTHROPIC_API_KEY", "")
    if client is None:
        if not cle_api:
            raise ErreurBanc("Variable d'environnement ANTHROPIC_API_KEY absente. Windows : set ANTHROPIC_API_KEY=sk-ant-...")
        client = lambda system, messages, modele: appeler_api(system, messages, modele, cle_api, url=getattr(args, "url_api", URL_API), journal=sortie)
    os.makedirs(args.sortie, exist_ok=True)
    cache = Cache(os.path.join(args.sortie, "cache.json"))
    nb, caracteres = estimer(prompts, lignes, args.repetitions)
    deja = sum(1 for v in variantes for l in lignes for rep in range(1, args.repetitions + 1)
               if cache.get(Cache.cle(args.modele, *construire_appel(prompts[v], l), rep)) is not None)
    sortie("%d variante(s) × %d message(s) × %d répétition(s) = %d appels, dont %d déjà en cache." % (len(variantes), len(lignes), args.repetitions, nb, deja))
    sortie("Volume envoyé : environ %.2f million de caractères (≈ %.2f million de tokens d'entrée, à raison de ~3,5 caractères par token)." % (caracteres / 1e6, caracteres / 3.5e6))
    sortie("Modèle : %s. Multiplie les tokens par ton tarif pour connaître le coût ; je n'ai pas de tarif vérifié." % args.modele)
    if nb - deja > 0 and not args.oui:
        if not sys.stdin.isatty() and entree is input:
            raise ErreurBanc("Exécution non interactive : ajoute --oui pour confirmer les appels.")
        if entree("Continuer ? [o/N] ").strip().lower() not in ("o", "oui", "y", "yes"):
            raise ErreurBanc("Arrêt demandé : aucun appel n'a été fait.")
    autorises = {v: nombres_autorises(prompts[v]) for v in variantes}
    resultats, fait = [], 0
    for v in variantes:
        for l in lignes:
            system, messages = construire_appel(prompts[v], l)
            for rep in range(1, args.repetitions + 1):
                k = Cache.cle(args.modele, system, messages, rep)
                texte = cache.get(k)
                if texte is None:
                    texte = client(system, messages, args.modele)
                    cache.put(k, texte)
                    fait += 1
                    if fait % 10 == 0:
                        sortie("  … %d appels faits" % fait)
                r = {"variante": v, "id": l["id"], "rep": rep, "categorie": l["categorie"], "langue_client": l["langue_client"],
                     "message": l["message"], "verite": l["verite"], "reponse_brute": texte}
                r.update(evaluer(l, texte, autorises[v]))
                resultats.append(r)
    if args.juge:
        juge_modele = args.juge_modele or args.modele
        par_id = {l["id"]: l for l in lignes}
        for r in resultats:
            l = par_id[r["id"]]
            catalogue = extraire_catalogue(prompts[r["variante"]])
            system, messages = SYSTEME_JUGE, [{"role": "user", "content": prompt_juge(l, r["reponse_visible"], catalogue)}]
            k = Cache.cle(juge_modele, system, messages, 0)
            texte = cache.get(k)
            if texte is None:
                texte = client(system, messages, juge_modele)
                cache.put(k, texte)
            v = lire_verdict(texte) or {}
            r.update({"juge_langue": v.get("langue_reponse") if v.get("langue_reponse") in ("français", "mixte", "wolof") else "",
                      "juge_respecte": v.get("respecte_langue_client") if isinstance(v.get("respecte_langue_client"), bool) else None,
                      "juge_avance_vente": v.get("avance_la_vente") if isinstance(v.get("avance_la_vente"), bool) else None,
                      "juge_invente": v.get("invente_un_fait") if isinstance(v.get("invente_un_fait"), bool) else None,
                      "juge_propose_sans_indice": v.get("propose_sans_indice") if isinstance(v.get("propose_sans_indice"), bool) else None,
                      "juge_commentaire": str(v.get("commentaire", ""))[:300] if v else "(verdict illisible)"})
    colonnes = ["variante", "id", "rep", "categorie", "langue_client", "message", "verite", "reponse_brute", "reponse_visible", "marqueurs", "mots",
                "langue_reponse", "mots_wolof", "mots_francais", "respecte_langue", "chiffres_hors_catalogue", "mot_attendu_ok", "mot_interdit_ok",
                "marqueur_attendu_ok", "marqueur_interdit_ok", "ok_global", "echecs"]
    if args.juge:
        colonnes += ["juge_langue", "juge_respecte", "juge_avance_vente", "juge_invente", "juge_propose_sans_indice", "juge_commentaire"]
    ecrire_csv(os.path.join(args.sortie, "resultats.csv"), resultats, colonnes)
    rapport = fabriquer_rapport(resultats, variantes, args.repetitions, contamines)
    ecrire_texte(os.path.join(args.sortie, "rapport.txt"), rapport + "\n")
    sortie("")
    sortie(rapport)
    sortie("")
    feuille, cle = fabriquer_feuille(resultats, variantes, args.echantillon, args.graine)
    nom_f, nom_c = ecrire_feuille_et_cle(args.sortie, feuille, cle, sortie)
    sortie("Fichiers dans %s : resultats.csv, rapport.txt, %s (à remplir), %s (NE PAS l'ouvrir avant d'avoir noté)." % (args.sortie, nom_f, nom_c))
    return resultats


EXEMPLES_LANGUE = [  # (texte, étiquettes acceptables, cas limite ?) — réponses réelles de tes logs et exemples de prompt
    ("Pardon, vocal bi ma dégguma ko bu baax 🙏 Mën nga ma ko bind ? Ban modèle ngeen bëgg, ak ban pointure ?", {"wolof"}, False),
    ("Waaw, vocal bi dafa neex waaye transcription bi mësul ko. 🙏 Bind ma ko ci message bu gàtt : ban modèle ngeen bëgg ?", {"wolof"}, False),
    ("Ah d'accord, guiss nako. Amna kay.", {"wolof"}, False),
    ("Waaw, lépp am na ! Ban pointure ngeen bëgg ?", {"wolof"}, False),
    ("Salam ! Vocal bi, ma dégguma ko bu baax 🙏 Pouvez-vous m'écrire le modèle que vous voulez ? On a Moccasin, Samba et Taggat Yaram.", {"mixte"}, False),
    ("Waaw, am na en 44 ! C'est 18 000 FCFA. Vous êtes dans quelle zone pour la livraison ?", {"mixte", "français"}, True),
    ("Je crois avoir compris \"chaussure de sport\" 🙏 C'est la Chaussure de sport Taggat Yaram (20 000 FCFA) que vous voulez ? Répondez-moi par écrit \"waaw\" wala \"déedéet\".", {"français", "mixte"}, True),
    ("Bonjour ! Waaw, on en a. Vous les voulez en quelle pointure ?", {"français"}, False),
    ("D'accord, taille 42 en noir. Je vérifie la disponibilité et je reviens vite.", {"français"}, False),
    ("Bonjour, je vous écoute. Quel modèle vous intéresse ?", {"français"}, False),
    ("Jërejëf ! Revenez quand vous voulez.", {"français"}, False),
    ("Ok", {"indéterminé"}, False),
]


def verifier_langue(sortie=print):
    """Les cas clairs doivent être justes ; les cas limites (où un humain hésiterait) sont signalés, pas comptés."""
    clairs_ok = clairs = 0
    sortie("banc_prompt %s" % VERSION)
    for texte, acceptables, limite in EXEMPLES_LANGUE:
        etiquette, w, f = classer_langue(texte)
        conforme = etiquette in acceptables
        if not limite:
            clairs += 1
            clairs_ok += conforme
        marque = ("cas limite" if limite else ("ok" if conforme else "≠ ATTENDU : %s" % "/".join(sorted(acceptables))))
        sortie("%-12s [%-24s] wolof=%d français=%d | %s" % (etiquette, marque, w, f, texte[:70]))
    sortie("\n%d / %d cas clairs classés comme attendu (les cas limites ne sont pas comptés). Si une ligne te paraît fausse, ajuste MOTS_WOLOF," % (clairs_ok, clairs))
    sortie("MOTS_FRANCAIS ou les SEUILS en haut du script. La détection est une aide : elle ne remplace pas ta relecture.")
    return clairs_ok, clairs


def construire_parseur():
    p = argparse.ArgumentParser(description="Banc d'essai de prompts (voir l'en-tête du fichier).")
    sous = p.add_subparsers(dest="commande")
    l = sous.add_parser("lancer", help="fait répondre Claude avec chaque prompt et note les réponses")
    l.add_argument("--prompts", default=os.path.join(DOSSIER_SCRIPT, "prompts"))
    l.add_argument("--messages", default=os.path.join(DOSSIER_SCRIPT, "messages_test.csv"))
    l.add_argument("--sortie", default=os.path.join(DOSSIER_SCRIPT, "resultats"))
    l.add_argument("--variantes", default="", help="noms de variantes séparés par des virgules (défaut : toutes)")
    l.add_argument("--repetitions", type=int, default=2)
    l.add_argument("--modele", default=MODELE_PAR_DEFAUT)
    l.add_argument("--limite", type=int, default=0, help="ne traite que les N premiers messages")
    l.add_argument("--juge", action="store_true", help="ajoute un second avis automatique (appels supplémentaires)")
    l.add_argument("--inclure-contamines", action="store_true", help="teste AUSSI les messages qui figurent comme exemples dans un prompt (déconseillé : résultats faussés)")
    l.add_argument("--juge-modele", default="")
    l.add_argument("--url-api", default=URL_API, help="adresse de l'API (à changer seulement derrière un proxy ou pour un test local)")
    l.add_argument("--echantillon", type=int, default=36, help="nombre de lignes de la feuille aveugle")
    l.add_argument("--graine", type=int, default=7)
    l.add_argument("--oui", action="store_true", help="ne pas demander confirmation avant les appels")
    n = sous.add_parser("noter", help="résume ta relecture de la feuille aveugle")
    n.add_argument("--messages", default=os.path.join(DOSSIER_SCRIPT, "messages_test.csv"), help="pour comparer ton jugement à la détection automatique")
    n.add_argument("--feuille", default=os.path.join(DOSSIER_SCRIPT, "resultats", "feuille_aveugle.csv"))
    n.add_argument("--cle", default=os.path.join(DOSSIER_SCRIPT, "resultats", "cle_secrete.csv"))
    sous.add_parser("verifier-langue", help="montre la classification de langue sur des exemples")
    return p


def principal(argv=None):
    for flux in (sys.stdout, sys.stderr):
        try:
            flux.reconfigure(encoding="utf-8")
        except Exception:
            pass
    argv = sys.argv[1:] if argv is None else list(argv)
    if not argv or (argv[0].startswith("-") and argv[0] not in ("-h", "--help")):
        argv = ["lancer"] + argv          # sans sous-commande, on lance le banc
    args = construire_parseur().parse_args(argv)
    try:
        if args.commande == "lancer":
            if args.repetitions < 1:
                raise ErreurBanc("--repetitions doit valoir au moins 1.")
            lancer(args)
        elif args.commande == "noter":
            print(noter(args.feuille, args.cle, args.messages))
        else:
            verifier_langue()
    except ErreurBanc as e:
        print("ERREUR : %s" % e)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(principal())
