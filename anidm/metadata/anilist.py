from dataclasses import dataclass

from curl_cffi import requests

API_URL = "https://graphql.anilist.co"

SEARCH_QUERY = """
query ($search: String) {
  Page(page: 1, perPage: 20) {
    media(search: $search, type: ANIME, sort: SEARCH_MATCH) {
      id
      title { romaji english }
      startDate { year }
      season
      seasonYear
      episodes
      averageScore
      genres
      coverImage { large extraLarge }
      format
      description
    }
  }
}
"""


@dataclass
class AnimeInfo:
    anilist_id: int
    title: str
    romaji: str
    english: str | None
    year: int | None
    season: str | None
    episodes: int | None
    score: int | None
    genres: list
    cover_url: str | None
    cover_url_large: str | None
    format: str | None
    description: str | None


class AniListClient:
    def search(self, query):
        resp = requests.post(
            API_URL,
            json={"query": SEARCH_QUERY, "variables": {"search": query}},
            impersonate="chrome",
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()

        results = []
        for m in data["data"]["Page"]["media"]:
            english = m["title"].get("english")
            romaji = m["title"].get("romaji")
            results.append(AnimeInfo(
                anilist_id=m["id"],
                title=english or romaji,
                romaji=romaji,
                english=english,
                year=(m["startDate"] or {}).get("year"),
                season=m.get("season"),
                episodes=m.get("episodes"),
                score=m.get("averageScore"),
                genres=m.get("genres") or [],
                cover_url=(m["coverImage"] or {}).get("large"),
                cover_url_large=(m["coverImage"] or {}).get("extraLarge"),
                format=m.get("format"),
                description=m.get("description"),
            ))
        return results
