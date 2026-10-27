import html as htmllib
import json
import re
import sys
import time
from pathlib import Path

from curl_cffi import requests

from anidm.backends.animepahe import AnimepaheBackend
from anidm import flaresolverr, catalog

HOST = "https://animepahe.pw"
ANILIST_API = "https://graphql.anilist.co"
OUT = Path("metadata.json")

backend = AnimepaheBackend()


def fetch_page(session):
    for attempt in (1, 2):
        cf, ua = backend._ensure_clearance(force=(attempt == 2))
        r = requests.get(
            f"{HOST}/anime/{session}",
            cookies={"cf_clearance": cf},
            headers={"User-Agent": ua},
            impersonate="chrome",
            timeout=25,
        )
        if r.status_code == 200:
            return r.text
    return None


def parse_page(page):
    def first(pat):
        m = re.search(pat, page, re.S)
        return m.group(1).strip() if m else None

    anilist = first(r'anilist\.co/anime/(\d+)')
    mal = first(r'myanimelist\.net/anime/(\d+)')
    poster = first(r'<meta property="og:image" content="([^"]+)"')
    syn = first(r'<div class="anime-synopsis">(.*?)</div>')
    if syn:
        syn = htmllib.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", syn))).strip()

    return {
        "anilist_id": int(anilist) if anilist else None,
        "mal_id": int(mal) if mal else None,
        "type": first(r'Type:\s*<a[^>]*>([^<]+)</a>'),
        "status": first(r'Status:\s*<a[^>]*>([^<]+)</a>'),
        "animepahe_poster": poster,
        "synopsis": syn,
    }


def anilist_meta(anilist_id):
    q = ("query($id:Int){Media(id:$id,type:ANIME){"
         "seasonYear startDate{year} episodes averageScore genres "
         "coverImage{extraLarge large}}}")
    for _ in range(4):
        try:
            r = requests.post(
                ANILIST_API,
                json={"query": q, "variables": {"id": anilist_id}},
                impersonate="chrome",
                timeout=15,
            )
            if r.status_code == 429:
                wait = int(r.headers.get("Retry-After", 60)) + 1
                print(f"     AniList rate-limited, waiting {wait}s...")
                time.sleep(wait)
                continue
            m = r.json()["data"]["Media"]
            cov = m.get("coverImage") or {}
            return {
                "cover_url": cov.get("extraLarge") or cov.get("large"),
                "year": m.get("seasonYear") or (m.get("startDate") or {}).get("year"),
                "episodes": m.get("episodes"),
                "score": m.get("averageScore"),
                "genres": m.get("genres") or [],
            }
        except Exception:
            return {}
    return {}


def main():
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else None
    flaresolverr.start()
    backend.warm_clearance()

    if catalog.is_stale():
        print("building catalog...")
        catalog.save(catalog.parse_index(backend.fetch_index()))

    anime = catalog.load()["anime"]
    if limit:
        anime = anime[:limit]

    try:
        out = json.loads(OUT.read_text())
    except Exception:
        out = {}

    for i, a in enumerate(anime, 1):
        s = a["session"]
        if s in out:
            continue
        page = fetch_page(s)
        if not page:
            print(f"[{i}/{len(anime)}] FAILED fetch: {a['title']}")
            continue
        meta = parse_page(page)
        extra = anilist_meta(meta["anilist_id"]) if meta["anilist_id"] else {}
        meta["cover_url"] = extra.get("cover_url")
        meta["year"] = extra.get("year")
        meta["episodes"] = extra.get("episodes")
        meta["score"] = extra.get("score")
        meta["genres"] = extra.get("genres", [])
        meta["title"] = a["title"]
        out[s] = meta
        OUT.write_text(json.dumps(out, indent=2, ensure_ascii=False))
        if i % 25 == 0 or i == len(anime):
            print(f"[{i}/{len(anime)}] {len(out)} saved  (latest: {a['title']})")
        time.sleep(1.0)

    print(f"\ndone — {len(out)} entries in {OUT.resolve()}")


if __name__ == "__main__":
    main()
