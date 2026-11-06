"""Create reusable k6 sessions without burning the login rate limiter.

The login limiter is 5/minute per IP and 10/hour per account. A ramp of several
load runs would exhaust it, so log in once, write the cookies to
``load/sessions.json``, and let ``load/k6.js`` reuse them:

    backend/.venv/Scripts/python.exe scripts/k6_sessions.py
    docker run --rm -v "${PWD}/load:/scripts" \
      -e BASE_URL=http://api:8000 -e SESSIONS_FILE=sessions.json \
      --network lms_default grafana/k6 run /scripts/k6.js

Access tokens live for ACCESS_TOKEN_TTL_MIN (15 minutes by default), so
regenerate the file if a run reports 401s.

The login limiter is Redis-backed when ``REDIS_URL`` is set, so counters are
shared across API workers and survive a restart. If a run is rate limited,
delete the limiter keys instead of restarting the API:

    docker compose --env-file .env -f infra/docker-compose.yml exec -T redis \\
      sh -c "redis-cli --scan --pattern 'login:*' | xargs -r redis-cli del"
"""

from __future__ import annotations

import http.cookiejar
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE_URL = "http://localhost:8000"
EMAILS = [
    "student01@lms.example.com",
    "student03@lms.example.com",
    "student05@lms.example.com",
    "student06@lms.example.com",
    "student07@lms.example.com",
]
PASSWORD = "Passw0rd!"
OUT = Path(__file__).resolve().parent.parent / "load" / "sessions.json"


def login(email: str) -> tuple[str, str]:
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    body = json.dumps({"email": email, "password": PASSWORD}).encode()
    for attempt in range(6):
        request = urllib.request.Request(
            f"{BASE_URL}/api/v1/auth/login",
            data=body,
            headers={"Content-Type": "application/json"},
        )
        try:
            with opener.open(request, timeout=30) as response:
                response.read()
        except urllib.error.HTTPError as exc:
            if exc.code != 429:
                raise
            wait = int(exc.headers.get("Retry-After", "61"))
            print(f"{email}: rate limited, waiting {wait}s", flush=True)
            time.sleep(max(wait, 1) + 1)
            continue
        cookies = {cookie.name: cookie.value for cookie in jar}
        header = "; ".join(f"{name}={value}" for name, value in cookies.items())
        return header, cookies["lms_csrf"]
    raise SystemExit(f"{email}: still rate limited")


def main() -> None:
    sessions = []
    for email in EMAILS:
        cookie, csrf = login(email)
        sessions.append({"cookie": cookie, "csrf": csrf, "email": email})
        print(f"{email}: session ready", flush=True)
    OUT.write_text(
        json.dumps({"base_url": BASE_URL, "sessions": sessions}, indent=2),
        encoding="utf-8",
    )
    print(f"wrote {OUT}")


if __name__ == "__main__":
    sys.exit(main())
