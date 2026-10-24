import html
import json
import re
import time
from pathlib import Path

CATALOG_PATH = Path.home() / ".cache" / "ani-dm" / "catalog.json"
MAX_AGE = 24 * 3600


def parse_index(page_html):
    entries = []
    for session, title in re.findall(r'/anime/([a-z0-9-]+)"\s+title="([^"]+)"', page_html):
        entries.append({"session": session, "title": html.unescape(title)})
    return entries

_cache = None

def save(entries):
    global _cache
    CATALOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    data = {"updated": time.time(), "anime": entries}
    CATALOG_PATH.write_text(json.dumps(data))
    _cache = data


def load():
    global _cache
    if _cache is None:
        try:
            _cache = json.loads(CATALOG_PATH.read_text())
        except Exception:
            _cache = {"updated": 0, "anime": []}
    return _cache

def is_stale():
    data = load()
    return not data["anime"] or (time.time() - data.get("updated", 0)) > MAX_AGE


def _norm(s):
    return re.sub(r"[^a-z0-9]", "", s.lower())


def search(query, limit=60):
    q = _norm(query)
    if not q:
        return []
    results = []
    for item in load()["anime"]:
        n = _norm(item["title"])
        if q in n:
            rank = 0 if n == q else 1 if n.startswith(q) else 2
            results.append((rank, len(n), item))
    results.sort(key=lambda t: (t[0], t[1]))
    return [item for _, _, item in results[:limit]]
