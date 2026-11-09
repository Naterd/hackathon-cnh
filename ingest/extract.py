"""Cut one KubeCon talk into slide stills, with OCR text and the captions spoken over each.

Input, under --src:   <event>/<id>/video.mp4, video.info.json, video.en.vtt or video.en.whisper.vtt
Output, under --out:  <id>/talk.json, <id>/stills/NNN.jpg, and <id>/segments.json written last,
                      so segments.json existing means the talk is done.

In the cluster each pod of an Indexed Job handles one talk: JOB_COMPLETION_INDEX picks the line of
--talks. Locally: python extract.py --src <dir> --out <dir> --talk <video-id>
"""
import argparse
import concurrent.futures as cf
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

import numpy as np
from PIL import Image

try:
    import telemetry  # next to this script in the Job's /code; a no-op without the Dash0-injected SDK
except ImportError:
    from ingest import telemetry

# Breakouts, lightning talks and sponsored keynotes use a fixed layout: the slide in a box on the
# left, the speaker camera top right, and a static branded area bottom right. Stage keynotes cut
# between full-screen camera shots and full-screen slides instead.
CROP_BOX = "836:474:11:109"   # the slide box in the 720p layout, w:h:x:y
CROP_FULL = "iw:ih:0:0"
LOGO_AREA = "400:220:870:380"  # static in the boxed layout (frame diff 0.2-0.4), moving in keynotes (9+)
LOGO_STATIC = 2.0
SCENE = 0.04            # ffmpeg scene-change threshold (0.1 misses slides that share a template)
MIN_SEGMENT = 3.0        # seconds; shorter segments are transitions and fold into the previous slide
DUP_BITS = 6             # dHash distance at or below which consecutive stills count as the same slide
MAX_STILLS = 60          # per talk; terminal-heavy talks otherwise produce hundreds
MIN_WORDS_FULL = 4       # full-frame layout: stills with less OCR text are camera shots, not slides
MAX_IMAGE_BYTES = 900_000  # under the semantic field's 1 MB limit; our stills are 60-300 KB


def log(msg):
    print(f"{time.strftime('%H:%M:%S')} {msg}", flush=True)


def run(cmd):
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode:
        raise RuntimeError(f"{cmd[0]} failed: {p.stderr[-800:]}")
    return p


def layout(video, duration, samples=8):
    """'box' when the branded corner stays static across the talk, else 'full' (stage keynote)."""
    frames = []
    for k in range(1, samples + 1):
        p = subprocess.run(["ffmpeg", "-loglevel", "error", "-ss", f"{duration * k / (samples + 1):.1f}",
                            "-i", str(video), "-frames:v", "1", "-vf", f"crop={LOGO_AREA},scale=100:55,format=gray",
                            "-f", "rawvideo", "-"], capture_output=True)  # binary: raw gray pixels
        frames.append(np.frombuffer(p.stdout, np.uint8).astype(np.int16))
    diffs = [np.abs(a - b).mean() for a, b in zip(frames, frames[1:]) if a.size == b.size and a.size]
    # Median, not mean: a boxed talk can cut to a full-screen camera or title card for a moment,
    # which spikes two of the pairs; a stage keynote moves in nearly every pair.
    return "box" if diffs and float(np.median(diffs)) < LOGO_STATIC else "full"


def scene_cuts(video, crop):
    """Times (seconds) where the slide area changes."""
    vf = f"crop={crop},fps=2,select=gt(scene\\,{SCENE}),showinfo"
    p = run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(video), "-vf", vf, "-an", "-f", "null", "-"])
    return [float(t) for t in re.findall(r"pts_time:([0-9.]+)", p.stderr)]


def segments(cuts, duration):
    bounds = [0.0] + sorted(c for c in cuts if 0 < c < duration) + [duration]
    segs = []
    for start, end in zip(bounds, bounds[1:]):
        if segs and end - start < MIN_SEGMENT:
            segs[-1]["end"] = end
        else:
            segs.append({"start": start, "end": end})
    if len(segs) > 1 and segs[0]["end"] - segs[0]["start"] < MIN_SEGMENT:
        segs[1]["start"] = segs[0]["start"]
        segs.pop(0)
    return segs


