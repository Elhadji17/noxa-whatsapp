#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Test à l'aveugle du style wolof/français du bot NOXA — HORS PRODUCTION.

Ce script ne touche ni au webhook, ni à Redis, ni à WhatsApp. Il envoie les
mêmes messages de test à plusieurs « bras » (un modèle + une version du prompt),
mélange et anonymise les réponses, puis produit :

  feuille_de_notation.csv   à remplir À L'AVEUGLE : tu ne vois pas quel modèle
                            a écrit quelle réponse.
  cle_secrete.csv           la correspondance option -> bras, plus des contrôles
                            automatiques (chiffres et affirmations hors catalogue).

Une fois la feuille remplie, la commande `score` calcule les moyennes par bras.

USAGE
  # 1) Vérifier la mécanique sans rien dépenser ni appeler d'API :
  python ab_test_wolof.py run --dry-run ^
      --arm "actuel=openai,gpt-4o,prompt_A_actuel.txt" ^
      --arm "nouveau=openai,gpt-4o,prompt_B_nouveau.txt"

  # 2) Le vrai test (clés API dans des variables d'environnement) :
  #    set OPENAI_API_KEY=...   set ANTHROPIC_API_KEY=...   set GEMINI_API_KEY=...
  python ab_test_wolof.py run ^
      --arm "gpt4o_actuel=openai,gpt-4o,prompt_A_actuel.txt" ^
      --arm "gpt4o_nouveau=openai,gpt-4o,prompt_B_nouveau.txt" ^
      --arm "claude=anthropic,claude-sonnet-5,prompt_B_nouveau.txt" ^
      --arm "gemini=google,<ID_MODELE_GEMINI>,prompt_B_nouveau.txt" ^
      --arm "openai_recent=openai,<ID_MODELE_OPENAI_RECENT>,prompt_B_nouveau.txt"

  # 3) Remplis feuille_de_notation.csv (Excel), enregistre-la, puis :
  python ab_test_wolof.py score --sheet resultats\\feuille_de_notation.csv ^
                                --key resultats\\cle_secrete.csv

  Les identifiants de modèles (<ID_...>) sont à copier tels quels depuis la page
  « models » de chaque fournisseur : je ne les devine pas.

LIMITES (à garder en tête en lisant les résultats)
  - Chaque message est envoyé seul, sans historique de conversation.
  - Le prompt inclut un état de commande vide.
  - Les contrôles automatiques sont des heuristiques : ils signalent, ils ne jugent pas.
  - Un test sur ~16 messages donne une tendance, pas une preuve statistique.
  - Les limites de débit (jetons par minute) dépendent de ton palier chez chaque
    fournisseur : le script réessaie tout seul avec des pauses, ce qui peut
    rallonger le test de quelques minutes.
