# Ingest image: ffmpeg, OCR and the Python dependencies. ingest/run.sh mounts the current code
# as a ConfigMap at /code, so a code change needs no image build; /app holds a baked-in copy.
FROM python:3.12-slim
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg libgl1 libglib2.0-0 \
 && rm -rf /var/lib/apt/lists/*
COPY ingest/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt \
 && python -c "from rapidocr_onnxruntime import RapidOCR; RapidOCR()"
COPY ingest/*.py /app/
WORKDIR /app
USER 1000
