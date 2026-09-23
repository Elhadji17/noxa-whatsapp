"""
Service ASR pour NOXA — Whosper (CAYTU/whosper-large-v2).

Licence Apache 2.0 — candidat "qualité" du pilote à deux modèles (meilleur
sur le français/code-switching que XLS-R), mais Whisper-large-v2 est un
modèle autorégressif (génère mot par mot) : ~4 secondes par vocal mesurées
sur GPU dans nos tests Colab. Sur CPU, attends-toi à un ralentissement bien
plus marqué que pour XLS-R ou MMS (probablement plusieurs dizaines de
secondes par vocal) — un hébergement avec GPU est fortement recommandé pour
ce modèle spécifiquement, contrairement à XLS-R qui reste léger sur CPU.

Endpoints :
  GET  /health      → vérifie que le service et le modèle sont chargés
  POST /transcribe   → reçoit un fichier audio, renvoie la transcription
"""

import logging
import tempfile
import time

from fastapi import FastAPI, File, HTTPException, UploadFile
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("whosper-asr-service")

MODEL_ID = "CAYTU/whosper-large-v2"

app = FastAPI(title="NOXA Whosper ASR service")

transcriber = None


@app.on_event("startup")
def load_model():
    global transcriber
    # Import différé : le paquet whosper charge torch/transformers/peft à
    # l'import, autant ne le faire qu'au démarrage réel du service.
    from whosper import WhosperTranscriber

    logger.info("Chargement de Whosper (peut prendre plusieurs minutes au premier démarrage)...")
    transcriber = WhosperTranscriber(model_id=MODEL_ID)
    logger.info("Modèle chargé.")


class HealthResponse(BaseModel):
    status: str
    model: str


class TranscriptionResponse(BaseModel):
    text: str
    duration_ms: int


@app.get("/health", response_model=HealthResponse)
def health():
    if transcriber is None:
        raise HTTPException(status_code=503, detail="Modèle pas encore chargé")
    return HealthResponse(status="ok", model=MODEL_ID)


@app.post("/transcribe", response_model=TranscriptionResponse)
async def transcribe(file: UploadFile = File(...)):
    if transcriber is None:
        raise HTTPException(status_code=503, detail="Modèle pas encore chargé")

    started_at = time.time()

    try:
        audio_bytes = await file.read()
        # WhosperTranscriber attend un chemin de fichier, pas des bytes en
        # mémoire — on passe par un fichier temporaire.
        with tempfile.NamedTemporaryFile(suffix=".ogg", delete=True) as tmp:
            tmp.write(audio_bytes)
            tmp.flush()
            result = transcriber.transcribe_audio(tmp.name)
    except Exception as e:
        logger.exception("Échec de la transcription")
        raise HTTPException(status_code=500, detail=f"Échec de la transcription : {e}")

    text = result.get("text", str(result)) if isinstance(result, dict) else str(result)
    duration_ms = int((time.time() - started_at) * 1000)
    logger.info(f"Transcription en {duration_ms} ms : {text}")

    return TranscriptionResponse(text=text, duration_ms=duration_ms)