"""

import argparse
import csv
import json
import os
import random
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.request

# ---------------------------------------------------------------------------
# Appels aux fournisseurs (HTTP direct, aucune dépendance à installer)
# ---------------------------------------------------------------------------

class HttpError(RuntimeError):
    """Erreur HTTP d'un fournisseur, avec le code et l'éventuel Retry-After."""

    def __init__(self, code, corps, retry_after=None):
        super().__init__(f"HTTP {code}: {corps}")
        self.code = code
        self.retry_after = retry_after


def http_post_json(url, headers, payload, timeout=90):
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, method="POST",
        headers={"Content-Type": "application/json", **headers},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        corps = e.read().decode("utf-8", errors="replace")[:400]
        ra = e.headers.get("Retry-After") if e.headers else None
        try:
            ra = float(ra) if ra else None
        except ValueError:
            ra = None
        raise HttpError(e.code, corps, ra)
    except urllib.error.URLError as e:
        raise RuntimeError(f"Réseau: {e.reason}")
    except TimeoutError:
        raise RuntimeError("Réseau: délai dépassé")
    except ConnectionError as e:
        # Couvre notamment http.client.RemoteDisconnected ("Remote end closed
        # connection without response") : observé en pratique, non couvert par
        # URLError ni TimeoutError, donc jusqu'ici jamais réessayé.
        raise RuntimeError(f"Réseau: connexion interrompue ({e})")


# Codes qui valent la peine d'être retentés : limite de débit (429), surcharge
# ou panne passagère du fournisseur. Les autres (401 clé refusée, 400 requête
# invalide, 404 modèle inconnu) échouent tout de suite : réessayer ne sert à rien.
CODES_RETRY = {408, 409, 429, 500, 502, 503, 504, 529}

# Un 429 n'est pas toujours une limite de débit passagère : OpenAI renvoie le
# même code HTTP pour un compte à court de crédit ("insufficient_quota"). Dans
# ce cas, attendre et réessayer ne change jamais rien — inutile de faire
# patienter la personne 90 secondes pour un échec garanti.
CODES_QUOTA_EPUISEE = ("insufficient_quota", "RESOURCE_EXHAUSTED")


def appeler_avec_retry(fonction, modele, system, user, essais=5, base=5.0):
    """Réessaie avec une pause croissante (5, 10, 20, 40 s...) ou celle que le
    fournisseur demande via Retry-After. Les limites de jetons par minute des
    paliers OpenAI se libèrent en une minute : d'où une pause de départ de 5 s."""
    for essai in range(1, essais + 1):
        try:
            return fonction(modele, system, user)
        except HttpError as e:
            if any(c in str(e) for c in CODES_QUOTA_EPUISEE):
                # Quota épuisé, pas une limite de débit passagère : réessayer
                # ne peut jamais réussir, autant échouer tout de suite.
                raise
            if e.code not in CODES_RETRY or essai == essais:
                raise
            attente = e.retry_after if e.retry_after else base * (2 ** (essai - 1))
            cause = f"HTTP {e.code}"
        except RuntimeError as e:
            if not str(e).startswith("Réseau") or essai == essais:
                raise
            attente = base * (2 ** (essai - 1))
            cause = str(e)
        attente = min(attente, 90) + random.uniform(0, 1)
        print(f"    ↻ {cause} — nouvelle tentative {essai + 1}/{essais} dans {attente:.0f}s", flush=True)
        time.sleep(attente)


def _cle(nom):
    v = os.environ.get(nom)
    if not v:
        raise RuntimeError(f"Variable d'environnement {nom} absente")
    return v


