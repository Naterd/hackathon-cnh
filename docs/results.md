# Results log

What we measured, and when, for the demo and the judges. Add a row each time we change the ingest or the lookup and re-run the eval. Raw eval reports are in `docs/results/`.

## The corpus (KubeCon + CloudNativeCon NA 2024, Salt Lake City)

| | |
| --- | --- |
| Recordings | 370 talks, 179 hours, 720p (one playlist video is private) |
| Captions | 263 from YouTube, 107 transcribed with Whisper large-v3 where YouTube rate-limited us |
| Indexed (`kubecon-moments-v1`) | 12,516 documents: 12,146 slide stills and 370 talks |
| Layouts | 338 talks with the slide-box layout, 32 full-frame (stage keynotes, panels, highlight reels) |

## Ingest runs

| Date (MT) | What | Time | Compute | Result |
| --- | --- | --- | --- | --- |
| Nov 9, 10:43 | Extract all 370 talks (v1) | 25 min | 96 pods on 32 spot e2-standard-8 nodes (256 vCPUs) | 370 ok, 0 failed |
| Nov 9, 11:22 | Re-extract 66 talks after fixing the layout detection (mean → median) | 8 min | 66 pods | 34 moved back to the box layout |
| Nov 9, 11:20 | Index all talks into Elasticsearch | 6.5 min | 1 pod, 16 parallel bulks | 11,981 docs, 0 failed (31 docs/s, each with an image and a caption embedding) |
| Nov 9, 11:34 | Re-index the 66 re-extracted talks with `--replace` | 45 s | 1 pod | 1,104 old docs replaced by 1,639 |

What's behind those numbers:
- **Slide detection:** at threshold 0.04 on the cropped slide area, a 33-minute slide talk gives 28 changes, which become 21 stills. A terminal-heavy talk gives 122 changes, capped at 60 stills.
- **Elasticsearch ingest from GKE:** 14 images a second with one bulk, 52 with 12 parallel bulks, 75 with 24 parallel bulks. At 48 parallel bulks every request got HTTP 429. From the laptop it peaked at 14 a second, because uploading was the bottleneck.
- **OCR:** RapidOCR takes 1.7 s per still on one core, the biggest share of ingest CPU.

## Lookup accuracy (`ingest/eval.py`)

Random moments from random talks, each rendered as a phone photo with random perspective, tilt, glare, blur, noise and JPEG quality, then run through the app's lookup against the live index.

- **Photo modes:**
  - **screen:** the whole video frame, as when photographing a laptop playing the talk. This is the demo.
  - **slide:** only the slide, as when photographing the projector screen at the venue.
- **Metrics:**
  - **talk@1:** the right talk comes first.
  - **moment:** the top hit is the slide that was on screen, so the YouTube link opens at the right second.

| Date (MT) | Index | Change | n | talk@1 | talk@3 | moment | moment ±30 s | screen talk@1 | slide talk@1 | p50 / p95 latency |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Nov 9, 12:11 | v1 | Baseline: image kNN + OCR keywords + text-to-image, RRF | 200 | 58% | 73.5% | 43% | 47.5% | 47% | 69% | 3.5 s / 7.4 s |

What the baseline showed:
- **Screen photos match the wrong stills.** A photo of the whole video frame looks most like the full-frame stills from keynotes and panels, so a handful of those talks win over and over. The stills we indexed are cropped to the slide box. The slide text is also small in a whole-frame photo, so OCR reads only 5–11 words.
- **Slides with almost no text** (0–1 OCR words) depend entirely on the image search, which is weaker.
- **Stage keynotes:** 22% talk@1, though only 18 photos.
- **Next:** find the screen in the photo, flatten it, and crop to the slide box before the image search.

## Simulated photos used in the docs

`docs/test-photos/appset-603s.jpg` and `networking-700s.jpg` both return the right talk and slide first. The links are `?t=549` and `?t=654`.
