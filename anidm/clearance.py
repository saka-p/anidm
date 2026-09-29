import json
import urllib.request
import urllib.error
from pathlib import Path

FLARESOLVERR_URL = "http://localhost:8191/v1"
TARGET_URL = "https://animepahe.pw"
TIMEOUT_MS = 60000


def fetch_clearance(target_url=TARGET_URL):
    payload = json.dumps({
        "cmd": "request.get",
        "url": target_url,
        "maxTimeout": TIMEOUT_MS,
    }).encode()
    req = urllib.request.Request(
        FLARESOLVERR_URL,
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_MS / 1000 + 15) as resp:
            data = json.load(resp)
    except urllib.error.URLError as e:
        raise RuntimeError(
            f"Could not reach FlareSolverr at {FLARESOLVERR_URL} ({e}). "
            "Is the container running?"
        )

    if data.get("status") != "ok":
        raise RuntimeError(f"FlareSolverr did not solve the challenge: {data.get('message')}")

    solution = data["solution"]
    user_agent = solution["userAgent"]
    cf = next(
        (c["value"] for c in solution["cookies"] if c["name"] == "cf_clearance"),
        None,
    )
    if not cf:
        raise RuntimeError("No cf_clearance cookie found in the FlareSolverr response.")
    return cf, user_agent


def refresh_config(config_path):
    cf, ua = fetch_clearance()
    config_path = Path(config_path)
    config_path.write_text(json.dumps({"cf": cf, "ua": ua}, indent=2) + "\n")
    return cf, ua
