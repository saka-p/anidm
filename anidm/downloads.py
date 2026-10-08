import os
import signal
import threading
from dataclasses import dataclass
from enum import Enum


class Status(Enum):
    QUEUED = "queued"
    DOWNLOADING = "downloading"
    DONE = "done"
    ERROR = "error"


@dataclass
class DownloadJob:
    anime: object
    episode: object
    status: Status = Status.QUEUED
    percent: float = 0.0
    speed: str = ""
    eta: str = ""
    error: str = ""


class DownloadManager:
    def __init__(self, backend, library_dir, on_change=None):
        self.backend = backend
        self.library_dir = library_dir
        self.on_change = on_change
        self._jobs = []
        self._lock = threading.Lock()
        self._cond = threading.Condition(self._lock)
        self._paused = False
        self._current_job = None
        self._current_proc = None
        self._interrupt = None
        self._worker = threading.Thread(target=self._run, daemon=True)
        self._worker.start()

    def enqueue(self, anime, episode):
        job = DownloadJob(anime=anime, episode=episode)
        with self._cond:
            self._jobs.append(job)
            self._cond.notify()
        self._notify()
        return job

    def pause(self):
        with self._cond:
            if self._paused:
                return
            self._paused = True
            if self._current_proc is not None:
                self._interrupt = "pause"
                self._kill_locked(self._current_proc)
        self._notify()

    def resume(self):
        with self._cond:
            if not self._paused:
                return
            self._paused = False
            self._cond.notify()
        self._notify()

    def stop(self):
        with self._cond:
            self._jobs = [j for j in self._jobs if j.status != Status.QUEUED]
            self._paused = False
            if self._current_proc is not None:
                self._interrupt = "stop"
                self._kill_locked(self._current_proc)
        self._notify()

    def is_paused(self):
        return self._paused

    def _kill_locked(self, proc):
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        except (ProcessLookupError, PermissionError, OSError):
            try:
                proc.terminate()
            except Exception:
                pass

    def _next_queued(self):
        for j in self._jobs:
            if j.status == Status.QUEUED:
                return j
        return None

    def _set_current_proc(self, proc):
        with self._cond:
            self._current_proc = proc

    def _run(self):
        while True:
            with self._cond:
                while self._paused or self._next_queued() is None:
                    self._cond.wait()
                job = self._next_queued()
                job.status = Status.DOWNLOADING
                job.percent = 0.0
                self._current_job = job
                self._interrupt = None
            self._notify()

            last_pct = -1

            def on_progress(percent, speed, eta):
                nonlocal last_pct
                job.percent = percent
                job.speed = speed
                job.eta = eta
                if int(percent) != last_pct:
                    last_pct = int(percent)
                    self._notify()

            error = None
            try:
                self.backend.download(job.anime, job.episode,
                                      str(self.library_dir),
                                      on_progress=on_progress,
                                      on_proc=self._set_current_proc)
            except Exception as e:
                error = e

            with self._cond:
                self._current_proc = None
                self._current_job = None
                interrupt = self._interrupt
                self._interrupt = None
                if interrupt == "pause":
                    job.status = Status.QUEUED
                    job.percent = 0.0
                elif interrupt == "stop":
                    if job in self._jobs:
                        self._jobs.remove(job)
                elif error is not None:
                    job.status = Status.ERROR
                    job.error = str(error)
                else:
                    job.status = Status.DONE
                    job.percent = 100.0
            self._notify()

    def _notify(self):
        if self.on_change:
            self.on_change()

    def current_job(self):
        with self._lock:
            return self._current_job

    def pending_count(self):
        with self._lock:
            return sum(1 for j in self._jobs
                       if j.status in (Status.QUEUED, Status.DOWNLOADING))

    def status_for(self, anime, episode):
        with self._lock:
            for j in self._jobs:
                if (j.anime.slug == anime.slug
                        and j.episode.number == episode.number):
                    return j.status
        return None
