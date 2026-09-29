import json
import sys
from pathlib import Path
import urllib.request
import urllib.error

FLARESOLVERR_URL = "http://localhost:8191/v1"
TARGET_URL = "https://animepahe.pw"
CONFIG_PATH = Path.home() / "Software" / "animepahe-dl" / "config.json"
TIMEOUT_MS = 60000

def fetch_clearance():
    payload = json.dumps({
        "cmd": "request.get",
        "url": TARGET_URL,
        "maxTimeout": TIMEOUT_MS,
    }).encode()

    req = urllib.request.Request(
            FLARESOLVERR_URL,
            data=payload,
            headers={"Content-Type": "application/json"},
        )

    with urllib.request.urlopen(req, timeout=TIMEOUT_MS / 1000 + 15) as resp:
        data = json.load(resp)

        if data.get("status") != "ok":
            raise RuntimeError(f"FlareSolverr did not solve it: {data.get('message')}")

        solution = data["solution"]
        user_agent = solution["userAgent"]

        cf_clearance = None
        for cookie in solution ["cookies"]:
            if cookie["name"] == "cf_clearance":
                cf_clearance = cookie["value"]
                break
        if cf_clearance is None:
            raise RuntimeError("No cf_clearance cookie found in the response.")

        return cf_clearance, user_agent

def write_config(cf_clearance, user_agent):
    config = {
        "cf": cf_clearance,
        "ua": user_agent,
    }
    CONFIG_PATH.write_text(json.dumps(config, indent=2))
    print(f"Wrote credentials to {CONFIG_PATH}")


if __name__ == "__main__":
    cf, ua = fetch_clearance()
    write_config(cf, ua)


