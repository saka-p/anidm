from anidm.backends.animepahe import AnimepaheBackend

backend = AnimepaheBackend()
backend.warm_clearance()

anime = backend.search("frieren")[0]
ep = backend.list_episodes(anime)[0]


def show(percent, speed, eta):
    print(f"{percent:5.1f}% | {speed} | ETA {eta}")


path = backend.download(anime, ep, "/tmp/anidm-test", on_progress=show)
print("saved to", path)
