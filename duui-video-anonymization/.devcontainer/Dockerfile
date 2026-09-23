FROM python:3.11-bookworm

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg curl git unzip procps \
    libxext6 libxrender1 libxtst6 libxi6 libfreetype6 \
    && rm -rf /var/lib/apt/lists/*