def grab(video, t, dst, crop):
    run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{t:.2f}", "-i", str(video),
         "-frames:v", "1", "-vf", f"crop={crop}", "-q:v", "3", str(dst)])


def fit_under_limit(path):
    """Re-encode a still above the semantic field's 1 MB image limit (decoded bytes)."""
    if path.stat().st_size <= MAX_IMAGE_BYTES:
        return
    im = Image.open(path).convert("RGB")
    im.thumbnail((1280, 1280))
    for quality in (85, 70, 55, 40):
        im.save(path, "JPEG", quality=quality)
        if path.stat().st_size <= MAX_IMAGE_BYTES:
            return


def dhash(path):
    a = np.asarray(Image.open(path).convert("L").resize((9, 8), Image.LANCZOS), dtype=np.int16)
    bits = (a[:, 1:] > a[:, :-1]).flatten()
    return int("".join("1" if b else "0" for b in bits), 2)


_ocr = None


def _ocr_init():
    global _ocr
    from rapidocr_onnxruntime import RapidOCR
    _ocr = RapidOCR(intra_op_num_threads=1, inter_op_num_threads=1)


def ocr(path):
    """OCR one still; one engine per worker process, single-threaded (cheapest per image)."""
    assert _ocr is not None, "worker started without _ocr_init"
    res, _ = _ocr(str(path))
    lines = res or []
    lines.sort(key=lambda r: (round(r[0][0][1] / 20), r[0][0][0]))  # rows top to bottom, then left to right
    text = " ".join(r[1] for r in lines)
    conf = sum(float(r[2]) for r in lines) / len(lines) if lines else 0.0
    return text, round(conf, 3)


TIME = re.compile(r"(?:(\d+):)?(\d+):(\d+)[.,](\d+)\s+-->")
TAG = re.compile(r"<[^>]+>")


def captions(path):
    """[(seconds, text)] from a WebVTT file. YouTube auto-captions repeat each line in the next
    cue; consecutive duplicates are dropped."""
    out, t, last = [], None, None
    for raw in path.read_text(errors="replace").splitlines():
        m = TIME.match(raw)
        if m:
            h, mi, s, frac = m.groups()
            t = int(h or 0) * 3600 + int(mi) * 60 + int(s) + float("0." + frac)
            continue
        line = TAG.sub("", raw).strip()
        if t is None or not line or line == last:
            continue
        out.append((t, line))
        last = line
    return out


def caption_file(talk_dir):
    for name in ("video.en.vtt", "video.en.whisper.vtt"):
        if (talk_dir / name).exists():
            return talk_dir / name, ("youtube" if name == "video.en.vtt" else "whisper")
    return None, None


def talk_meta(info, vid, caption_source):
    title = info.get("title") or vid
    speakers = title.rsplit(" - ", 1)[1] if " - " in title else ""
    return {
        "video_id": vid,
        "title": title,
        "speakers_text": speakers,
        "description": info.get("description") or "",
        "upload_date": info.get("upload_date"),
        "duration": info.get("duration"),
        "url": f"https://www.youtube.com/watch?v={vid}",
        "channel": info.get("channel"),
        "tags": info.get("tags") or [],
        "caption_source": caption_source,
    }


