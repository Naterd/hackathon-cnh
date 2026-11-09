"""Random accuracy test: realistic phone photos of random moments, run through the app's lookup.

Picks random moments from the extracted talks (derived/<version>), renders each one as a phone
photo (perspective, rotation, glare, blur, noise, JPEG quality), runs the app's lookup on it
(docs/search_example.py, which run.sh ships alongside) and scores it:
  talk@1, talk@3  the right talk is first, or in the top 3
  moment          the top hit is the slide on screen at that moment, so its link lands right
Photo modes:
  screen     the whole video frame, as when photographing a laptop playing the talk
  slide      only the slide, as when photographing the projector screen at the venue
  projector  the talk played full screen on a laptop mirrored to a projector, photographed from the room:
             a lit wall, a small washed-out picture, a screen margin, clutter, sometimes a head in the way
  browser    the same, with the video in a browser window instead of full screen
Photos are shrunk to --upload-px on the long side first, as app/static/app.js does before uploading.

  python eval.py --videos /bucket/kubecon-na-2024 --derived /bucket/derived/v1/kubecon-na-2024 --n 200
"""
import argparse
import concurrent.futures as cf
import io
import json
import math
import random
import statistics
import subprocess
import time
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter

import search_example
from extract import CROP_BOX

INDEX = "kubecon-moments"


def frame_at(video, t, crop=None):
    vf = ["-vf", f"crop={crop}"] if crop else []
    p = subprocess.run(["ffmpeg", "-loglevel", "error", "-ss", f"{t:.2f}", "-i", str(video), "-frames:v", "1",
                        *vf, "-f", "image2pipe", "-vcodec", "png", "-"], capture_output=True, check=True)
    return Image.open(io.BytesIO(p.stdout)).convert("RGB")


def perspective_coeffs(src, dst):
    """Coefficients for Image.transform(PERSPECTIVE): maps output points dst back to input points src."""
    rows = []
    for (x, y), (u, v) in zip(dst, src):
        rows.append([x, y, 1, 0, 0, 0, -u * x, -u * y])
        rows.append([0, 0, 0, x, y, 1, -v * x, -v * y])
    return np.linalg.solve(np.array(rows, float), np.array(src, float).reshape(8)).tolist()


def phone_photo(img, rng):
    """The frame as a handheld phone would see a screen: bezel, keystone, tilt, light, blur, noise."""
    w, h = img.size
    pad = int(w * 0.04)
    screen = Image.new("RGB", (w + 2 * pad, h + 2 * pad), (12, 12, 14))
    screen.paste(img, (pad, pad))
    W, H = 1600, 1200
    sw, sh = screen.size
    scale = rng.uniform(0.65, 0.95) * min(W / sw, H / sh)
    cx, cy = W / 2 + rng.uniform(-60, 60), H / 2 + rng.uniform(-60, 60)
    hw, hh = sw * scale / 2, sh * scale / 2
    j = lambda: rng.uniform(-0.1, 0.1) * hw  # keystone: each corner moves independently
    dst = [(cx - hw + j(), cy - hh + j()), (cx + hw + j(), cy - hh + j()),
           (cx + hw + j(), cy + hh + j()), (cx - hw + j(), cy + hh + j())]
    src = [(0, 0), (sw, 0), (sw, sh), (0, sh)]
    bg = tuple(rng.randint(20, 90) for _ in range(3))
    warped = screen.transform((W, H), Image.PERSPECTIVE, perspective_coeffs(src, dst), Image.BICUBIC,
                              fillcolor=bg)
    photo = warped.rotate(rng.uniform(-8, 8), Image.BICUBIC, fillcolor=bg)
    photo = ImageEnhance.Brightness(photo).enhance(rng.uniform(0.75, 1.25))
    photo = ImageEnhance.Contrast(photo).enhance(rng.uniform(0.7, 1.1))
    if rng.random() < 0.6:  # glare: a soft bright spot
        glare = Image.new("L", (W, H), 0)
        gx, gy, r = rng.uniform(0.2, 0.8) * W, rng.uniform(0.2, 0.8) * H, rng.uniform(120, 360)
        ImageDraw.Draw(glare).ellipse((gx - r, gy - r * 0.6, gx + r, gy + r * 0.6), fill=int(rng.uniform(70, 160)))
        photo = Image.composite(Image.new("RGB", (W, H), (255, 255, 255)), photo,
                                glare.filter(ImageFilter.GaussianBlur(r / 2.5)))
    photo = photo.filter(ImageFilter.GaussianBlur(rng.uniform(0, 1.6)))
    a = np.asarray(photo, np.int16) + np.random.default_rng(rng.randint(0, 2**31)).normal(0, rng.uniform(2, 9), (H, W, 3))
    buf = io.BytesIO()
    Image.fromarray(np.clip(a, 0, 255).astype(np.uint8)).save(buf, "JPEG", quality=rng.randint(55, 90))
    return buf.getvalue()


