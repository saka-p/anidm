from anidm.backends.animepahe import AnimepaheBackend
from anidm.backends.animepahe import AnimepaheBackend, LIBRARY_DIR

backend = AnimepaheBackend()
results = backend.search("Smoking behind the Supermarket with You")
first = results[0]
episodes = backend.list_episodes(first)

ep1 = episodes[7]
path = backend.download(first, ep1, str(LIBRARY_DIR))
print(f"\nDownloaded to: {path}")
