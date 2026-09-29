from anidm.metadata.anilist import AniListClient

client = AniListClient()
for anime in client.search("Attack on Titan"):
    print(f"{anime.year}  {anime.title}  ({anime.episodes} eps)")
    print(f"    cover: {anime.cover_url}")
