#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Script de fine-tuning à coller dans des cellules Colab (découpé par
"# ===== CELLULE N =====" — une cellule Colab par section).

Base : abdouaziiz/wav2vec2-xls-r-300m-wolof-lm (300M paramètres, licence MIT),
le même modèle que le service XLS-R de production NOXA.

SÉCURITÉS INTÉGRÉES, PAS DE CONTOURNEMENT POSSIBLE :
  1. Vérification du vocabulaire AVANT tout entraînement — arrêt complet
     (pas un avertissement) si un texte contient un caractère hors du
     vocabulaire confirmé du modèle. Le vocabulaire confirmé (retrouvé sur
     le vrai vocab.json du modèle) ne contient ni chiffres, ni majuscules
     utilisables (do_lower_case=false), ni "z", ni apostrophe.
  2. Sauvegarde sur Google Drive à intervalle court (pas seulement à la
     fin) — une coupure Colab (déconnexion à 90 min d'inactivité, plafond
     dur à 12h) ne fait perdre que les dernières minutes, pas tout.
  3. Reprise automatique : relancer ce script après une coupure retrouve
     le dernier point de sauvegarde sur Drive, sans rien perdre d'autre.

CE QUI N'A JAMAIS ÉTÉ TESTÉ DE BOUT EN BOUT (pas de GPU ni réseau pour le
faire) : le chargement réel du modèle, la boucle d'entraînement elle-même,
le montage Drive. Lance la Cellule 1 seule d'abord, regarde qu'elle se
termine sans erreur, AVANT de lancer la suite et de partir.

CE QUI A ÉTÉ TESTÉ (logique pure, sans GPU) : la vérification de
vocabulaire, la lecture du manifeste, la correspondance des chemins audio.
Voir le fichier de tests livré séparément pour la preuve.
"""

# ===== CELLULE 1 : vérifications AVANT toute installation lourde =====
# Lance cette cellule seule en premier. Si elle échoue, rien d'autre n'a
# encore été installé/téléchargé — aucun temps perdu.

import re
import os
import csv
from collections import Counter

VOCAB_CONFIRME = set("aàãbcçdeéëfghijklmnñoópqrstuvwxy")  # vérifié sur le vrai vocab.json
CHARS_A_SUPPRIMER = re.compile(r'["?.!\-;:,]')  # régex exacte de l'auteur du modèle


def normaliser_texte(texte):
    texte = str(texte).lower()
    texte = CHARS_A_SUPPRIMER.sub("", texte)
    return texte


def verifier_vocabulaire_ou_arreter(textes):
    """Lève une erreur bloquante (pas juste un avertissement) si un texte
    contient un caractère hors du vocabulaire confirmé du modèle. Mieux
    vaut un arrêt net maintenant qu'un entraînement sur des [UNK] pendant
    des heures sans surveillance."""
    compte_hors_vocab = Counter()
    textes_problematiques = []
    for texte in textes:
        normalise = normaliser_texte(texte)
        hors_vocab = {c for c in normalise if c != " " and c not in VOCAB_CONFIRME}
        if hors_vocab:
            compte_hors_vocab.update(hors_vocab)
            textes_problematiques.append((texte, hors_vocab))

    if textes_problematiques:
        message = [f"ARRÊT : {len(textes_problematiques)} texte(s) sur {len(textes)} contiennent",
                    "un caractère hors du vocabulaire du modèle. Corrige-les avant de continuer :"]
        for car, n in compte_hors_vocab.most_common():
            message.append(f"  {car!r} ({n} fois)")
        message.append("Exemples :")
        for texte, hv in textes_problematiques[:5]:
            message.append(f"  {texte!r} -> {sorted(hv)}")
        raise SystemExit("\n".join(message))
    print(f"✅ Vocabulaire vérifié sur {len(textes)} texte(s) : rien à corriger.")


def filtrer_exemples_compatibles(exemples):
    """Alternative DÉLIBÉRÉE au blocage strict ci-dessus : sépare les
    exemples utilisables de ceux à corriger, plutôt que de tout arrêter.
    À utiliser sciemment (ex: s'entraîner ce soir sur un sous-ensemble propre,
    corriger le reste plus tard) -- jamais comme comportement par défaut
    silencieux, pour ne pas perdre de vue que des exemples ont été écartés."""
    propres, rejetes = [], []
    for e in exemples:
        normalise = normaliser_texte(e["reference"])
        hors_vocab = {c for c in normalise if c != " " and c not in VOCAB_CONFIRME}
        (rejetes if hors_vocab else propres).append(e)
    print(f"{len(propres)} exemple(s) compatible(s), {len(rejetes)} écarté(s) (à corriger plus tard) "
          f"sur {len(exemples)} au total.")
    if len(propres) < 10:
        print("⚠️  Moins de 10 exemples compatibles -- probablement trop peu pour un entraînement "
              "qui apprenne quoi que ce soit d'utile. Mieux vaut corriger le texte avant de continuer.")
    return propres, rejetes


def nom_fichier_cross_plateforme(chemin):
    """os.path.basename() dépend du système sur lequel le script TOURNE, pas
    du système qui a PRODUIT le chemin. Colab tourne sous Linux : sur un
    chemin Windows ("C:\\Users\\...\\x.ogg"), os.path.basename() de Linux ne
    reconnaît pas '\\' comme séparateur et renvoie la chaîne entière -- bug
    réel rencontré en testant cette fonction, qui aurait fait échouer
    TOUTES les correspondances de fichiers en silence sur un vrai Colab.
    On découpe donc manuellement sur '/' ET '\\', peu importe l'OS courant."""
    return re.split(r"[\\/]", str(chemin))[-1]


