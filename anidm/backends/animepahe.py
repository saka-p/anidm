import subprocess
import urllib.parse
import shutil
import threading

from pathlib import Path
from curl_cffi import requests

from .base import Anime, Episode, Backend
from ..clearance import fetch_clearance, refresh_config

HOST = "https://animepahe.pw"
SCRIPT_DIR = Path(__file__).resolve().parent.parent.parent / "vendor" / "animepahe-dl"
SCRIPT = SCRIPT_DIR / "animepahe-dl.sh"
CONFIG_PATH = SCRIPT_DIR / "config.json"
LIBRARY_DIR = Path.home() / "Videos" / "ani-dm"


class AnimepaheBackend(Backend):
    def __init__(self):
        self._creds = None
        self._mint_lock = threading.Lock()

    def _ensure_clearance(self, force=False):
        if self._creds is not None and not force:
            return self._creds

        with self._mint_lock:
            if self._creds is not None and not force:
                return self._creds

            cf, ua = fetch_clearance()
            self._creds = (cf, ua)
            return self._creds

    def warm_clearance(self):
        import time
        for attempt in range(10):
            try:
                self._ensure_clearance(self)
                print("WARM: cookie minted successfully")
                return
            except Exception as e:
                print(f"WARM: attempt {attempt + 1} failed - {e}")
                time.sleep(3)
        print("WARM: gave up after retries")

    def _api_get(self, url):
        for attempt in (1, 2):
            cf, ua = self._ensure_clearance(force=(attempt == 2))

            resp = requests.get(
                url,
                cookies={"cf_clearance": cf},
                headers={"User-Agent": ua},
                impersonate="chrome",
                timeout=20,
            )
            if resp.status_code == 200:
                return resp.json()

        raise RuntimeError("animepahe request failed after refreshing clearance.")

    def _clean_query(self, q):
        return q.replace("-", " ").replace(":", " ")

    def search(self, query):
        query = self._clean_query(query)
        q = urllib.parse.quote(query)
        url = f"{HOST}/api?m=search&q={q}"
        payload = self._api_get(url)

        results = []
        for item in payload["data"]:
            results.append(Anime(
                slug=item["session"],
                title=item["title"],
                episodes=item["episodes"],
                year=item["year"],
            ))
        
        return results



    def list_episodes(self, anime):
        episodes = []
        page = 1
        while True:
            url = (f"{HOST}/api?m=release&id={anime.slug}"
                   f"&sort=episode_asc&page={page}")
            payload = self._api_get(url)

            for item in payload["data"]:
                episodes.append(Episode(
                    number=item["episode"],
                    session=item["session"],
                    duration=item["duration"],
                    audio=item["audio"],
                ))

            if page >= payload["last_page"]:
                break
            page += 1

        return episodes

    def download(self, anime, episode, dest_dir):
        refresh_config(CONFIG_PATH)
        subprocess.run(
            [str(SCRIPT), "-s", anime.slug, "-e", str(episode.number)],
            cwd=str(SCRIPT_DIR),
            check=True,
        )

        matches = list(SCRIPT_DIR.glob(f"*/{episode.number}.mp4"))
        if not matches:
            raise RuntimeError(
                f"Script ran but no {episode.number}.mp4 was found under {SCRIPT_DIR}."
            )
        produced = max(matches, key=lambda p: p.stat().st_mtime)

        safe_title = anime.title.replace(":", "").replace("/", "-")
        series_dir = Path(dest_dir) / safe_title
        series_dir.mkdir(parents=True, exist_ok=True)
        final = series_dir / f"{safe_title} - Ep {episode.number}.mp4"

        source_folder = produced.parent
        shutil.move(str(produced), str(final))
        shutil.rmtree(source_folder, ignore_errors=True)
        return str(final)
