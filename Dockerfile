FROM python:3.11-slim

# ffmpeg/libsndfile nécessaires pour librosa (décodage des fichiers audio .ogg de WhatsApp)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    libsndfile1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
# Test de diagnostic : retour temporaire au paquet torch standard (celui du
# tout premier essai), pour vérifier si la version CPU-only introduite pour
# le problème de disque est la cause du ralentissement massif observé en
# Docker (~150-300s par vocal, contre 4-13s en exécution native Windows).
# Le disque WSL2 dispose en réalité de 1 To de limite (confirmé dans Docker
# Desktop), donc l'espace disque n'était probablement pas le vrai problème
# du premier échec de build.
RUN pip install --no-cache-dir torch==2.4.1
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py .

# Le port est fourni par la plateforme d'hébergement via $PORT (Render, Fly.io...) ;
# 8000 par défaut si non défini (ex: test local).
ENV PORT=8000
EXPOSE 8000

CMD ["sh", "-c", "uvicorn app:app --host 0.0.0.0 --port ${PORT}"]
