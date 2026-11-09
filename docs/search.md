# Search: the index and how the app queries it

The index is live. The alias **`kubecon-moments`** points at `kubecon-moments-v1`, which holds 12,516 documents: 12,146 slide stills and 370 talks from KubeCon NA 2024. `docs/search_example.py` is a working reference for every query below, tested against the live index on Nov 9.

## Connecting

- **Credentials:** Secret `slidekick/app-credentials` holds `ELASTIC_URL` and `ELASTIC_ES_API_KEY`. It exists in the cluster but not in git.
- **Header:** `Authorization: ApiKey <ELASTIC_ES_API_KEY>`.
- **Wiring it in:** add `envFrom: [{secretRef: {name: app-credentials}}]` to the container in `deploy/deployment.yaml`.
- **Memory:** raise the limit from the starter's 256Mi to at least 1Gi. OCR loads about 300–500 MB of models.
- **Running locally:** ask Nate for the values. Never commit them or print them.

## The documents

Two kinds, told apart by `source`. Ids are stable: `<video_id>-<NNN>` for a still, `<video_id>-talk` for a talk.

| Field | On | Holds |
| --- | --- | --- |
| `source` | both | `frame` (a slide still) or `talk` |
| `talk_id`, `video_id` | both | The YouTube video id |
| `event` | both | `kubecon-na-2024` |
| `title`, `speakers_text` | both | `speakers_text` is parsed from the title, for example "Alexander Matyushentsev, Akuity" |
| `url` | both | Still: `https://youtu.be/<id>?t=<start_sec>`, the second the slide appeared. Talk: the YouTube watch URL. |
| `start_sec`, `end_sec`, `n` | frame | When the slide is on screen, and its order in the talk |
| `ocr_text`, `ocr_conf` | frame | Text read off the slide. OCR merges some words ("WhichSolutionIsBestForYou"); the field's analyzer splits them. |
| `said_text` | frame | Captions spoken while the slide was up |
| `slide` | frame | `semantic`: the still as an image object. Never return it in a search. |
| `said` | frame | `semantic`: the captions again, embedded |
| `still` | frame | The image's path in the bucket (`<id>/stills/NNN.jpg`). To show it, see "Showing the matched slide". |
| `description`, `abstract` | talk | The YouTube description. `abstract` is the same text as a `semantic` field. |
| `upload_date`, `duration_sec`, `caption_source` | talk | `caption_source` is `youtube` or `whisper` |

A real still, with `slide` and `said` left out:

```json
{
 "_id": "9RvDczVIiS0-008", "source": "frame", "talk_id": "9RvDczVIiS0", "event": "kubecon-na-2024",
 "title": "Mastering ApplicationSet: Advanced Argo CD Automation - Alexander Matyushentsev, Akuity",
 "speakers_text": "Alexander Matyushentsev, Akuity", "n": 8, "start_sec": 549.5, "end_sec": 615.5,
 "url": "https://youtu.be/9RvDczVIiS0?t=549", "still": "9RvDczVIiS0/stills/008.jpg",
 "ocr_text": "Solutions:ApplicationSet KubeCon North Anerica 2024 Gitbased apiVersion:argoproj.io/v1alpha1 …",
 "ocr_conf": 0.977, "said_text": "described all the reasons why we were not satisfied with available Solutions …"
}
```

## Photo lookup

1. **Prepare the photo.** Apply the EXIF orientation, shrink to at most 1280 px, and encode a JPEG under 900 KB; the field's limit is 1 MB of decoded bytes. Send it as an image object, not a bare data URL: `{"type": "image", "format": "base64", "value": "data:image/jpeg;base64,…"}`.
2. **OCR it** with the same engine as ingest (RapidOCR, `rapidocr-onnxruntime==1.4.4`), so merged words match the same way on both sides. Create the engine once at startup: 0.5 s per photo warm, about 1 s cold.
3. **Run one hybrid query** that merges three rankings with RRF:

```json
POST kubecon-moments/_search
{
  "size": 50,
  "_source": {"excludes": ["slide", "said", "abstract"]},
  "retriever": {"rrf": {"rank_window_size": 100, "retrievers": [
    {"standard": {"query": {"knn": {"field": "slide", "k": 20, "num_candidates": 100,
      "query_vector_builder": {"embedding": {"input": {"type": "image", "format": "base64", "value": "data:image/jpeg;base64,…"}}}}}}},
    {"standard": {"query": {"bool": {"filter": [{"term": {"source": "frame"}}],
      "must": {"multi_match": {"query": "<OCR text>", "fields": ["ocr_text^2", "said_text"]}}}}}},
    {"standard": {"query": {"bool": {"filter": [{"term": {"source": "frame"}}],
      "must": {"match": {"slide": "<OCR text>"}}}}}}
  ]}}
}
```

4. **Group the hits by `talk_id` in code.** Semantic fields don't support aggregations. Rank talks by their summed scores. The best talk's first hit is the moment to show, and its `url` opens YouTube at that second.

Things that bit us while testing:
- **`rank_window_size` must be at least `size`.** It defaults to 10, so `size: 50` without it returns HTTP 400.
- **Don't filter the kNN leg.** Only stills have `slide`, so it can only return stills anyway. With a `source` filter inside `knn`, the image search took 7–13 s instead of about 2 s. The text legs keep their filter.
- **Always exclude `slide`, `said` and `abstract` from `_source`,** and never highlight `slide`. Each `slide` is about 100 KB of base64.
- **Latency:** a full lookup takes 3.7–4.8 s warm from a laptop. The first query after a quiet spell took up to 13 s, so send a warm-up query at startup and show progress in the UI. The image embedding is most of the time.
- **Confidence:** RRF scores are close together (about 0.03–0.05). Use the gap between the first and second talk's summed scores, not the raw score.

