"""
Convert Cookie-Editor JSON export → Playwright storage_state format.

Usage:
  1. Export Instagram cookies from Cookie-Editor as JSON
  2. Save the file anywhere (e.g. ~/Downloads/instagram_cookies.json)
  3. Run: python convert_cookies.py ~/Downloads/instagram_cookies.json
  4. Done — sessions/instagram.json is ready for the scraper
"""

import json
import sys
from pathlib import Path

SESSIONS_DIR = Path(__file__).parent / "sessions"
SESSIONS_DIR.mkdir(exist_ok=True)
OUTPUT = SESSIONS_DIR / "instagram.json"

SAME_SITE_MAP = {
    "no_restriction": "None",
    "unspecified": "None",
    "lax": "Lax",
    "strict": "Strict",
    "none": "None",
}


def convert(input_path: str) -> None:
    raw = json.loads(Path(input_path).read_text())

    cookies = []
    for c in raw:
        cookies.append({
            "name": c["name"],
            "value": c["value"],
            "domain": c["domain"],
            "path": c.get("path", "/"),
            "expires": c.get("expirationDate", -1),
            "httpOnly": c.get("httpOnly", False),
            "secure": c.get("secure", False),
            "sameSite": SAME_SITE_MAP.get(
                str(c.get("sameSite", "")).lower(), "None"
            ),
        })

    state = {"cookies": cookies, "origins": []}
    OUTPUT.write_text(json.dumps(state, indent=2))
    print(f"✅ Saved {len(cookies)} cookies → {OUTPUT}")
    print("   You can now run the scraper — Instagram session is ready.")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python convert_cookies.py <path-to-cookie-editor-export.json>")
        sys.exit(1)
    convert(sys.argv[1])
