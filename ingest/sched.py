"""Match our talks to the KubeCon schedule on Sched: slot, room, clean speakers and deck links.

Reads our talk titles from the index (talk docs, no semantic fields touched), the event's public
iCal feed, and the one page that lists every session with its speakers and uploaded decks.
Writes one JSON object keyed by video id; ingest/details.py loads it into kubecon-details.

    ELASTIC_URL=... ELASTIC_ES_API_KEY=... python ingest/sched.py --out sched.json
"""
import argparse
import datetime as dt
import difflib
import html
import json
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from zoneinfo import ZoneInfo

from index import ES

UA = {"User-Agent": "SlideKick-hackathon/1.0"}
LOCAL = ZoneInfo("America/Denver")  # Salt Lake City


def fetch(url, cache):
    f = Path(cache) / re.sub(r"\W+", "_", url)[-120:]
    for wait in (15, 45, None):
        if f.exists():
            return f.read_text(encoding="utf-8", errors="replace")
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60) as r:
                f.write_bytes(r.read())
            time.sleep(1)  # be polite: one page at a time, about one a second
        except urllib.error.HTTPError as e:
            if e.code not in (429, 503) or wait is None:
                raise  # Sched keeps refusing: stop rather than hammer it; cached pages resume next run
            time.sleep(wait)
    return f.read_text(encoding="utf-8", errors="replace")


def ical_events(text):
    text = re.sub(r"\r?\n[ \t]", "", text)  # unfold continuation lines
    for block in re.findall(r"BEGIN:VEVENT(.*?)END:VEVENT", text, re.S):
        ev = {}
        for line in block.strip().splitlines():
            key, _, value = line.partition(":")
            ev[key.split(";")[0]] = re.sub(r"\\([,;\\])", r"\1", value).replace("\\n", "\n").strip()
        yield ev


def local_time(stamp):
    return dt.datetime.strptime(stamp, "%Y%m%dT%H%M%SZ").replace(tzinfo=dt.timezone.utc).astimezone(LOCAL).isoformat()


def norm(s):
    return re.sub(r"[^a-z0-9]+", " ", html.unescape(s).lower()).strip()


def title_key(s):
    """The title without its speaker suffix. YouTube cuts long titles with "..." and lists panels as "- Panel"."""
    s = html.unescape(s)
    cut = bool(re.search(r"\.\.\.|\u2026", s))
    s = re.split(r"\.\.\.|\u2026", s)[0]
    head, sep, _ = s.rpartition(" - ")
    return norm(head if sep and not cut else s), cut


def best_event(title, events):
    key, cut = title_key(title)
    score = lambda e: difflib.SequenceMatcher(None, key, title_key(e["SUMMARY"])[0][:len(key)] if cut else title_key(e["SUMMARY"])[0]).ratio()
    e = max(events, key=score)
    return (e, score(e)) if score(e) >= 0.85 and len(key) >= 12 else (None, 0)


def session_page(page):
    speakers = []
    for block in re.findall(r'class="sched-person-session">(.*?)</div>\s*</div>\s*</div>', page, re.S):
        name = re.search(r'<h2><a [^>]*>(.*?)</a>', block, re.S)
        role = re.search(r'class="sched-event-details-role-company">(.*?)</div>', block, re.S)
        if not name:
            continue
        role = html.unescape(re.sub(r"<[^>]+>", "", role.group(1))).strip() if role else ""
        position, _, company = role.rpartition(",")  # "Chief Software Architect, Akuity"
        speakers.append({"name": html.unescape(name.group(1)).strip(),
                         "position": position.strip(), "company": (company or role).strip()})
    decks = [{"name": html.unescape(re.sub(r"\s+", " ", n)).strip(), "url": u}
             for u, n in re.findall(r'class="file-uploaded[^"]*" href="([^"]+)"[^>]*>(.*?)</a>', page, re.S)]
    return speakers, decks


def list_page(page):
    """Sched UID -> (speakers, decks) for every session on the descriptions list page."""
    out = {}
    for block in page.split('class="sched-container"')[1:]:
        uid = re.search(r'<a href="event/[^"]+" id="([0-9a-f]{32})"', block)
        if uid:
            out[uid.group(1)] = session_page(block)
    return out


def title_speakers(summary):
    """Speakers from the " - Name, Company & Name, Company" suffix, for sessions Sched lists without profiles."""
    _, sep, tail = html.unescape(summary).rpartition(" - ")
    people = []
    for part in re.split(r"\s*(?:&|;|\band\b)\s*", tail if sep and "|" not in tail else ""):
        name, _, company = part.partition(",")
        if re.fullmatch(r"(?:[A-Z][\w.'\u2019-]*\s+){1,3}[A-Z][\w.'\u2019-]*", name.strip()):
            people.append({"name": name.strip(), "position": "", "company": company.strip()})
    return people


def clean_title(summary, speakers):
    """Drop the " - Speaker, Company" suffix that Sched and YouTube append, but not a dash in the title itself."""
    head, sep, tail = summary.rpartition(" - ")
    if sep and any(s["name"].split()[0].lower() in tail.lower() for s in speakers):
        return head.strip()
    return summary.strip()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--event", default="kubecon-na-2024")
    ap.add_argument("--sched", default="https://kccncna2024.sched.com")
    ap.add_argument("--index", default="kubecon-moments")
    ap.add_argument("--cache", default="/tmp/sched-cache")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    Path(a.cache).mkdir(parents=True, exist_ok=True)

    status, r = ES().call("POST", f"/{a.index}/_search", {
        "size": 1000, "_source": ["talk_id", "title"],
        "query": {"bool": {"filter": [{"term": {"source": "talk"}}, {"term": {"event": a.event}}]}}})
    if status != 200:
        sys.exit(f"talks: HTTP {status} {str(r)[:200]}")
    talks = {h["_source"]["talk_id"]: h["_source"]["title"] for h in r["hits"]["hits"]}
    events = [e for e in ical_events(fetch(f"{a.sched}/all.ics", a.cache)) if e.get("SUMMARY")]
    by_title = {norm(e["SUMMARY"]): e for e in events}
    people = list_page(fetch(f"{a.sched}/list/descriptions/", a.cache))

    out, unmatched = {}, []
    for vid, title in sorted(talks.items()):
        best = difflib.get_close_matches(norm(title), list(by_title), n=1, cutoff=0.75)
        if best:
            e, match = by_title[best[0]], difflib.SequenceMatcher(None, norm(title), best[0]).ratio()
        else:
            e, match = best_event(title, events)
        if not e:
            unmatched.append(title)
            continue
        speakers, decks = people.get(e["UID"], ([], []))
        speakers = speakers or title_speakers(e["SUMMARY"])
        out[vid] = {"sched_url": e["URL"].replace("http://", "https://"),
                    "title_clean": clean_title(html.unescape(e["SUMMARY"]), speakers),
                    "starts_at": local_time(e["DTSTART"]), "ends_at": local_time(e["DTEND"]),
                    "room": e.get("LOCATION", "").split(", Salt Lake City")[0],
                    "track": e.get("CATEGORIES", ""), "speakers": speakers, "decks": decks,
                    "match": round(match, 3)}
    Path(a.out).write_text(json.dumps(out, indent=1, ensure_ascii=False))
    print(f"matched {len(out)}/{len(talks)}; with speakers {sum(bool(v['speakers']) for v in out.values())}; "
          f"with a deck {sum(bool(v['decks']) for v in out.values())}")
    for t in unmatched:
        print("unmatched:", t)


if __name__ == "__main__":
    main()