def browser_window(frame, rng):
    """The frame as a video playing in a browser window: tab bar, title, a column of suggested videos."""
    W, H = 1728, 1117
    d = np.full((H, W, 3), 250, np.uint8)
    d[:86] = (228, 226, 224)
    cv2.rectangle(d, (140, 46), (1300, 74), (255, 255, 255), -1)
    vw = rng.randint(1000, 1180)
    vh, x0, y0 = round(vw * 9 / 16), rng.randint(40, 110), 100
    d[y0:y0 + vh, x0:x0 + vw] = cv2.resize(frame, (vw, vh), interpolation=cv2.INTER_AREA)
    cv2.putText(d, "Talk title goes here - Speaker Name, Company", (x0, y0 + vh + 44), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (20, 20, 20), 2)
    for k in range(6):
        yy = 100 + k * 110
        cv2.rectangle(d, (x0 + vw + 30, yy), (x0 + vw + 200, yy + 94), (rng.randint(40, 200),) * 3, -1)
        cv2.rectangle(d, (x0 + vw + 212, yy + 8), (min(W - 20, x0 + vw + 480), yy + 22), (90, 90, 90), -1)
    return d


def projector_photo(img, rng, browser=False):
    """The frame on a projector screen, photographed from the audience. Returns JPEG bytes, 2000x1500."""
    np_rng = np.random.default_rng(rng.randint(0, 2**31))
    content = cv2.cvtColor(np.asarray(img), cv2.COLOR_RGB2BGR)
    if browser:
        content = browser_window(content, rng)
    ch, cw = content.shape[:2]
    W, H = 2000, 1500
    base = rng.randint(120, 215)  # a lit wall, darker toward the audience
    shade = np.linspace(1.0, rng.uniform(0.45, 0.8), H)[:, None, None]
    bg = np.clip(np.full((H, W, 3), [base + rng.randint(-12, 12) for _ in range(3)], np.float32) * shade, 0, 255).astype(np.uint8)
    for _ in range(rng.randint(2, 6)):  # clutter: doors, banners, ceiling lines
        x, y, w, h = rng.randint(0, W - 100), rng.randint(0, H - 100), rng.randint(60, 500), rng.randint(40, 500)
        cv2.rectangle(bg, (x, y), (x + w, y + h), [rng.randint(30, 230) for _ in range(3)], -1 if rng.random() < 0.6 else 6)
    frac, wash = rng.uniform(0.3, 0.7), rng.uniform(0.08, 0.38)  # share of the photo's width; ambient light
    m = int(cw * rng.uniform(0.0, 0.08))                           # blank screen around the picture
    b = int(cw * rng.uniform(0.008, 0.02)) if rng.random() < 0.5 else 0  # the screen's black frame
    surf = np.full((ch + 2 * (m + b), cw + 2 * (m + b), 3), 10, np.uint8)
    if m:
        surf[b:-b or None, b:-b or None] = 235
    tint = np.float32([rng.uniform(0.9, 1.0), rng.uniform(0.92, 1.0), 1.0])
    c = content.astype(np.float32) * (1 - wash) * rng.uniform(0.8, 1.0) * tint + wash * 255 * rng.uniform(0.8, 1.0)
    surf[m + b:m + b + ch, m + b:m + b + cw] = np.clip(c, 0, 255).astype(np.uint8)  # lifted blacks, flat contrast
    sh, sw = surf.shape[:2]
    scale = frac * W / sw
    cx, cy = W / 2 + rng.uniform(-150, 150), H * rng.uniform(0.36, 0.5)
    hw, hh = sw * scale / 2, sh * scale / 2
    j = lambda: rng.uniform(-0.09, 0.09) * hw  # keystone
    dst = np.float32([[cx - hw + j(), cy - hh + j()], [cx + hw + j(), cy - hh + j()],
                      [cx + hw + j(), cy + hh + j()], [cx - hw + j(), cy + hh + j()]])
    a = np.deg2rad(rng.uniform(-5, 5))
    dst = ((dst - [cx, cy]) @ np.float32([[np.cos(a), -np.sin(a)], [np.sin(a), np.cos(a)]]).T + [cx, cy]).astype(np.float32)
    M = cv2.getPerspectiveTransform(np.float32([[0, 0], [sw, 0], [sw, sh], [0, sh]]), dst)
    mask = cv2.warpPerspective(np.full((sh, sw), 255, np.uint8), M, (W, H))
    photo = np.where(mask[..., None] > 127, cv2.warpPerspective(surf, M, (W, H), flags=cv2.INTER_AREA), bg)
    if rng.random() < 0.35:  # a head in front of the bottom edge
        hx, hy = int(rng.uniform(cx - hw, cx + hw)), int(cy + hh + rng.uniform(-0.05, 0.25) * hh)
        cv2.ellipse(photo, (hx, hy + 120), (rng.randint(70, 130), rng.randint(110, 170)), 0, 0, 360, (25, 22, 20), -1)
    if rng.random() < 0.25:  # glare
        g, r = np.zeros((H, W), np.float32), rng.uniform(120, 360)
        cv2.ellipse(g, (int(rng.uniform(0.2, 0.8) * W), int(rng.uniform(0.2, 0.8) * H)), (int(r), int(r * 0.6)), 0, 0, 360, rng.uniform(0.3, 0.6), -1)
        g = cv2.GaussianBlur(g, (0, 0), r / 2.5)[..., None]
        photo = (photo * (1 - g) + 255 * g).astype(np.uint8)
    photo = cv2.GaussianBlur(photo, (0, 0), rng.uniform(0.6, 2.0))
    photo = np.clip(photo.astype(np.float32) * rng.uniform(0.8, 1.15) + np_rng.normal(0, rng.uniform(2, 8), photo.shape), 0, 255)
    return cv2.imencode(".jpg", photo.astype(np.uint8), [cv2.IMWRITE_JPEG_QUALITY, rng.randint(60, 90)])[1].tobytes()


