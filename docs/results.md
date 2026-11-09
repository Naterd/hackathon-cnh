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

### Projector photos and the fingerprint (Nov 9 afternoon)

Two reviews asked whether the demo survives a projector: the talk on a laptop mirrored to a projector, photographed from the room. It didn't. `find_screen()` placed the video within 3% of its true corners in 21% of simulated projector photos, and in none when the video sat in a browser window. Three changes followed, each measured on the same 200 photos (seed 1).

- **New photo modes:**
  - **projector:** a lit wall, the picture at 30–70% of the photo's width, washed out, a screen margin, clutter, sometimes a head in the way.
  - **browser:** the same, with the video in a browser window.
- **Upload size:** every photo is shrunk to 1280 px first, as the app does before uploading.
- **Not comparable with the baseline row:** these ran from a laptop over the 50 most-viewed talks (all the boxed layout), against the full live index.

| Time (MT), raw report | Change | talk@1 | talk@3 | moment | laptop screen | slide only | projector | browser window | p50 / p95 latency | Throttled lookups |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 13:50, `projector-1` | Locate the recording by its fixed surround; best-hit talk ranking; three tries on HTTP 429 | 53% | 56% | 42.5% | 76% | 72% | 54% | 10% | 15.8 s / 17.2 s | 137 of 200 |
| 14:05, `projector-2` | Add the pixel fingerprint, tried first; one retry on 429 | 73% | 80% | 63.5% | 88% | 66% | 78% | 60% | 10.3 s / 15.4 s | 103 of 200 |
| 14:11, `projector-3` | Check each placement against the layout's pixels; no retry, and a 20 s pause on inference after a 429 | **78.5%** | **86.5%** | **68%** | 90% | 82% | 82% | 60% | 1.7 s / 7.4 s | 119 of 200 |

What these runs showed:
- **Locating the recording:** matching the fixed surround (logo, mountains, box outlines) places the video within 3% of its true corners in 97% of laptop photos, 86% of projector photos and 54% of browser-window photos, on 70 simulated photos each. `find_screen()` alone: 93%, 21%, 0%.
- **The fingerprint carries projector photos.** At a 1280 px upload the projected slide is about 400 px wide, and OCR reads almost nothing: keywords alone found the right talk for 41% of them. The fingerprint alone, against all 12,086 stills, found 84%.
- **When the fingerprint is sure, it is right:** 78 of 79 in the last run. Those lookups make no inference call and take about 1.5 s.
- **False placements:** a slide's own KubeCon footer can match the logo. 14 of 60 slide-only photos were "located" wrongly, which is the drop to 66% in the second row. Correlating the flattened photo with the layout's fixed pixels separates them cleanly: right placements score 0.7–0.95, false ones below 0.
- **Throttling shaped every row.** Elastic's inference trial limit answered HTTP 429 to most lookups at 4 in parallel, after 4–6 s each. More than half of each run's answers came from keywords and the fingerprint alone. Retrying cost 16 s a lookup; failing over at once and pausing inference for 20 s brought the median to 1.7 s.
- **Talk ranking:** ranking by best hit beat summing every hit on all three runs (last run: 78.5% against 53.5% among the same top three).
- **Still unmeasured:** the full 370-talk corpus with these modes (`INGEST_NS=ingest-ci ingest/run.sh eval --n 200 --modes screen,slide,projector,browser`), stage keynotes, and real photos off a real projector.

## Simulated photos used in the docs

`docs/test-photos/appset-603s.jpg` and `networking-700s.jpg` both return the right talk and slide first. The links are `?t=549` and `?t=654`. Rechecked Nov 9, 14:08 with the lookup above, while inference was throttled.