def lire_manifeste_pour_entrainement(chemin_manifeste, dossier_audio_colab):
    """Lit un manifeste au format chemin_audio;reference;... (même format que
    benchmark_corpus_xlsr.py / extraire_grille_noxa.py) et RECONSTRUIT le
    chemin audio pour l'environnement Colab : seul le NOM DE FICHIER de la
    colonne chemin_audio est réutilisé, jamais le chemin complet d'origine
    (souvent un chemin Windows du type C:\\Users\\... qui n'existe pas ici).
    Même piège que celui déjà rencontré et corrigé dans extraire_grille_noxa.py
    avec les chemins relatifs/absolus — ici la bonne approche est de ne
    garder QUE le nom de fichier, puis de le rejoindre au dossier Colab."""
    with open(chemin_manifeste, "rb") as f:
        brut = f.read()
    try:
        contenu = brut.decode("utf-8-sig")
    except UnicodeDecodeError:
        contenu = brut.decode("cp1252", errors="replace")
    lignes_brutes = contenu.splitlines()
    premiere = lignes_brutes[0] if lignes_brutes else ""
    delim = ";" if premiere.count(";") >= premiere.count(",") else ","
    lignes = list(csv.DictReader(lignes_brutes, delimiter=delim))

    exemples = []
    manquants = []
    for l in lignes:
        nom_fichier = nom_fichier_cross_plateforme(l["chemin_audio"])
        chemin_colab = os.path.join(dossier_audio_colab, nom_fichier)
        reference = l["reference"].strip()
        if not reference:
            continue
        if not os.path.isfile(chemin_colab):
            manquants.append(nom_fichier)
            continue
        exemples.append({"chemin_audio": chemin_colab, "reference": reference})

    if manquants:
        print(f"⚠️  {len(manquants)} fichier(s) référencé(s) dans le manifeste mais absents de "
              f"{dossier_audio_colab!r} (vérifie que tu as bien tout uploadé sur Drive) :")
        for m in manquants[:10]:
            print(f"    {m}")
        if len(manquants) > 10:
            print(f"    ... et {len(manquants) - 10} de plus")
    print(f"{len(exemples)} exemple(s) utilisable(s) pour l'entraînement.")
    return exemples


