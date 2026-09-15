"""
Service ASR isolé pour NOXA — MMS-Wolof (facebook/mms-1b-all, target_lang="wol").

Objectif de cette phase (Phase A du plan) : vérifier que MMS fonctionne de façon
fiable et reproductible en dehors de Colab, mesurer sa latence réelle en
conditions HTTP, et comparer ses sorties à celles déjà obtenues sur les 52
vocaux du benchmark — AVANT de toucher au webhook NOXA.

Endpoints :
  GET  /health      → vérifie que le service et le modèle sont chargés
  POST /transcribe   → reçoit un fichier audio, renvoie la transcription MMS
"""

import io
import logging
import time

import librosa
import torch
from fastapi import FastAPI, File, HTTPException, UploadFile
from pydantic import BaseModel
from transformers import AutoProcessor, Wav2Vec2ForCTC

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("mms-asr-service")

MODEL_NAME = "facebook/mms-1b-all"
TARGET_LANG = "wol"

app = FastAPI(title="NOXA MMS-Wolof ASR service")

# Chargé une seule fois au démarrage du service, pas à chaque requête —
# c'est justement ce qu'un simple appel HTTP générique ne permet pas de
# garantir facilement, d'où ce service dédié.
processor = None
model = None
device = None


@app.on_event("startup")
def load_model():
    global processor, model, device
    logger.info("Chargement du processor (adaptateur wolof)...")
    processor = AutoProcessor.from_pretrained(MODEL_NAME, target_lang=TARGET_LANG)
    logger.info("Chargement du modèle (peut prendre plusieurs minutes au premier démarrage)...")
    model = Wav2Vec2ForCTC.from_pretrained(
        MODEL_NAME, target_lang=TARGET_LANG, ignore_mismatched_sizes=True
    )
    device = "cuda" if torch.cuda.is_available() else "cpu"
    model = model.to(device)
    model.eval()
    logger.info(f"Modèle chargé sur : {device}")


class HealthResponse(BaseModel):
    status: str
    device: str
    model: str
    target_lang: str


class TranscriptionResponse(BaseModel):
    text: str
    duration_ms: int


@app.get("/health", response_model=HealthResponse)
def health():
    if model is None:
        raise HTTPException(status_code=503, detail="Modèle pas encore chargé")
    return HealthResponse(
        status="ok", device=device, model=MODEL_NAME, target_lang=TARGET_LANG
    )


@app.post("/transcribe", response_model=TranscriptionResponse)
async def transcribe(file: UploadFile = File(...)):
    if model is None:
        raise HTTPException(status_code=503, detail="Modèle pas encore chargé")

    started_at = time.time()

    try:
        audio_bytes = await file.read()
        speech, sr = librosa.load(io.BytesIO(audio_bytes), sr=16000)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Fichier audio invalide : {e}")

    try:
        inputs = processor(speech, sampling_rate=16000, return_tensors="pt")
        input_values = inputs.input_values.to(device)
        with torch.no_grad():
            logits = model(input_values).logits
        predicted_ids = torch.argmax(logits, dim=-1)
        text = processor.decode(predicted_ids[0])
    except Exception as e:
        logger.exception("Échec de la transcription")
        raise HTTPException(status_code=500, detail=f"Échec de la transcription : {e}")

    duration_ms = int((time.time() - started_at) * 1000)
    logger.info(f"Transcription en {duration_ms} ms : {text}")

    return TranscriptionResponse(text=text, duration_ms=duration_ms)
