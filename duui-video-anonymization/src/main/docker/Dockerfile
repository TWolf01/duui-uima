FROM docker.io/library/python:3.11-slim-bookworm

RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY src/main/python/ .

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
EXPOSE 9717
CMD ["uvicorn", "duui_video_anonymization:app", "--host", "0.0.0.0", "--port", "9717"]
