import time
from anidm.downloads import DownloadManager


class FakeBackend:
    def download(self, anime, episode, dest_dir):
        time.sleep(2)


class FakeEp:
    def __init__(self, number):
        self.number = number


class FakeAnime:
    slug = "test"
    title = "Test"


backend = FakeBackend()
anime = FakeAnime()
mgr = DownloadManager(backend, "/tmp")


def report():
    states = [(j.episode.number, j.status.value) for j in mgr._jobs]
    print("STATE:", states)


mgr.on_change = report

mgr.enqueue(anime, FakeEp(1))
mgr.enqueue(anime, FakeEp(2))

mgr._work.join()
print("all done")