# ===== CELLULE 2 : montage Drive + installation (NON TESTÉ ICI — pas de Colab réel) =====
CELLULE_2 = r'''
from google.colab import drive
drive.mount('/content/drive')

!pip install -q transformers datasets accelerate librosa soundfile jiwer

DOSSIER_SORTIE_DRIVE = "/content/drive/MyDrive/noxa_finetuning_xlsr"
DOSSIER_AUDIO_COLAB = "/content/drive/MyDrive/noxa_finetuning_xlsr/audio"
CHEMIN_MANIFESTE = "/content/drive/MyDrive/noxa_finetuning_xlsr/manifeste.csv"
# Place le manifeste ET le dossier audio correspondant dans Drive AVANT de lancer
# cette cellule — upload manuel depuis l'interface Drive, c'est le plus simple.

import os
os.makedirs(DOSSIER_SORTIE_DRIVE, exist_ok=True)
'''

# ===== CELLULE 3 : vérification du vocabulaire SUR LES VRAIES DONNÉES (bloquant) =====
CELLULE_3 = r'''
exemples = lire_manifeste_pour_entrainement(CHEMIN_MANIFESTE, DOSSIER_AUDIO_COLAB)
verifier_vocabulaire_ou_arreter([e["reference"] for e in exemples])
# Si cette cellule lève une erreur : corrige le manifeste localement, ré-upload,
# relance CETTE cellule seule avant de continuer. Ne saute jamais cette étape.
'''

# ===== CELLULE 3bis : ALTERNATIVE EXPLICITE -- entraîner ce soir sur le sous-
# ensemble déjà propre, corriger le reste plus tard. À utiliser À LA PLACE de
# la Cellule 3 (pas en plus), seulement si tu choisis sciemment cette option. =====
CELLULE_3BIS = r'''
exemples = lire_manifeste_pour_entrainement(CHEMIN_MANIFESTE, DOSSIER_AUDIO_COLAB)
exemples, exemples_ecartes = filtrer_exemples_compatibles(exemples)
# 'exemples_ecartes' reste disponible pour revoir précisément ce qui a été
# laissé de côté, si tu veux l'inspecter avant de continuer :
for e in exemples_ecartes:
    print("  écarté :", repr(e["reference"]))
'''

# ===== CELLULE 4 : préparation du modèle et des données (NON TESTÉ — pas de GPU ici) =====
CELLULE_4 = r'''
import torch
import librosa
import numpy as np
from datasets import Dataset
from transformers import Wav2Vec2ForCTC, Wav2Vec2Processor

NOM_MODELE = "abdouaziiz/wav2vec2-xls-r-300m-wolof-lm"
processor = Wav2Vec2Processor.from_pretrained(NOM_MODELE)
model = Wav2Vec2ForCTC.from_pretrained(NOM_MODELE)

# Geler l'extracteur de caractéristiques : on adapte le modèle à un
# vocabulaire plus étroit, on ne réapprend pas la phonétique wolof de zéro.
# Avec aussi peu de données, dégeler tout risquerait un vrai sur-apprentissage.
model.freeze_feature_encoder()

def charger_audio(exemple):
    signal, _ = librosa.load(exemple["chemin_audio"], sr=16000, mono=True)
    exemple["signal"] = signal
    exemple["texte_normalise"] = normaliser_texte(exemple["reference"])
    return exemple

dataset = Dataset.from_list(exemples).map(charger_audio)

# Découpage train/validation simple (90/10) pour détecter un sur-apprentissage
# en cours de route -- avec un jeu aussi petit, ce n'est qu'indicatif, pas
# une vraie garantie statistique.
dataset = dataset.train_test_split(test_size=0.1, seed=42)

def preparer_exemple(batch):
    batch["input_values"] = processor(batch["signal"], sampling_rate=16000).input_values[0]
    # as_target_processor() a été retiré des versions récentes de transformers
    # (confirmé : AttributeError, pas juste un avertissement de dépréciation).
    # Remplacement officiel : appeler le tokenizer directement, sans passer
    # par le processor complet pour le texte.
    batch["labels"] = processor.tokenizer(batch["texte_normalise"]).input_ids
    return batch

dataset = dataset.map(preparer_exemple, remove_columns=["signal", "chemin_audio", "reference", "texte_normalise"])
'''

