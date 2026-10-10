#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Essai de déploiement de Whosper (CAYTU/whosper-large-v2) sur Modal, pour MESURER
la latence à chaud, le démarrage à froid et la fidélité après conversion.

Ce que le script fait
  1. convertir : Whosper est un ADAPTATEUR LoRA posé sur Whisper large-v2 (la fiche du
     modèle est étiquetée PEFT). On le fusionne dans le modèle de base, puis on convertit
     le résultat au format CTranslate2 (fp16) pour faster-whisper, et on garde aussi la
     version fusionnée au format Transformers comme voie de secours.
  2. Transcripteur : classe GPU (T4) qui charge le modèle une fois par conteneur et expose
     une méthode `transcrire`. Elle indique dans sa réponse si le conteneur était froid.
  3. verifier : test de fumée (1 seconde de silence) pour attraper tout de suite un
     problème de bibliothèques CUDA, avant de lancer la vraie mesure.

Ce qui n'a PAS pu être testé ici (pas de réseau, pas de GPU, pas de Modal) : tout ce qui
touche réellement à Modal, à CUDA, à la fusion et à la conversion. Les parties de logique
(décodage, structure des réponses, enchaînement des étapes, détection adaptateur/modèle
complet, commandes lancées) ont été testées avec des doublures. Attends-toi à devoir
corriger une ou deux erreurs de version au premier lancement : colle-les-moi telles quelles.

Pièges connus
  - La version de cuDNN doit correspondre à celle de CTranslate2. L'image utilise une
    image CUDA officielle avec cuDNN ; si faster-whisper plante sur une bibliothèque
    'libcudnn', redéploie avec la voie de secours :  set WHOSPER_BACKEND=hf
  - Les variables d'environnement lues ici sont celles de TON PC au moment du déploiement ;
    elles sont recopiées dans l'image (voir .env plus bas). Pour changer le délai de veille
    ou la voie, redéploie.
  - Transformers est volontairement limité à la version 4 : l'outil de conversion de
    CTranslate2 n'est pas garanti avec la version 5.

