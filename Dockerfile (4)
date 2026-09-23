FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    libsndfile1 \
    git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
# Paquet officiel Whosper, non disponible sur PyPI — installation depuis le
# dépôt GitHub, comme dans le notebook de benchmark.
RUN pip install --no-cache-dir git+https://github.com/sudoping01/whosper.git

COPY app.py .

ENV PORT=8000
EXPOSE 8000

CMD ["sh", "-c", "uvicorn app:app --host 0.0.0.0 --port ${PORT}"]
