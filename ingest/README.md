# Ingest

Turns the KubeCon recordings in GCS into searchable slide moments in Elasticsearch, in two steps:

1. **`extract.py`** runs one pod per talk on the GKE spot pool.
    - **Layout:** breakouts, lightning talks and sponsored keynotes show the slide in a fixed box. If the branded corner stays static across 8 sample frames, it crops to that box. Otherwise (stage keynotes) it uses the full frame and later drops camera shots, meaning stills with fewer than 4 OCR words.
    - **Stills:** it detects slide changes with ffmpeg (scene threshold 0.04), drops near-duplicate stills with a dHash, and caps a talk at 60 stills.
    - **Text:** it OCRs each still (RapidOCR) and attaches the captions spoken over it. It writes `talk.json`, `stills/NNN.jpg` and finally `segments.json` to `gs://<bucket>/derived/<version>/<event>/<id>/`.
2. **`index.py`** runs as one pod. It loads every extracted talk into Elasticsearch: one document per still (the `semantic` image field `slide`, OCR text, captions, start and end seconds, a YouTube link at the right second) and one per talk. It uses 16 parallel bulks of 48 and retries throttled items.

Re-running is cheap. Extract skips finished talks unless `--force`, and index overwrites documents by their stable ids. For a changed mapping or extractor, write into a new `DERIVED` version or index name, then point the `kubecon-moments` alias at it with `--alias`.

## Running it

```bash
source <hackathon>/infra/config.env       # CNH_BUCKET, and kubectl on the cnh cluster
ingest/run.sh extract --limit 50          # or --talks id,id; all talks by default
ingest/run.sh status
ingest/run.sh index --alias kubecon-moments
ingest/run.sh logs index
```

The code ships as a ConfigMap from your checkout, so a code change needs no image build. The image (`ingest/Dockerfile`, built by `.github/workflows/ingest-image.yaml`) holds ffmpeg and the OCR libraries. Before that image exists, `BOOTSTRAP=1 ingest/run.sh extract ...` installs them at pod start instead, which is slow but fine for a few talks.

Locally, with ffmpeg and `pip install -r ingest/requirements.txt`:

```bash
python ingest/extract.py --src <dir with <id>/video.mp4> --out /tmp/derived --talk <video-id>
ELASTIC_URL=... ELASTIC_ES_API_KEY=... python ingest/index.py --src /tmp/derived --index kubecon-moments-dev
```

## Measured on Nov 9

- A 33-minute slide talk: 28 changes became 21 stills in 20 seconds on a laptop. A terminal-heavy talk: 122 changes became 77 segments, capped at 60 stills, in 43 seconds.
- Elasticsearch from GKE: about 75 images a second at 24 parallel bulks; 48 parallel bulks got HTTP 429.
- Two simulated phone photos (rotated, low contrast, noisy) each found the right talk and the right slide.
