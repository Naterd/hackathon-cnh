"""Smoke test for the FastAPI app. Run from app/: python ../tests/app_smoke.py"""

import sys

from fastapi.testclient import TestClient

sys.path.insert(0, ".")
from main import app  # noqa: E402

client = TestClient(app)
checks = [
    ("GET", "/healthz", None, 200),
    ("GET", "/readyz", None, 200),
    ("GET", "/", None, 200),
    ("GET", "/sw.js", None, 200),
    ("POST", "/api/lookup", b"", 400),
]
failed = 0
for method, path, body, want in checks:
    got = client.request(method, path, content=body).status_code
    ok = got == want
    failed += not ok
    print(f"{'ok  ' if ok else 'FAIL'} {method} {path}: {got} (want {want})")
sys.exit(1 if failed else 0)
