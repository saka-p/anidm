import json
import re
from pathlib import Path

META_PATH = Path(__file__).resolve().parent / "data" / "metadata.json"
_by_session = None
_by_title = None


def _norm(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _load():
    global _by_session, _by_title
    if _by_session is None:
        try:
            _by_session = json.loads(META_PATH.read_text())
        except Exception:
            _by_session = {}
        _by_title = {}
        for entry in _by_session.values():
            t = _norm(entry.get("title"))
            if t:
                _by_title.setdefault(t, entry)
    return _by_session, _by_title


def get(session):
    return _load()[0].get(session) or {}


def get_by_title(title):
    return _load()[1].get(_norm(title)) or {}