Ordre des commandes (invite de commandes Windows)
  pip install modal
  modal setup                                  (une seule fois : ouvre le navigateur)
  modal run modal_whosper.py::main             (fusion + conversion, une fois, 10 à 20 min)
  modal deploy modal_whosper.py                (déploie la classe)
  modal run modal_whosper.py::verifier         (test de fumée)
  (le nom après :: est obligatoire : le fichier contient deux points d'entrée)
  ... puis essai_whosper.py (calibrer, chaud, froid)
  modal app stop noxa-whosper-essai            (à la fin, pour tout arrêter)
Le volume 'noxa-whosper-modeles' garde environ 6 Go ; supprime-le si tu abandonnes :
  modal volume delete noxa-whosper-modeles
"""

import os

import modal

APP_NAME = "noxa-whosper-essai"
VOLUME_NAME = "noxa-whosper-modeles"
ADAPTATEUR = os.environ.get("WHOSPER_ADAPTATEUR", "CAYTU/whosper-large-v2")
BASE_PAR_DEFAUT = "openai/whisper-large-v2"
CHEMIN_FUSION = "/modeles/whosper-fusion-fp16"   # voie de secours (Transformers)
CHEMIN_CT2 = "/modeles/whosper-ct2-fp16"          # voie rapide (faster-whisper)
VEILLE_S = int(os.environ.get("WHOSPER_VEILLE_S", "60"))   # secondes avant arrêt d'un conteneur inactif
BACKEND = os.environ.get("WHOSPER_BACKEND", "ct2")          # "ct2" (rapide) ou "hf" (secours)

image = (
    modal.Image.from_registry("nvidia/cuda:12.4.1-cudnn-runtime-ubuntu22.04", add_python="3.11")
    .apt_install("ffmpeg")
    .pip_install(
        "torch", "transformers>=4.44,<5", "peft", "accelerate", "sentencepiece", "numpy",
        "huggingface_hub", "ctranslate2>=4.5", "faster-whisper>=1.1",
    )
    .env({"WHOSPER_BACKEND": BACKEND})
)
app = modal.App(APP_NAME, image=image)
volume = modal.Volume.from_name(VOLUME_NAME, create_if_missing=True)


def decoder_audio(octets):
    """Octets d'un fichier audio quelconque (ogg/opus de WhatsApp compris) -> signal mono 16 kHz en float32."""
    import subprocess

    import numpy as np

    r = subprocess.run(
        ["ffmpeg", "-nostdin", "-loglevel", "error", "-i", "pipe:0", "-f", "s16le", "-ac", "1", "-ar", "16000", "pipe:1"],
        input=octets, capture_output=True)
    if r.returncode != 0 or not r.stdout:
        raise RuntimeError("ffmpeg n'a pas pu lire l'audio : " + r.stderr.decode("utf-8", "replace")[:300])
    return np.frombuffer(r.stdout, dtype=np.int16).astype(np.float32) / 32768.0


@app.function(volumes={"/modeles": volume}, cpu=4, memory=32768, timeout=3600)
def convertir(adaptateur: str = ADAPTATEUR, force: bool = False):
    """Fusionne l'adaptateur LoRA dans Whisper large-v2, convertit en CTranslate2 fp16, sauvegarde les deux."""
    import shutil
    import subprocess

    if os.path.isdir(CHEMIN_CT2) and os.path.isdir(CHEMIN_FUSION) and not force:
        print("Modèle déjà converti (utilise force=True pour refaire).")
        return {"deja_fait": True}

    from huggingface_hub import snapshot_download

    local = snapshot_download(adaptateur)
    est_adaptateur = os.path.isfile(os.path.join(local, "adapter_config.json"))
    print(f"{adaptateur} : {'adaptateur LoRA (PEFT)' if est_adaptateur else 'modèle complet'}")

    import torch
    from transformers import WhisperForConditionalGeneration, WhisperProcessor

    if est_adaptateur:
        from peft import PeftConfig, PeftModel
        base_id = PeftConfig.from_pretrained(local).base_model_name_or_path or BASE_PAR_DEFAUT
        print(f"Modèle de base indiqué par l'adaptateur : {base_id}")
        base = WhisperForConditionalGeneration.from_pretrained(base_id, torch_dtype=torch.float32)
        modele = PeftModel.from_pretrained(base, local).merge_and_unload()
    else:
        base_id = BASE_PAR_DEFAUT
        modele = WhisperForConditionalGeneration.from_pretrained(local, torch_dtype=torch.float32)

    # Le vocabulaire ne change pas avec un LoRA : on prend le processeur du modèle de base,
    # qui écrit aussi tokenizer.json (nécessaire à faster-whisper) et preprocessor_config.json.
    processeur = WhisperProcessor.from_pretrained(base_id)
    tmp = "/tmp/whosper_fusion_fp32"
    shutil.rmtree(tmp, ignore_errors=True)
    modele.save_pretrained(tmp)
    processeur.save_pretrained(tmp)
    if not os.path.isfile(os.path.join(tmp, "tokenizer.json")):
        from transformers import WhisperTokenizerFast
        WhisperTokenizerFast.from_pretrained(base_id).save_pretrained(tmp)

    # Voie de secours : la même chose en fp16 (moitié moins lourd)
    shutil.rmtree(CHEMIN_FUSION, ignore_errors=True)
    modele.half().save_pretrained(CHEMIN_FUSION)
    processeur.save_pretrained(CHEMIN_FUSION)

    # Voie rapide : conversion CTranslate2 (commande documentée par faster-whisper)
    shutil.rmtree(CHEMIN_CT2, ignore_errors=True)
    cmd = ["ct2-transformers-converter", "--model", tmp, "--output_dir", CHEMIN_CT2,
           "--copy_files", "tokenizer.json", "preprocessor_config.json", "--quantization", "float16", "--force"]
    print("Conversion :", " ".join(cmd))
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError("La conversion CTranslate2 a échoué :\n" + (r.stderr or r.stdout)[-1500:])
    volume.commit()
    taille = sum(os.path.getsize(os.path.join(d, f)) for d, _, fs in os.walk(CHEMIN_CT2) for f in fs) / 1e9
    print(f"Terminé. Modèle CTranslate2 : {taille:.2f} Go. Fichiers : {sorted(os.listdir(CHEMIN_CT2))}")
    return {"deja_fait": False, "adaptateur_lora": est_adaptateur, "base": base_id, "taille_ct2_go": round(taille, 2)}


@app.cls(gpu="T4", volumes={"/modeles": volume}, scaledown_window=VEILLE_S, timeout=900)
class Transcripteur:
    @modal.enter()
    def charger(self):
        import time
        t0 = time.time()
        self.backend = os.environ.get("WHOSPER_BACKEND", "ct2")
        if self.backend == "ct2":
            from faster_whisper import WhisperModel
            self.modele = WhisperModel(CHEMIN_CT2, device="cuda", compute_type="float16")
        else:
            import torch
            from transformers import WhisperForConditionalGeneration, WhisperProcessor
            self.processeur = WhisperProcessor.from_pretrained(CHEMIN_FUSION)
            self.modele = WhisperForConditionalGeneration.from_pretrained(
                CHEMIN_FUSION, torch_dtype=torch.float16).to("cuda").eval()
        self.duree_chargement = time.time() - t0
        self.debut = time.time()
        self.appels = 0

    def _inferer(self, signal, langue, beam):
        if self.backend == "ct2":
            segments, info = self.modele.transcribe(
                signal, language=langue, task="transcribe", beam_size=beam, vad_filter=False,
                condition_on_previous_text=False, without_timestamps=True)
            texte = " ".join(s.text.strip() for s in segments).strip()
            return texte, {"langue_detectee": info.language, "proba_langue": round(float(info.language_probability), 3)}
        import torch
        entree = self.processeur(signal, sampling_rate=16000, return_tensors="pt")
        traits = entree.input_features.to("cuda", dtype=torch.float16)
        options = {"task": "transcribe", "num_beams": beam}
        if langue:
            options["language"] = langue
        with torch.no_grad():
            ids = self.modele.generate(traits, **options)
        return self.processeur.batch_decode(ids, skip_special_tokens=True)[0].strip(), {"langue_detectee": langue or "auto", "proba_langue": None}

    @modal.method()
    def transcrire(self, audio: bytes, langue=None, beam: int = 1):
        """Renvoie toujours un dictionnaire ; en cas d'échec, la clé 'erreur' est renseignée."""
        import time
        froid = self.appels == 0
        self.appels += 1
        base = {"conteneur_froid": froid, "chargement_modele_s": round(self.duree_chargement, 2),
                "age_conteneur_s": round(time.time() - self.debut, 2), "backend": self.backend}
        t0 = time.time()
        try:
            signal = decoder_audio(audio)
        except Exception as e:
            return {**base, "erreur": f"décodage : {e}"}
        t1 = time.time()
        try:
            texte, info = self._inferer(signal, langue, beam)
        except Exception as e:
            return {**base, "erreur": f"{type(e).__name__} : {e}"}
        t2 = time.time()
        return {**base, "texte": texte, "duree_audio_s": round(len(signal) / 16000, 2),
                "temps_decodage_s": round(t1 - t0, 3), "temps_inference_s": round(t2 - t1, 3), **info}


@app.local_entrypoint()
def main():
    """modal run modal_whosper.py::main  ->  fusion + conversion"""
    print(convertir.remote())


@app.local_entrypoint()
def verifier():
    """modal run modal_whosper.py::verifier  ->  test de fumée : une seconde de silence"""
    import io
    import wave

    tampon = io.BytesIO()
    with wave.open(tampon, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"\x00\x00" * 16000)
    res = Transcripteur().transcrire.remote(tampon.getvalue(), None, 1)
    print(res)
    if res.get("erreur"):
        print("\nÉCHEC. Si le message parle de 'libcudnn' ou 'libcublas' : redéploie avec  set WHOSPER_BACKEND=hf")
    else:
        print(f"\nOK : voie '{res['backend']}', chargement du modèle {res['chargement_modele_s']} s, "
              f"inférence {res['temps_inference_s']} s sur {res['duree_audio_s']} s d'audio.")