def upload(jpeg, px):
    """What the app sends: the photo shrunk to px on its long side, JPEG quality 85 (app/static/app.js)."""
    im = Image.open(io.BytesIO(jpeg))
    if not px or max(im.size) <= px:
        return jpeg
    im.thumbnail((px, px))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=85)
    return buf.getvalue()


def sample(derived, n, rng, modes):
    talks = []
    for d in sorted(p.parent for p in Path(derived).glob("*/segments.json")):
        segs = [s for s in json.loads((d / "segments.json").read_text()) if s["end"] - s["start"] >= 4]
        if segs:
            talks.append((d.name, json.loads((d / "talk.json").read_text()).get("layout", "box"), segs))
    for i in range(n):
        vid, layout, segs = rng.choice(talks)
        s = rng.choice(segs)
        yield {"i": i, "talk_id": vid, "layout": layout, "mode": modes[i % len(modes)],
               "t": round(rng.uniform(s["start"] + 1, s["end"] - 1), 2), "seg": [s["start"], s["end"]]}


def run_one(index, videos, case, seed, save_dir, upload_px):
    rng = random.Random(seed * 100003 + case["i"])
    crop = CROP_BOX if case["mode"] == "slide" and case["layout"] == "box" else None
    frame = frame_at(Path(videos) / case["talk_id"] / "video.mp4", case["t"], crop)
    if case["mode"] in ("projector", "browser"):
        jpeg = projector_photo(frame, rng, browser=case["mode"] == "browser")
    else:
        jpeg = phone_photo(frame, rng)
    jpeg = upload(jpeg, upload_px)
    t0 = time.time()
    r = search_example.lookup(jpeg, index=index)
    ids = [t["talk_id"] for t in r["talks"]]
    top = r["talks"][0]["moments"][0] if r["talks"] else None
    by_total = max(r["talks"], key=lambda t: t["total"])["talk_id"] if r["talks"] else None
    result = {**case, "talk1": ids[:1] == [case["talk_id"]], "talk3": case["talk_id"] in ids[:3],
              # the ranking this replaced (sum of every hit's score), among the same three talks
              "talk1_total": by_total == case["talk_id"], "degraded": r["degraded"],
              "fp_sure": r["fingerprint"]["sure"], "fp_cosine": r["fingerprint"]["cosine"],
              "moment": bool(top) and ids[0] == case["talk_id"] and top["start_sec"] <= case["t"] < top["end_sec"],
              # an adjacent slide still lands the viewer close: within 30 s either side of that slide
              "moment_near": bool(top) and ids[0] == case["talk_id"]
              and top["start_sec"] - 30 <= case["t"] < top["end_sec"] + 30,
              "got": ids[:3], "found": r["found"], "ocr_words": len(r["ocr_text"].split()),
              "total_s": round(time.time() - t0, 2)}
    if save_dir and not result["talk1"]:
        Path(save_dir).mkdir(parents=True, exist_ok=True)
        (Path(save_dir) / f"{case['i']:04d}-{case['mode']}-{case['talk_id']}.jpg").write_bytes(jpeg)
    return result


