import subprocess
import urllib.parse
import shutil
import threading
import re

from pathlib import Path
from curl_cffi import requests

from .base import Anime, Episode, Backend
from ..clearance import fetch_clearance, refresh_config
from .. import flaresolverr
from .. import meta

HOST = "https://animepahe.pw"
SCRIPT_DIR = Path(__file__).resolve().parent.parent.parent / "vendor" / "animepahe-dl"
SCRIPT = SCRIPT_DIR / "animepahe-dl.sh"
CONFIG_PATH = SCRIPT_DIR / "config.json"
LIBRARY_DIR = Path.home() / "Videos" / "ani-dm"


def _safe(name):
    cleaned = name.replace("/", "-").replace("\\", "-")
    cleaned = re.sub(r"[\x00-\x1f]", "", cleaned)
    return cleaned.strip().rstrip(".") or "Untitled"


def _ep_tag(number):
    try:
        return f"E{int(number):02d}"
    except (TypeError, ValueError):
        return f"E{number}"


def _season_for(mtype):
    if (mtype or "").lower() in ("ova", "special", "music"):
        return 0
    return 1


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
        if not flaresolverr.wait_until_ready():
            print("WARM: flaresolverr never became reachable")
            return
        for attempt in (1, 2):
            try:
                self._ensure_clearance(force=True)
                print("WARM: cookie minted successfully")
                return
            except Exception as e:
                print(f"WARM: attempt {attempt} failed - {e}")
        print("WARM: gave up")

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

    def fetch_index(self):
        for attempt in (1, 2):
            cf, ua = self._ensure_clearance(force=(attempt == 2))
            resp = requests.get(
                f"{HOST}/anime",
                cookies={"cf_clearance": cf},
                headers={"User-Agent": ua},
                impersonate="chrome",
                timeout=30,
            )
            if resp.status_code == 200:
                return resp.text
        raise RuntimeError("Failed to fetch animepahe index after refreshing clearance.")

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

    def download(self, anime, episode, dest_dir, on_progress=None, on_proc=None):
        refresh_config(CONFIG_PATH)
        proc = subprocess.Popen(
            [str(SCRIPT), "-s", anime.slug, "-e", str(episode.number)],
            cwd=str(SCRIPT_DIR),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            start_new_session=True,
        )
        if on_proc:
            on_proc(proc)


        tail = []
        for line in proc.stdout:
            line = line.strip()
            if line.startswith("ANIDM"):
                parts = line[len("ANIDM"):].strip().split("|")
                if len(parts) == 3:
                    pct = parts[0].strip().rstrip("%")
                    try:
                        percent = float(pct)
                    except ValueError:
                        continue
                    if on_progress:
                        on_progress(percent, parts[1].strip(), parts[2].strip())
            elif line:
                tail.append(line)
                tail = tail[-8:]

        proc.wait()
        if proc.returncode != 0:
            detail = "\n".join(tail[-5:])
            raise RuntimeError(
                f"Download failed (exit {proc.returncode}) for episode "
                f"{episode.number}:\n{detail}"
            )

        matches = list(SCRIPT_DIR.glob(f"*/{episode.number}.mp4"))
        if not matches:
            raise RuntimeError(
                f"Script ran but no {episode.number}.mp4 was found under {SCRIPT_DIR}."
            )
        produced = max(matches, key=lambda p: p.stat().st_mtime)

        final = self.library_path(anime, episode, dest_dir)
        final.parent.mkdir(parents=True, exist_ok=True)

        source_folder = produced.parent
        shutil.move(str(produced), str(final))
        shutil.rmtree(source_folder, ignore_errors=True)
        return str(final) 
    
    @staticmethod
    def library_root(anime, dest_dir):
        m = meta.get_by_title(anime.title)
        safe = _safe(anime.title)
        dest = Path(dest_dir)
        if (m.get("type") or "").lower() == "movie":
            name = f"{safe} ({m['year']})" if m.get("year") else safe
            return dest / "Movies" / name
        return dest / "Shows" / safe

    @staticmethod
    def library_path(anime, episode, dest_dir):
        m = meta.get_by_title(anime.title)
        root = AnimepaheBackend.library_root(anime, dest_dir)
        if (m.get("type") or "").lower() == "movie":
            return root / f"{root.name}.mp4"
        season = _season_for(m.get("type"))
        return (root / f"Season {season:02d}"
                / f"{_safe(anime.title)} S{season:02d}{_ep_tag(episode.number)}.mp4")
