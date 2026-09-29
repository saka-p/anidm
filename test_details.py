from anidm.backends.animepahe import AnimepaheBackend

backend = AnimepaheBackend()
session = "675f331e-606a-3734-e94d-6d9c6507d7e3"
data = backend._api_get(f"https://animepahe.pw/api?m=release&id={session}&sort=episode_asc&page=1")
print("RELEASE KEYS:", list(data.keys()))

from curl_cffi import requests

cf, ua = backend._ensure_clearance()
resp = requests.get(
    f"https://animepahe.pw/anime/{session}",
    cookies={"cf_clearance": cf},
    headers={"User-Agent": ua},
    impersonate="chrome",
    timeout=20,
)
html = resp.text
idx = html.find("Aired")
print(html[idx - 100:idx + 300])
