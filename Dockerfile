FROM python:3.11-slim

# ffmpeg/libsndfile nécessaires pour librosa (décodage des fichiers audio .ogg de WhatsApp)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    libsndfile1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py .

# Le port est fourni par la plateforme d'hébergement via $PORT (Render, Fly.io...) ;
# 8000 par défaut si non défini (ex: test local).
ENV PORT=8000
EXPOSE 8000

CMD ["sh", "-c", "uvicorn app:app --host 0.0.0.0 --port ${PORT}"]
