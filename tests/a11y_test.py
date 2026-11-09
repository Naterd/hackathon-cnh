"""Screen-reader wiring on the app's pages stays in place. Run from app/:
python ../tests/a11y_test.py"""

import re
import sys
from pathlib import Path

html = Path("templates/index.html").read_text()
js = Path("static/app.js").read_text()
css = Path("static/style.css").read_text()
reference = (Path(__file__).resolve().parent.parent / "docs/search_example.py").read_text()
failed = 0


def check(ok, what):
    global failed
    failed += not ok
    print(f"{'ok  ' if ok else 'FAIL'} {what}")


views = ["capture", "scan", "match", "roll", "timeline"]
for v in views:
    m = re.search(rf'<h1[^>]*id="{v}-title"[^>]*>', html)
    check(m and 'tabindex="-1"' in m.group(0), f"view {v} has an h1 #{v}-title that can take focus")
check(html.count("<h1") == len(views), "exactly one h1 per view")
check("${name}-title`)?.focus(" in js, "show() moves focus to the new view's heading")
check(js.index("renderMatch(photo);") < js.index('show("match");'), "the match is rendered before it's shown, so focus lands on the title")
check('id="status" aria-live="polite"' in html and "function say(" in js, "the live region and say() exist")
check('say(`Found it:' in js and 'say("Finding the talk' in js, "the lookup's start and result are announced")
check('aria-busy="true"' in re.search(r'<section id="scan"[^>]*>', html).group(0), "the Finding view is aria-busy")
check("opens YouTube in a new tab" in html, "the recording link says it opens a new tab")
check("On the slide: ${moment.ocr_text}" in js, "the slide image's alt text is the slide's words")
check('id="said"' in html and "moment.said_text" in js, "what the speaker said is on the match page")
check('"ocr_text": s.get("ocr_text"' in reference and '"said_text": s.get("said_text"' in reference,
      "the reference lookup passes ocr_text and said_text through")
check("color: var(--color-neutral-600)" not in re.search(r"\.tabbar a \{[^}]*\}", css).group(0), "tab labels aren't the low-contrast grey")
check("prefers-reduced-motion" in css, "animations respect reduced motion")
sys.exit(1 if failed else 0)