def summarize(results):
    def block(rs):
        lat = sorted(r["total_s"] for r in rs)
        return {"n": len(rs),
                "talk@1": round(sum(r["talk1"] for r in rs) / len(rs), 3),
                "talk@3": round(sum(r["talk3"] for r in rs) / len(rs), 3),
                "talk@1_by_total": round(sum(r["talk1_total"] for r in rs) / len(rs), 3),
                "degraded": sum(r["degraded"] for r in rs),  # lookups that fell back to keywords only (HTTP 429)
                # how often the pixel fingerprint decided alone, and how often it was right when it did
                "fp_sure": sum(r["fp_sure"] for r in rs),
                "fp_sure_right": sum(r["fp_sure"] and r["talk1"] for r in rs),
                "moment": round(sum(r["moment"] for r in rs) / len(rs), 3),
                "moment±30s": round(sum(r["moment_near"] for r in rs) / len(rs), 3),
                "p50_s": round(statistics.median(lat), 2),
                "p95_s": round(lat[min(len(lat) - 1, math.ceil(0.95 * len(lat)) - 1)], 2)}
    out = {"all": block(results)}
    for key in ("mode", "layout", "found"):
        for v in sorted({r[key] for r in results}):
            out[f"{key}={v}"] = block([r for r in results if r[key] == v])
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--videos", required=True, help="directory of <id>/video.mp4")
    ap.add_argument("--derived", required=True, help="extract.py output: <id>/segments.json")
    ap.add_argument("--index", default=INDEX)
    ap.add_argument("--n", type=int, default=100)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--modes", default="screen,slide", help="comma-separated: screen, slide, projector, browser")
    ap.add_argument("--upload-px", type=int, default=1280, help="shrink photos to this first, as the app does; 0 = don't")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--save-failures", help="directory for the photos the lookup got wrong")
    ap.add_argument("--min-talk1", type=float, help="exit 1 if talk@1 is below this, for CI")
    a = ap.parse_args()

    rng = random.Random(a.seed)
    cases = list(sample(a.derived, a.n, rng, a.modes.split(",")))
    results, errors = [], 0
    with cf.ThreadPoolExecutor(a.workers) as ex:
        for f in cf.as_completed([ex.submit(run_one, a.index, a.videos, c, a.seed, a.save_failures, a.upload_px)
                                  for c in cases]):
            try:
                results.append(f.result())
            except Exception as e:  # count, keep going: one bad frame shouldn't sink the run
                errors += 1
                print(f"error: {e}", flush=True)
    summary = summarize(results)
    print(json.dumps({"index": a.index, "seed": a.seed, "errors": errors, **summary}, indent=1))
    for r in [r for r in results if not r["talk1"]][:15]:
        print(f"miss: {r['talk_id']} @{r['t']}s mode={r['mode']} layout={r['layout']} "
              f"found={r['found']} degraded={r['degraded']} ocr_words={r['ocr_words']} got={r['got']}")
    if a.min_talk1 is not None:
        talk1 = sum(r["talk1"] for r in results) / len(cases)  # a photo that errored counts as a miss
        if talk1 < a.min_talk1 or errors > 0.1 * len(cases):
            raise SystemExit(f"talk@1 {talk1:.3f} with {errors} errors: below {a.min_talk1}, or over 10% errors")


if __name__ == "__main__":
    main()
