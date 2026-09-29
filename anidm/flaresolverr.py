import subprocess

CONTAINER_NAME = "flaresolverr"
IMAGE = "ghcr.io/flaresolverr/flaresolverr:latest"
PORT = "8191"


def _run(args):
    return subprocess.run(["podman", *args], capture_output=True, text=True)


def _running():
    result = _run(["ps", "--filter", f"name={CONTAINER_NAME}", "--format", "{{.Names}}"])
    return CONTAINER_NAME in result.stdout.split()


def _exists():
    result = _run(["ps", "-a", "--filter", f"name={CONTAINER_NAME}", "--format", "{{.Names}}"])
    return CONTAINER_NAME in result.stdout.split()

def wait_until_ready(timeout=45):
    import time
    import urllib.request
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            urllib.request.urlopen("http://localhost:8191/", timeout=3)
            return True
        except Exception:
            time.sleep(2)
    return False

def start():
    if _running():
        return
    if _exists():
        _run(["start", CONTAINER_NAME])
    else:
        _run(["run", "-d", "--name", CONTAINER_NAME,
              "-p", f"{PORT}:{PORT}", IMAGE])


def stop():
    if _running():
        _run(["stop", CONTAINER_NAME])


