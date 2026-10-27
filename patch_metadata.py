import json
import time
from pathlib import Path

from curl_cffi import requests

OUT = Path("metadata.json")
API = "https://graphql.anilist.co"

d = json.loads(OUT.read_text())


def by_mal(mal):
    q = ("query($m:Int){Media(idMal:$m,type:ANIME){id seasonYear startDate{year} "
         "episodes averageScore genres coverImage{extraLarge large}}}")
    for _ in range(4):
        try:
            r = requests.post(API, json={"query": q, "variables": {"m": mal}},
                              impersonate="chrome", timeout=15)
        except Exception as e:
            print("   request error:", e)
            time.sleep(3)
            continue
        if r.status_code == 429:
            w = int(r.headers.get("Retry-After", 60)) + 1
            print("   rate-limited, waiting", w)
            time.sleep(w)
            continue
        m = (r.json().get("data") or {}).get("Media")
        if not m:
            return None
        cov = m.get("coverImage") or {}
        return {
            "anilist_id": m.get("id"),
            "cover_url": cov.get("extraLarge") or cov.get("large"),
            "year": m.get("seasonYear") or (m.get("startDate") or {}).get("year"),
            "episodes": m.get("episodes"),
            "score": m.get("averageScore"),
            "genres": m.get("genres") or [],
        }
    return None


todo = [k for k, v in d.items() if not v.get("cover_url") and v.get("mal_id")]
print("trying MAL->AniList for", len(todo), "entries")
fixed = 0
for k in todo:
    v = d[k]
    meta = by_mal(v["mal_id"])
    if meta and meta.get("cover_url"):
        v.update(meta)
        fixed += 1
        print("  fixed:", v["title"])
    OUT.write_text(json.dumps(d, indent=2, ensure_ascii=False))
    time.sleep(0.6)
print(f"\ndone: {fixed} recovered via MAL id")