def extract(src_event, out_root, vid, workers, force):
    talk_dir, out_dir = src_event / vid, out_root / vid
    if (out_dir / "segments.json").exists() and not force:
        log(f"{vid}: already extracted, skipping")
        return
    t0 = time.time()
    with tempfile.TemporaryDirectory(dir=os.environ.get("WORK", None)) as tmp:
        work = Path(tmp)
        video = work / "video.mp4"
        shutil.copyfile(talk_dir / "video.mp4", video)
        info = json.loads((talk_dir / "video.info.json").read_text())
        duration = float(info.get("duration") or 0)
        cap_path, cap_source = caption_file(talk_dir)
        lines = captions(cap_path) if cap_path else []

        with telemetry.phase("layout"):
            shape = layout(video, duration)
        crop = CROP_BOX if shape == "box" else CROP_FULL
        with telemetry.phase("scene_detect"):
            cuts = scene_cuts(video, crop)
            segs = segments(cuts, duration)
        stills = work / "stills"
        stills.mkdir()
        with telemetry.phase("grab"), cf.ThreadPoolExecutor(workers) as ex:
            list(ex.map(lambda i: grab(video, (segs[i]["start"] + segs[i]["end"]) / 2, stills / f"{i:03d}.jpg", crop),
                        range(len(segs))))

        with telemetry.phase("dedupe"):
            kept, last_hash = [], 0
            for i, s in enumerate(segs):
                path = stills / f"{i:03d}.jpg"
                h = dhash(path)
                if kept and bin(h ^ last_hash).count("1") <= DUP_BITS:  # same slide as the previous still
                    kept[-1]["end"] = s["end"]
                    continue
                kept.append({**s, "file": path})
                last_hash = h
            if len(kept) > MAX_STILLS:
                longest = sorted(kept, key=lambda k: k["end"] - k["start"], reverse=True)[:MAX_STILLS]
                kept = sorted(longest, key=lambda k: k["start"])
                for a, b in zip(kept, kept[1:]):
                    a["end"] = b["start"]
                kept[-1]["end"] = duration

        with telemetry.phase("ocr"), cf.ProcessPoolExecutor(workers, initializer=_ocr_init) as ex:
            texts = list(ex.map(ocr, [k["file"] for k in kept]))
        if shape == "full":  # drop camera shots: keep stills with enough slide text
            pairs = [(k, t) for k, t in zip(kept, texts) if len(t[0].split()) >= MIN_WORDS_FULL]
            kept, texts = [p[0] for p in pairs], [p[1] for p in pairs]

        with telemetry.phase("write"):
            out_dir.mkdir(parents=True, exist_ok=True)
            (out_dir / "stills").mkdir(exist_ok=True)
            records = []
            for n, (k, (text, conf)) in enumerate(zip(kept, texts)):
                name = f"{n:03d}.jpg"
                fit_under_limit(k["file"])
                shutil.copyfile(k["file"], out_dir / "stills" / name)
                said = " ".join(line for t, line in lines if k["start"] <= t < k["end"])
                records.append({"n": n, "start": round(k["start"], 2), "end": round(k["end"], 2),
                                "still": f"stills/{name}", "ocr_text": text, "ocr_conf": conf, "said": said})
            (out_dir / "talk.json").write_text(json.dumps({**talk_meta(info, vid, cap_source), "layout": shape}, indent=1))
            (out_dir / "segments.json").write_text(json.dumps(records, indent=1))
        telemetry.stills(kept=len(records), dropped=len(segs) - len(records))
    log(f"{vid}: layout={shape}, {len(cuts)} cuts -> {len(segs)} segments -> {len(records)} stills, "
        f"captions={cap_source}, {time.time() - t0:.0f}s")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True, help="directory holding <id>/video.mp4 folders")
    ap.add_argument("--out", required=True, help="directory to write <id>/ results into")
    ap.add_argument("--talk", help="one video id; default: line JOB_COMPLETION_INDEX of --talks")
    ap.add_argument("--talks", help="file with one video id per line")
    ap.add_argument("--workers", type=int, default=int(os.environ.get("WORKERS", os.cpu_count() or 2)))
    ap.add_argument("--force", action="store_true", default=os.environ.get("FORCE") == "1")
    a = ap.parse_args()
    vid = a.talk or Path(a.talks).read_text().split()[int(os.environ["JOB_COMPLETION_INDEX"])]
    with telemetry.talk(vid):
        extract(Path(a.src), Path(a.out), vid, a.workers, a.force)
    telemetry.flush()


if __name__ == "__main__":
    main()
