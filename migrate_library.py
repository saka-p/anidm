import json
import re
import shutil
import sys
from pathlib import Path

from anidm import meta

LIBRARY_DIR = Path.home() / "Videos" / "ani-dm"
APPLY = "--apply" in sys.argv

EP_RE = re.compile(r"Ep\s*([0-9]+(?:\.[0-9]+)?)", re.IGNORECASE)
NUM_RE = re.compile(r"([0-9]+(?:\.[0-9]+)?)")


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


def library_root(title, dest):
    m = meta.get_by_title(title)
    safe = _safe(title)
    if (m.get("type") or "").lower() == "movie":
        name = f"{safe} ({m['year']})" if m.get("year") else safe
        return dest / "Movies" / name
    return dest / "Shows" / safe


def library_path(title, number, dest):
    m = meta.get_by_title(title)
    root = library_root(title, dest)
    if (m.get("type") or "").lower() == "movie":
        return root / f"{root.name}.mp4"
    season = _season_for(m.get("type"))
    return root / f"Season {season:02d}" / f"{_safe(title)} S{season:02d}{_ep_tag(number)}.mp4"


def read_title(folder):
    try:
        data = json.loads((folder / ".anidm.json").read_text())
        if data.get("title"):
            return data["title"]
    except Exception:
        pass
    return folder.name


def ep_number(filename, index):
    m = EP_RE.search(filename)
    if m:
        return m.group(1)
    nums = NUM_RE.findall(filename)
    return nums[-1] if nums else str(index + 1)


def main():
    root = LIBRARY_DIR
    if not root.is_dir():
        print(f"No library at {root}")
        return

    moves = []
    for folder in sorted(root.iterdir()):
        if not folder.is_dir() or folder.name in ("Movies", "Shows"):
            continue
        mp4s = sorted(folder.glob("*.mp4"))
        if not mp4s:
            continue
        title = read_title(folder)
        for i, src in enumerate(mp4s):
            moves.append((src, library_path(title, ep_number(src.name, i), root)))
        sidecar = folder / ".anidm.json"
        if sidecar.exists():
            moves.append((sidecar, library_root(title, root) / ".anidm.json"))

    if not moves:
        print("Nothing to migrate.")
        return

    for src, dest in moves:
        print(f"{src.relative_to(root)}  ->  {dest.relative_to(root)}")

    if not APPLY:
        print(f"\nDRY RUN — {len(moves)} items. Re-run with --apply to move them.")
        return

    for src, dest in moves:
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists():
            print(f"SKIP (exists): {dest.relative_to(root)}")
            continue
        shutil.move(str(src), str(dest))

    for folder in sorted(root.iterdir()):
        if folder.is_dir() and folder.name not in ("Movies", "Shows"):
            try:
                folder.rmdir()
            except OSError:
                pass

    print(f"\nMigrated {len(moves)} items.")


if __name__ == "__main__":
    main()
