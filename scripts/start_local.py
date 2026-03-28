"""Start the existing local development stack; supervise only our children."""
import argparse
import errno
import fcntl
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser

ROOT = Path(__file__).resolve().parent.parent
CONTAINER = "myos-broker-mysql"
CHILDREN = []


def say(message):
    print(f"[myOS] {message}", flush=True)


def capture(*args):
    return subprocess.run(args, capture_output=True, text=True, timeout=10)


def start(name, *args):
    process = subprocess.Popen(args, cwd=ROOT, start_new_session=True)
    CHILDREN.append((name, process))
    return process


def step(name, *args):
    process = start(name, *args)
    code = process.wait()
    CHILDREN.remove((name, process))
    if code != 0:
        raise RuntimeError(f"{name} failed; see the output above.")


def ensure_docker():
    if not shutil.which("docker"):
        raise RuntimeError("Install Docker Desktop first (see README.md).")
    try:
        available = capture("docker", "info").returncode == 0
    except subprocess.TimeoutExpired:
        available = False
    if not available:
        if sys.platform != "darwin":
            raise RuntimeError("Start the Docker daemon, then run ./start.sh again.")
        say("Opening Docker Desktop; waiting up to 120 seconds...")
        if capture("open", "-a", "Docker").returncode:
            raise RuntimeError("Could not open Docker Desktop. Install/open it and retry.")
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            try:
                if capture("docker", "info").returncode == 0:
                    break
            except subprocess.TimeoutExpired:
                pass
            time.sleep(2)
        else:
            raise RuntimeError("Docker is not ready. Check Docker Desktop and retry.")
    state = capture("docker", "inspect", "--format", "{{.State.Running}}", CONTAINER)
    if state.returncode:
        raise RuntimeError(f"Cannot find/access {CONTAINER}. Complete MySQL setup in README.md.")
    if state.stdout.strip() != "true":
        say("Starting the existing MySQL container...")
        step("MySQL container startup", "docker", "start", CONTAINER)


def wait_for_database(connection):
    say("Waiting for MySQL (up to 90 seconds)...")
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
            connection.close()
            return
        except Exception:
            # Don't print connection exceptions: they may contain credentials.
            connection.close()
            time.sleep(1)
    raise RuntimeError("MySQL connection failed. Check the container and MYSQL_* settings.")


def check_children():
    for name, process in CHILDREN:
        if name.startswith(("Web", "Worker")) and process.poll() is not None:
            raise RuntimeError(f"{name} exited (code {process.returncode}); stopping this session.")


def stop_children(grace):
    say("Stopping web server and workers; waiting for active jobs to finish...")
    for name, process in CHILDREN:
        if process.poll() is None:
            try:
                if name.startswith("Worker"):
                    # Only signal the worker: its current mkfs/QEMU must finish normally.
                    process.terminate()
                else:
                    os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
    deadline = time.monotonic() + grace
    while any(p.poll() is None for _, p in CHILDREN) and time.monotonic() < deadline:
        time.sleep(0.1)
    for name, process in CHILDREN:
        # Also clean up descendants if a worker died unexpectedly.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait()
    say("Stopped. MySQL/Docker were left running; stored jobs and files are preserved.")


def interrupted(signum, frame):
    raise KeyboardInterrupt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--no-docker", action="store_true", help="use an already available MySQL server")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535 or args.workers < 1:
        parser.error("port must be 1..65535 and workers must be at least 1")

    os.chdir(ROOT)
    sys.path.insert(0, str(ROOT))
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "broker.settings")
    os.environ.setdefault("MYOS_REPO", str(ROOT.parent / "my-os"))
    os.environ["PYTHONUNBUFFERED"] = "1"
    (ROOT / ".local").mkdir(exist_ok=True)
    lock = (ROOT / ".local" / "start.lock").open("a")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        say("Already running via ./start.sh. Stop that terminal with Ctrl+C first.")
        return 1

    signal.signal(signal.SIGINT, interrupted)
    signal.signal(signal.SIGTERM, interrupted)
    grace = 45
    try:
        import django
        from django.conf import settings
        from django.db import connection
        from jobs.runner import check_artifacts

        django.setup()
        grace = max(45, settings.JOB_TIMEOUT_SECONDS + 40)
        check_artifacts(Path(settings.MYOS_REPO))
        if not os.access(Path(settings.MYOS_REPO) / "mkfs", os.X_OK):
            raise RuntimeError("myOS mkfs is not executable. Rebuild myOS for this host.")
        if not shutil.which(settings.QEMU_BINARY):
            raise RuntimeError("QEMU was not found. Install it or set QEMU_BINARY.")
        with socket.socket() as probe:
            try:
                probe.bind(("127.0.0.1", args.port))
            except OSError as exc:
                if exc.errno == errno.EADDRINUSE:
                    raise RuntimeError(f"Port {args.port} is unavailable. Try ./start.sh --port 8001.") from exc
                raise RuntimeError(f"Cannot listen on port {args.port}: {exc.strerror}") from exc
        if not args.no_docker:
            ensure_docker()
        connection.settings_dict["OPTIONS"]["connect_timeout"] = 2
        wait_for_database(connection)
        say("Applying pending database migrations...")
        step("Database migration", sys.executable, "manage.py", "migrate", "--noinput")
        step("Django checks", sys.executable, "manage.py", "check")
        start("Web server", sys.executable, "manage.py", "runserver", f"127.0.0.1:{args.port}")
        for number in range(args.workers):
            start(f"Worker {number + 1}", sys.executable, "manage.py", "run_worker")
        url = f"http://127.0.0.1:{args.port}/"
        deadline = time.monotonic() + 30
        # Bypass proxy environment settings when probing our local server.
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        while time.monotonic() < deadline:
            check_children()
            try:
                with opener.open(url + "slots", timeout=1) as response:
                    if response.status == 200:
                        break
            except (OSError, urllib.error.URLError):
                pass
            time.sleep(0.2)
        else:
            raise RuntimeError("Web server did not become ready within 30 seconds.")
        say(f"Ready: {url} | {args.workers} workers | Ctrl+C to stop")
        if not args.no_browser:
            try:
                webbrowser.open(url)
            except webbrowser.Error:
                say(f"Open {url} in your browser.")
        while True:
            check_children()
            time.sleep(0.5)
    except KeyboardInterrupt:
        return 0
    except Exception as exc:
        say(f"Startup/runtime error: {exc}")
        return 1
    finally:
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        if CHILDREN:
            stop_children(grace)
        lock.close()


if __name__ == "__main__":
    sys.exit(main())