# ===== CELLULE 5 : collator CTC standard (NON TESTÉ — recette HuggingFace classique) =====
CELLULE_5 = r'''
from dataclasses import dataclass
from typing import Dict, List, Union

@dataclass
class DataCollatorCTCAvecPadding:
    processor: Wav2Vec2Processor
    padding: Union[bool, str] = True

    def __call__(self, features: List[Dict[str, Union[List[int], torch.Tensor]]]) -> Dict[str, torch.Tensor]:
        input_features = [{"input_values": f["input_values"]} for f in features]
        label_features = [{"input_ids": f["labels"]} for f in features]

        batch = self.processor.pad(input_features, padding=self.padding, return_tensors="pt")
        # Même correctif que dans preparer_exemple : passer par le tokenizer
        # directement plutôt que par as_target_processor(), retiré des
        # versions récentes de transformers.
        labels_batch = self.processor.tokenizer.pad(label_features, padding=self.padding, return_tensors="pt")

        labels = labels_batch["input_ids"].masked_fill(labels_batch.attention_mask.ne(1), -100)
        batch["labels"] = labels
        return batch

data_collator = DataCollatorCTCAvecPadding(processor=processor)
'''

# ===== CELLULE 6 : entraînement avec sauvegarde Drive + reprise automatique =====
CELLULE_6 = r'''
from transformers import TrainingArguments, Trainer
import os

checkpoints_existants = [d for d in os.listdir(DOSSIER_SORTIE_DRIVE) if d.startswith("checkpoint-")] \
    if os.path.isdir(DOSSIER_SORTIE_DRIVE) else []
reprendre_depuis = max(checkpoints_existants, key=lambda d: int(d.split("-")[1])) if checkpoints_existants else None
if reprendre_depuis:
    print(f"Point de reprise trouvé : {reprendre_depuis} -- reprise automatique, rien n'est reparti de zéro.")
else:
    print("Aucun point de reprise trouvé -- premier lancement.")

training_args = TrainingArguments(
    output_dir=DOSSIER_SORTIE_DRIVE,
    # 'group_by_length' retiré : rejeté par la version de transformers de ce
    # Colab (TypeError), sans que j'aie pu confirmer pourquoi -- la doc
    # officielle le liste pourtant encore. C'est une optimisation de
    # regroupement par longueur (réduit le remplissage), pas une nécessité :
    # l'entraînement fonctionne normalement sans, juste un peu moins
    # efficacement sur de gros volumes. Avec 33 exemples, l'impact est nul.
    per_device_train_batch_size=4,
    per_device_eval_batch_size=4,
    eval_strategy="steps",
    num_train_epochs=30,          # jeu de données petit -> plus d'epochs, mais surveille le sur-apprentissage (eval_loss)
    save_steps=20,                 # sauvegarde fréquente -- 90 min d'inactivité ne doivent pas coûter cher
    eval_steps=20,
    logging_steps=10,
    save_total_limit=3,            # ne garde que les 3 derniers points, pour ne pas saturer Drive
    learning_rate=3e-5,            # prudent : on adapte, on ne réapprend pas depuis zéro
    warmup_steps=50,
    fp16=True,                     # le T4 gratuit le supporte
)

trainer = Trainer(
    model=model,
    args=training_args,
    train_dataset=dataset["train"],
    eval_dataset=dataset["test"],
    data_collator=data_collator,
    # 'tokenizer=' retiré en transformers v5, remplacé par 'processing_class='
    # (confirmé : même famille de changement que as_target_processor plus
    # haut -- cette version de transformers semble être une v5).
    processing_class=processor.feature_extractor,
)

chemin_reprise = os.path.join(DOSSIER_SORTIE_DRIVE, reprendre_depuis) if reprendre_depuis else None
trainer.train(resume_from_checkpoint=chemin_reprise)
'''

if __name__ == "__main__":
    print(__doc__)
    print("\nCe fichier n'est pas fait pour être exécuté tel quel : copie chaque")
    print("section CELLULE_N dans une cellule Colab séparée, dans l'ordre.")
    print("La Cellule 1 (fonctions pures, ci-dessus) est déjà testée sans GPU --")
    print("colle-la telle quelle, en premier, avant tout le reste.")