def call_openai(model, system, user):
    out = http_post_json(
        "https://api.openai.com/v1/chat/completions",
        {"Authorization": f"Bearer {_cle('OPENAI_API_KEY')}"},
        {"model": model, "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user}]},
    )
    return out["choices"][0]["message"]["content"] or ""


def call_anthropic(model, system, user):
    out = http_post_json(
        "https://api.anthropic.com/v1/messages",
        {"x-api-key": _cle("ANTHROPIC_API_KEY"), "anthropic-version": "2023-06-01"},
        {"model": model, "max_tokens": 600, "system": system,
         "messages": [{"role": "user", "content": user}]},
    )
    return "".join(b.get("text", "") for b in out.get("content", []) if b.get("type") == "text")


def call_google(model, system, user):
    out = http_post_json(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        {"x-goog-api-key": _cle("GEMINI_API_KEY")},
        {"system_instruction": {"parts": [{"text": system}]},
         "contents": [{"role": "user", "parts": [{"text": user}]}]},
    )
    parts = out["candidates"][0]["content"]["parts"]
    return "".join(p.get("text", "") for p in parts)


def call_mock(model, system, user):
    # Réponses factices, pour vérifier la mécanique sans appeler d'API.
    return f"Waaw, am na kay. [mock {model}]"


PROVIDERS = {"openai": call_openai, "anthropic": call_anthropic,
             "google": call_google, "mock": call_mock}

# ---------------------------------------------------------------------------
# Contrôles automatiques (heuristiques)
# ---------------------------------------------------------------------------

_MARQUEURS = re.compile(r"\[(?:PHOTO|COMMANDE|PROPOSITION|BESOIN_WOLOF)[^\]]*\]", re.I)
_NUM = re.compile(r"\d+(?:[ \u00a0\u202f.,]\d{3})*")
# Mots qu'un catalogue ne peut pas soutenir : le bot ne devrait jamais les
# affirmer de sa propre initiative.
_MOTS_INTERDITS = re.compile(
    r"\b(original|originaux|authentique|authentiques|promotion|promotions|promo|"
    r"reduction|reductions|remise|remises|rabais|gratuit|gratuite|garantie|garanti|"
    r"garantis|solde|soldes)\b"
)
_POURCENT = re.compile(r"\d+\s*%")


def sans_accents(s):
    return "".join(c for c in unicodedata.normalize("NFD", s.lower())
                   if unicodedata.category(c) != "Mn")


def nettoyer(reponse):
    """Ce que le client verrait réellement : sans les marqueurs internes."""
    return _MARQUEURS.sub("", reponse).strip()


def extraire_nombres(texte):
    out = set()
    for m in _NUM.finditer(texte):
        chiffres = re.sub(r"\D", "", m.group(0))
        if chiffres:
            out.add(int(chiffres))
    return out


def nombres_autorises(catalogue, message_client):
    prix = [int(re.sub(r"\D", "", p["price"])) for p in catalogue.get("products", [])]
    livraisons = [int(z["price"]) for z in catalogue.get("deliveryZones", [])]
    ok = set(prix) | set(livraisons)
    ok |= {p + d for p in prix for d in livraisons}          # totaux annoncés
    for p in catalogue.get("products", []):
        for t in p.get("sizes", []):
            ok |= extraire_nombres(str(t))
    ok |= extraire_nombres(str(catalogue.get("wavePaymentNumber", "")))
    ok |= extraire_nombres(message_client)                   # le bot peut reprendre ce qu'a dit le client
    return ok


_HEURE = re.compile(r"\b\d{1,2}\s*h(?:\s?\d{2})?\b", re.I)


def controles(reponse, catalogue, message_client):
    texte = nettoyer(reponse)
    ok = nombres_autorises(catalogue, message_client)
    # Les heures ("20h30") sont retirées avant l'extraction : elles ne sont ni des
    # prix ni des tailles, et laisser leurs chiffres dans la liste autorisée
    # laisserait passer une remise de "20 %".
    sans_heures = _HEURE.sub(" ", texte)
    # Les très petits nombres (1 à 5) sont ignorés : "2 couleurs", "1 modèle"...
    # LIMITE : les totaux autorisés sont toutes les sommes prix + livraison ; le
    # contrôle ne peut pas vérifier que le bon prix va avec la bonne livraison.
    hors = sorted(n for n in extraire_nombres(sans_heures) if n > 5 and n not in ok)
    mots = sorted(set(_MOTS_INTERDITS.findall(sans_accents(texte))))
    if _POURCENT.search(texte):
        mots.append("pourcentage")
    return hors, mots

# ---------------------------------------------------------------------------
# Entrées / sorties CSV (détection ; ou , pour Excel français)
# ---------------------------------------------------------------------------

def lire_csv(chemin):
    with open(chemin, "rb") as f:
        brut = f.read()
    # UTF-16 (Excel « Enregistrer sous → Texte Unicode ») : se reconnaît à son
    # marqueur d'ordre des octets en tête de fichier. cp1252 ne lèverait PAS
    # d'erreur sur ces octets (il accepte n'importe quelle valeur d'un octet),
    # donc sans ce test explicite le fichier se lirait en charabia silencieux
    # plutôt que d'échouer proprement.
    if brut[:2] in (b"\xff\xfe", b"\xfe\xff"):
        contenu = brut.decode("utf-16")
    else:
        try:
            contenu = brut.decode("utf-8-sig")
        except UnicodeDecodeError:
            # Excel français, « CSV (séparateur : point-virgule) », enregistre en
            # Windows-1252 et non en UTF-8. Seuls les identifiants et les notes
            # sont lus par `score` : d'éventuels caractères wolof abîmés (ŋ, ñ...)
            # dans la colonne de réponse sont sans conséquence.
            contenu = brut.decode("cp1252", errors="replace")
    lignes = contenu.splitlines()
    # Excel ajoute parfois une ligne "sep=;" ou "sep=," tout en haut du fichier
    # (un indice de séparateur pour sa propre réouverture) : sans ce retrait,
    # elle serait lue comme l'en-tête et "id" n'existerait plus comme colonne.
    if lignes and re.fullmatch(r"sep=.", lignes[0].strip()):
        contenu = "\n".join(lignes[1:])
    premiere = contenu.splitlines()[0] if contenu else ""
    comptes = {";": premiere.count(";"), ",": premiere.count(","), "\t": premiere.count("\t")}
    delim = max(comptes, key=comptes.get)
    if comptes[delim] == 0:
        # Aucun séparateur reconnu (ni ';', ni ',', ni tabulation) : mieux vaut
        # échouer clairement ici plutôt que de retourner une seule colonne
        # géante et faire échouer `score` plus loin avec un KeyError opaque.
        # Cas vu en pratique : Excel « Enregistrer sous → Texte (séparateur :
        # tabulation) », qui donne un .csv ou .txt tabulé malgré son extension.
        raise SystemExit(
            f"Impossible de reconnaître le séparateur dans {chemin!r} "
            f"(aucun ';', ',' ni tabulation dans l'en-tête). Dans Excel, "
            f"réenregistre avec « Enregistrer sous → CSV UTF-8 » ou "
            f"« CSV (séparateur : point-virgule) »."
        )
    return list(csv.DictReader(contenu.splitlines(), delimiter=delim))


def ecrire_csv(chemin, colonnes, lignes):
    with open(chemin, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=colonnes, delimiter=";", quoting=csv.QUOTE_ALL)
        w.writeheader()
        for l in lignes:
            w.writerow(l)

# ---------------------------------------------------------------------------
# Commande `run`
# ---------------------------------------------------------------------------

def parse_arm(texte):
    try:
        label, reste = texte.split("=", 1)
        provider, modele, fichier = [x.strip() for x in reste.split(",", 2)]
    except ValueError:
        raise SystemExit(f"--arm invalide : {texte!r}. Format : label=provider,modele,fichier_prompt")
    if provider not in PROVIDERS:
        raise SystemExit(f"Fournisseur inconnu : {provider!r} (attendu : openai, anthropic, google)")
    return {"label": label.strip(), "provider": provider, "modele": modele, "fichier": fichier}


def run(args):
    messages = lire_csv(args.messages)
    with open(args.catalog, encoding="utf-8") as f:
        catalogue = json.load(f)
    arms = [parse_arm(a) for a in args.arm]
    if not arms:
        raise SystemExit("Donne au moins un --arm.")
    for a in arms:
        with open(a["fichier"], encoding="utf-8") as f:
            a["prompt"] = f.read()

    os.makedirs(args.out_dir, exist_ok=True)
    resultats = []   # une ligne par (message, bras)
    total = len(messages) * len(arms)
    n = 0
    for m in messages:
        for a in arms:
            n += 1
            print(f"[{n}/{total}] {m['id']} <- {a['label']}", flush=True)
            prov = "mock" if args.dry_run else a["provider"]
            debut = time.time()
            erreur, brute = "", ""
            try:
                sys_prompt = a["prompt"]
                if (m.get("context") or "").strip():
                    sys_prompt += "\n\n[CONTEXTE DE LA CONVERSATION]\n" + m["context"].strip()
                brute = appeler_avec_retry(PROVIDERS[prov], a["modele"], sys_prompt, m["message"], essais=args.retries)
            except Exception as e:  # on garde la trace et on continue
                erreur = str(e)[:300]
            latence = int((time.time() - debut) * 1000)
            hors, mots = controles(brute, catalogue, m["message"]) if brute else ([], [])
            resultats.append({
                "id": m["id"], "message": m["message"], "bras": a["label"],
                "provider": a["provider"], "modele": a["modele"], "prompt": os.path.basename(a["fichier"]),
                "latence_ms": latence, "erreur": erreur,
                "chiffres_hors_catalogue": ",".join(map(str, hors)),
                "mots_non_catalogue": ",".join(mots),
                "reponse_brute": brute,
            })
            time.sleep(args.sleep if not args.dry_run else 0)

        # Référence humaine (optionnelle) : sert d'étalon pour l'oreille du noteur.
        ref = (m.get("reference") or "").strip()
        if ref:
            hors, mots = controles(ref, catalogue, m["message"])
            resultats.append({
                "id": m["id"], "message": m["message"], "bras": "humain",
                "provider": "-", "modele": "-", "prompt": "-", "latence_ms": 0, "erreur": "",
                "chiffres_hors_catalogue": ",".join(map(str, hors)),
                "mots_non_catalogue": ",".join(mots), "reponse_brute": ref,
            })

    # --- Anonymisation : ordre mélangé, reproductible avec --seed -----------
    feuille, cle = [], []
    for m in messages:
        lignes = [r for r in resultats if r["id"] == m["id"]]
        rng = random.Random(f"{args.seed}-{m['id']}")
        rng.shuffle(lignes)
        for i, r in enumerate(lignes):
            option = chr(ord("A") + i)
            vue = nettoyer(r["reponse_brute"]) if r["reponse_brute"] else "[ERREUR — pas de réponse, ne pas noter]"
            feuille.append({
                "id": r["id"], "message_client": r["message"], "option": option, "reponse": vue,
                "note_naturel_urbain_1a5": "", "note_exactitude_1a5": "",
                "note_code_switching_1a5": "", "dangereux_oui_non": "", "commentaire": "",
            })
            cle.append({**r, "option": option})

    ecrire_csv(os.path.join(args.out_dir, "feuille_de_notation.csv"),
               ["id", "message_client", "option", "reponse", "note_naturel_urbain_1a5",
                "note_exactitude_1a5", "note_code_switching_1a5", "dangereux_oui_non", "commentaire"],
               feuille)
    ecrire_csv(os.path.join(args.out_dir, "cle_secrete.csv"),
               ["id", "option", "bras", "provider", "modele", "prompt", "latence_ms", "erreur",
                "chiffres_hors_catalogue", "mots_non_catalogue", "message", "reponse_brute"],
               cle)

    erreurs = sum(1 for r in resultats if r["erreur"])
    print(f"\nTerminé : {len(resultats)} réponses, {erreurs} erreur(s).")
    print(f"  À remplir  : {os.path.join(args.out_dir, 'feuille_de_notation.csv')}")
    print(f"  Ne PAS ouvrir avant d'avoir noté : {os.path.join(args.out_dir, 'cle_secrete.csv')}")
    if erreurs:
        from collections import Counter
        causes = Counter(re.sub(r"\s+", " ", r["erreur"])[:160] for r in resultats if r["erreur"])
        print("\nCauses des erreurs (regroupées ; le bras concerné n'est pas indiqué, pour ne pas fausser la notation) :")
        for msg, nb in causes.most_common():
            print(f"  {nb}x {msg}")
        if any("insufficient_quota" in r["erreur"] or "RESOURCE_EXHAUSTED" in r["erreur"] for r in resultats):
            print("\n⚠️  Au moins une erreur signale un compte à court de crédit (pas une simple limite de")
            print("   débit). Si cette clé sert AUSSI en production, vérifie la facturation du fournisseur")
            print("   concerné dès maintenant : la production pourrait être affectée en ce moment même.")
        print("Une feuille avec des trous n'est pas exploitable : corrige la cause puis relance dans un NOUVEAU dossier (--out-dir).")

# ---------------------------------------------------------------------------
# Commande `score`
# ---------------------------------------------------------------------------

def _note(v):
    v = (v or "").strip().replace(",", ".")
    try:
        x = float(v)
        return x if 1 <= x <= 5 else None
    except ValueError:
        return None


def _oui(v):
    return (v or "").strip().lower() in {"oui", "o", "yes", "y", "1", "vrai"}


def score(args):
    feuille = lire_csv(args.sheet)
    cle = {(r["id"], r["option"]): r for r in lire_csv(args.key)}
    par_bras = {}
    for l in feuille:
        k = cle.get((l["id"], l["option"]))
        if not k:
            continue
        b = par_bras.setdefault(k["bras"], {
            "n": 0, "nat": [], "exa": [], "cs": [], "danger": 0, "notes": 0,
            "chiffres": 0, "mots": 0, "erreurs": 0, "lat": []})
        b["n"] += 1
        if k["erreur"]:
            b["erreurs"] += 1
        nat, exa, cs = (_note(l["note_naturel_urbain_1a5"]), _note(l["note_exactitude_1a5"]),
                        _note(l["note_code_switching_1a5"]))
        if any(x is not None for x in (nat, exa, cs)) or l["dangereux_oui_non"].strip():
            b["notes"] += 1
        for lst, x in ((b["nat"], nat), (b["exa"], exa), (b["cs"], cs)):
            if x is not None:
                lst.append(x)
        if _oui(l["dangereux_oui_non"]):
            b["danger"] += 1
        if k["chiffres_hors_catalogue"]:
            b["chiffres"] += 1
        if k["mots_non_catalogue"]:
            b["mots"] += 1
        if k["latence_ms"] not in ("", "0"):
            try:
                b["lat"].append(int(k["latence_ms"]))
            except ValueError:
                pass

    def moy(l):
        return f"{sum(l)/len(l):.2f}" if l else "  - "

    print(f"\n{'bras':<18}{'notés':>6}{'naturel':>9}{'exact.':>8}{'c-switch':>9}"
          f"{'danger':>8}{'chiffres?':>10}{'mots?':>7}{'erreurs':>8}{'lat.ms':>8}")
    lignes_csv = []
    for nom, b in sorted(par_bras.items()):
        lat = f"{sum(b['lat'])/len(b['lat']):.0f}" if b["lat"] else "-"
        print(f"{nom:<18}{b['notes']:>6}{moy(b['nat']):>9}{moy(b['exa']):>8}{moy(b['cs']):>9}"
              f"{b['danger']:>8}{b['chiffres']:>10}{b['mots']:>7}{b['erreurs']:>8}{lat:>8}")
        lignes_csv.append({"bras": nom, "notes": b["notes"], "naturel_moy": moy(b["nat"]),
                           "exactitude_moy": moy(b["exa"]), "code_switching_moy": moy(b["cs"]),
                           "dangereux": b["danger"], "chiffres_hors_catalogue": b["chiffres"],
                           "mots_non_catalogue": b["mots"], "erreurs": b["erreurs"], "latence_moy_ms": lat})
    sortie = os.path.join(os.path.dirname(args.sheet) or ".", "resultats.csv")
    ecrire_csv(sortie, list(lignes_csv[0].keys()) if lignes_csv else ["bras"], lignes_csv)
    print(f"\nEnregistré : {sortie}")
    print("Lecture : 'danger' = réponses que TU as jugées trompeuses ; 'chiffres?' et 'mots?' = "
          "réponses signalées par les contrôles automatiques (heuristiques, à recouper avec ton avis).")
    print("Avec ~16 messages, une différence de 0,3 point est du bruit : cherche les écarts nets.")


def main():
    p = argparse.ArgumentParser(description="Test à l'aveugle du style wolof/français (hors production)")
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="Interroger les modèles et fabriquer la feuille de notation")
    r.add_argument("--messages", default="messages_test.csv")
    r.add_argument("--catalog", default="catalog_adshoes.json")
    r.add_argument("--arm", action="append", default=[], help="label=provider,modele,fichier_prompt")
    r.add_argument("--out-dir", default="resultats")
    r.add_argument("--seed", type=int, default=42)
    r.add_argument("--sleep", type=float, default=1.5, help="pause entre deux appels (s)")
    r.add_argument("--retries", type=int, default=5, help="nombre d'essais par appel (429/5xx)")
    r.add_argument("--dry-run", action="store_true", help="aucun appel réseau : réponses factices")
    r.set_defaults(func=run)

    s = sub.add_parser("score", help="Calculer les moyennes par bras une fois la feuille remplie")
    s.add_argument("--sheet", required=True)
    s.add_argument("--key", required=True)
    s.set_defaults(func=score)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
