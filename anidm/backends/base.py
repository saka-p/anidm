from abc import ABC, abstractmethod
from dataclasses import dataclass

@dataclass
class Anime:
    slug: str
    title: str
    episodes: int
    year: int

@dataclass
class Episode:
    number: int
    session: str
    duration: str
    audio: str

class Backend(ABC):
    """A source of downloadable anime. Every source implements this same
    surface, so the GUI never needs to know which one it's talking to."""

    @abstractmethod
    def search(self, query: str) -> list[Anime]:
        """find anime matching a title query."""

    @abstractmethod
    def list_episodes(self, anime: Anime) -> list[Episode]:
        """List the episodes available for an anime"""

    @abstractmethod
    def download(self, anime: Anime, episode: Episode, dest_dir: str) -> str:
        """Download one episode into dest_dir. Return the finished file path."""
