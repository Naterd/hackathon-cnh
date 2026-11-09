FROM python:3.12-slim
# OpenCV, which the OCR uses, needs these.
RUN apt-get update \
 && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY app/requirements.txt .
# Load the OCR models once as root, like ingest/Dockerfile, so a missing library fails the
# build rather than the pod, which runs as uid 1000.
RUN pip install --no-cache-dir -r requirements.txt \
 && python -c "from rapidocr_onnxruntime import RapidOCR; RapidOCR()"
COPY app/ .
# The lookup, and the recording layout it finds next to itself.
COPY docs/search_example.py docs/layout-kubecon-na-2024.png ./
ARG APP_VERSION=dev
ENV APP_VERSION=$APP_VERSION PYTHONUNBUFFERED=1
USER 1000
EXPOSE 8080
# One process per pod: the Dash0 operator's auto-instrumentation doesn't follow forked workers.
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]