Results on the two test photos in `docs/test-photos/` (rotated, low contrast, noisy):

| Photo | Top result |
| --- | --- |
| `appset-603s.jpg` (taken 10:03 into the talk) | `https://youtu.be/9RvDczVIiS0?t=549`, the right slide |
| `networking-700s.jpg` (11:40 into the talk) | `https://youtu.be/Mj04QOqAaJ8?t=654`, the right slide |

## Showing the matched slide

`GET kubecon-moments/_doc/<id>?_source_includes=slide` returns `slide.value`, a data URL you can put straight into `<img src>`: about 84 KB, about 1 s. It's one document, so including `slide` is fine here. The app has no access to the bucket, and doesn't need it.

## Related talks

Match the talk's description against the other talks' `abstract`:

```json
{"size": 5, "_source": {"includes": ["talk_id", "title", "url"]},
 "query": {"bool": {"filter": [{"term": {"source": "talk"}}],
   "must": {"match": {"abstract": "<this talk's description>"}},
   "must_not": [{"term": {"talk_id": "<this talk>"}}]}}}
```

It works well for the networking talk, less well for others. Every description opens with the same CNCF promo paragraph ("Don't miss out! Join us at our next Flagship Conference…"), which blurs the match. v2 strips it.

## Camera roll

- **Read the time first:** read EXIF `DateTimeOriginal` in the browser before drawing to a canvas, which drops EXIF. A time without an offset is Mountain Time. Don't rely on GPS.
- **Look up each photo** as above, a few at a time, and stream each result onto the timeline as it arrives.
- **2024 photos** belong on the timeline at their talk's 2024 slot: `starts_at` in `kubecon-details`.

## Talk details and slide thumbnails (`kubecon-details`)

A second index for the extras that need no embeddings. It has the same ids as `kubecon-moments`, so after a lookup one `_mget` fetches them. It has no semantic fields, so Elastic's inference limits never apply to it.

**Never write to `kubecon-moments` from the app.** Even a partial update re-runs inference on the whole document. While Elastic throttled us, an update stored a still without its vectors and still answered `"result": "updated"` (Nov 9).

| Field | On | Holds |
| --- | --- | --- |
| `title_clean` | talk | The Sched title without the " - Speaker, Company" suffix |
| `starts_at`, `ends_at` | talk | The 2024 slot, ISO time with the Mountain Time offset, for example `2024-11-13T15:25:00-07:00` |
| `room` | talk | For example `Salt Palace \| Level 3 \| 355 E` |
| `track` | talk | Sched's category, for example `Maintainer Track` |
| `speakers` | talk | `[{"name", "position", "company"}]`, from each speaker's Sched profile. Where Sched has no profiles (some lightning talks), names come from the session title and `position` is empty. |
| `decks` | talk | `[{"name", "url"}]`: the slides uploaded to Sched. Link to them; never serve the PDFs. |
| `sched_url` | talk | The session's Sched page |
| `thumb` | still | A 320 px JPEG, base64 without a prefix, 6–10 KB: `<img src="data:image/jpeg;base64,{thumb}">` |
| `slide_title` | still | The slide's biggest line of text near the top, for labels and alt text. It's empty for title cards and slides without a clear title. |
| `n`, `start_sec`, `end_sec`, `url`, `title`, `speakers_text` | still | The same values as in `kubecon-moments`, so a hit here builds a result without a second fetch |
| `fp` | still | A 576-number pixel fingerprint (32×18 grayscale, mean-centred, unit length) for a native kNN leg that needs no inference. `fingerprint()` in `ingest/details.py` defines it, and the query side must compute it the same way. Blank stills have none. Exclude it from `_source`. |

```json
GET kubecon-details/_mget
{"ids": ["9RvDczVIiS0-talk", "9RvDczVIiS0-008"]}

POST kubecon-details/_search
{"size": 60, "query": {"bool": {"filter": [{"term": {"talk_id": "9RvDczVIiS0"}}, {"term": {"source": "frame"}}]}},
 "sort": [{"n": "asc"}], "_source": ["n", "start_sec", "thumb", "slide_title"]}
```

The second query is the deck strip: a whole talk's thumbnails in one request, about 300 KB for 32 slides.

Coverage: 361 of the 370 talks have Sched data, 315 of them with speakers and 206 with a deck. The other nine are the daily highlight reels and a few co-located-event talks that aren't on this Sched; they have no talk document here, so fall back to `title` and `speakers_text` from `kubecon-moments`. To refresh: `ingest/sched.py` writes the JSON to `gs://<bucket>/derived/sched/kubecon-na-2024.json`, then `ingest/run.sh details` reloads the index.

## Not in the index yet

- Descriptions with the CNCF boilerplate stripped
- Photos users upload (`source: photo`, `roll_id`, `taken_at`), so a timeline can be reopened
- This week's 2026 sessions and decks

## For the app image

`pillow` and `rapidocr-onnxruntime==1.4.4`, which pulls in `onnxruntime` and `opencv-python`. Apt packages `libgl1` and `libglib2.0-0` are needed for OpenCV. Don't add any `opentelemetry-*` package: the Dash0 operator injects the SDK and turns itself off if it finds one.
