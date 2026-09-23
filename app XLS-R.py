"""
Service ASR pour NOXA — XLS-R wolof (abdouaziiz/wav2vec2-xls-r-300m-wolof-lm).

Licence MIT — candidat "rapide" du pilote à deux modèles, à opposer à Whosper
(voir whosper-asr-service). Plus faible sur le français/code-switching que
Whosper, mais ~300M paramètres seulement : nettement plus léger et rapide,
sans les soucis de mémoire rencontrés avec MMS (jamais retenu, licence
CC-BY-NC-4.0 non commerciale — voir le webhook principal).

Endpoints :
  GET  /health      → vérifie que le service et le modèle sont chargés
  POST /transcribe   → reçoit un fichier audio, renvoie la transcription
"""

import io
import logging
import time

import librosa
import torch
from fastapi import FastAPI, File, HTTPException, UploadFile
from pydantic import BaseModel
from transformers import Wav2Vec2ForCTC, Wav2Vec2Processor

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("xlsr-asr-service")

MODEL_NAME = "abdouaziiz/wav2vec2-xls-r-300m-wolof-lm"

app = FastAPI(title="NOXA XLS-R ASR service")

processor = None
model = None
device = None


@app.on_event("startup")
def load_model():
    global processor, model, device
    logger.info("Chargement du processor...")
    processor = Wav2Vec2Processor.from_pretrained(MODEL_NAME)
    logger.info("Chargement du modèle...")
    model = Wav2Vec2ForCTC.from_pretrained(MODEL_NAME)
    device = "cuda" if torch.cuda.is_available() else "cpu"
    model = model.to(device)
    model.eval()
    logger.info(f"Modèle chargé sur : {device}")


class HealthResponse(BaseModel):
    status: str
    device: str
    model: str


class TranscriptionResponse(BaseModel):
    text: str
    duration_ms: int


@app.get("/health", response_model=HealthResponse)
def health():
    if model is None:
        raise HTTPException(status_code=503, detail="Modèle pas encore chargé")
    return HealthResponse(status="ok", device=device, model=MODEL_NAME)


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
        text = processor.batch_decode(predicted_ids)[0]
    except Exception as e:
        logger.exception("Échec de la transcription")
        raise HTTPException(status_code=500, detail=f"Échec de la transcription : {e}")

    duration_ms = int((time.time() - started_at) * 1000)
    logger.info(f"Transcription en {duration_ms} ms : {text}")

    return TranscriptionResponse(text=text, duration_ms=duration_ms)
