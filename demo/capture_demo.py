#!/usr/bin/env python
"""Record a real broker session for the website demo.

Two acts, both against the demo database (myos_broker_demo, wiped here) and
media/demo/, never the application's own:

    queue   starts the Django web server and two `run_worker` processes,
            uploads the samples over HTTP, and records each upload's
            response, the workers' log lines, and each job's slot, times,
            verdict and serial log
    race    runs test_concurrency.py's rounds (real QEMU) in unsafe and
            locked mode, and the SQL each claim function sends to MySQL

The website replays this file; it never edits it.

    .venv/bin/python demo/capture_demo.py             # -> demo/transcript.json
    .venv/bin/python demo/capture_demo.py --rounds 5
"""
import argparse
import datetime
import json
import os
import re
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import test_concurrency as tc  # noqa: E402  (also points MYSQL_DATABASE at the demo database)

DEMO_MEDIA = ROOT / "media" / "demo"
SAMPLES = ROOT / "samples"
TRANSCRIPT = "demo/transcript.json"

# Uploaded in this order, SUBMIT_GAP apart, while both workers are already
# polling. Slot assignment and job ids depend on the order, so the site must
# replay it as recorded. (file, run the ELF header check)
UPLOADS = [
    ("hello.elf", True),
    ("producer_consumer.elf", True),
    ("fail.elf", True),
    ("crash.elf", True),
    ("spin.elf", True),
    ("corrupt.elf", False),     # ?validate=false: let MyOS's own loader reject it
    ("corrupt.elf", True),      # the header check rejects it: HTTP 400
    ("too_big.elf", True),      # the size check rejects it: HTTP 400
]
SUBMIT_GAP = 0.15
POLL = 0.05
WORKERS = 2
LOG_LINE = re.compile(r"^\S+ \S+ (?P<level>[A-Z]+) \[\d+\] (?P<logger>[\w.]+): (?P<message>.*)$")


def sh(*cmd, cwd=ROOT):
    return subprocess.run(cmd, cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def git_state(repo, exclude=None):
    spec = ["--", ".", f":!{exclude}"] if exclude else []
    commit = sh("git", "rev-parse", "--short", "HEAD", cwd=repo)
    dirty = bool(sh("git", "status", "--porcelain", "--untracked-files=no", *spec, cwd=repo))
    return commit, dirty


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def epoch(iso):
    return datetime.datetime.fromisoformat(iso).timestamp() if iso else None


def rel(t, t0):
    return None if t is None else round(t - t0, 3)


# ------------------------------------------------------------------ HTTP

class Api:
    def __init__(self, port):
        self.base = f"http://127.0.0.1:{port}"

    def get(self, path):
        with urllib.request.urlopen(self.base + path, timeout=10) as r:
            return json.load(r)

    def upload(self, path, file):
        boundary = uuid.uuid4().hex
        body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"binary\"; "
                f"filename=\"{file.name}\"\r\nContent-Type: application/octet-stream\r\n\r\n").encode()
        body += file.read_bytes() + f"\r\n--{boundary}--\r\n".encode()
        req = urllib.request.Request(self.base + path, data=body, method="POST",
                                     headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, json.load(r)
        except urllib.error.HTTPError as e:
            return e.code, json.load(e)


class Process:
    """A child process whose stderr lines are kept with their arrival time."""

    def __init__(self, name, args, env):
        self.name = name
        self.lines = []
        self.proc = subprocess.Popen(args, cwd=ROOT, env=env, stdin=subprocess.DEVNULL,
                                     stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
        self.reader = threading.Thread(target=self._pump, daemon=True)
        self.reader.start()

    def _pump(self):
        for line in self.proc.stderr:
            self.lines.append((time.time(), line.rstrip("\n")))

    def wait_for(self, text, timeout):
        end = time.time() + timeout
        while time.time() < end:
            if any(text in line for _, line in self.lines):
                return
            if self.proc.poll() is not None:
                break
            time.sleep(0.05)
        tail = "\n".join(line for _, line in self.lines[-20:])
        raise SystemExit(f"{self.name}: never printed {text!r}\n{tail}")

    def stop(self):
        if self.proc.poll() is None:
            self.proc.send_signal(signal.SIGTERM)  # workers finish their current job first
        try:
            self.proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait()
        self.reader.join(timeout=2)


# ------------------------------------------------------------------ reset

def reset(n_slots, n_jobs):
    """test_concurrency.reset(), with job ids starting again at 1 each time."""
    from django.db import connection

    tc_reset(n_slots, 0)
    with connection.cursor() as c:
        c.execute("ALTER TABLE jobs_job AUTO_INCREMENT = 1")
    tc_reset_jobs(n_jobs)


def tc_reset_jobs(n_jobs):
    from django.core.files import File

    from jobs.models import Job

    for i in range(n_jobs):
        path = tc.SAMPLES[i % len(tc.SAMPLES)]
        with path.open("rb") as f:
            Job.objects.create(uploaded_binary=File(f, name=path.name), original_name=path.name)


tc_reset = tc.reset
tc.reset = reset  # run_round() looks reset up in its module at call time


# ------------------------------------------------------------- act: queue

def capture_queue():
    from django.conf import settings

    reset(WORKERS, 0)
    env = {**os.environ, "MEDIA_ROOT": str(DEMO_MEDIA), "PYTHONUNBUFFERED": "1",
           "MYSQL_DATABASE": settings.DATABASES["default"]["NAME"]}
    port = free_port()
    python = sys.executable
    web = Process("web", [python, "manage.py", "runserver", f"127.0.0.1:{port}", "--noreload"], env)
    workers = [Process(f"W{i}", [python, "manage.py", "run_worker"], env) for i in range(1, WORKERS + 1)]
    api = Api(port)
    try:
        for w in workers:
            w.wait_for("started", 30)
        for _ in range(100):
            try:
                api.get("/slots")
                break
            except OSError:
                time.sleep(0.1)
        else:
            raise SystemExit("web server did not answer /slots")

        # The timeline comes from the jobs' own created/started/finished
        # times. Polling /jobs and /slots separately can catch a release
        # half-seen (the slot already idle, the job still running).
        submissions = []
        t0 = time.time()
        for name, validate in UPLOADS:
            path = "/jobs" if validate else "/jobs?validate=false"
            sent = time.time()
            status, body = api.upload(path, SAMPLES / name)
            submissions.append({"t": rel(sent, t0), "t_response": rel(time.time(), t0),
                                "file": name, "bytes": (SAMPLES / name).stat().st_size,
                                "request": f"POST {path}", "http_status": status, "response": body})
            print(f"  POST {path:22} {name:22} -> {status}", file=sys.stderr)
            time.sleep(SUBMIT_GAP)

        accepted = [s["response"]["id"] for s in submissions if s["http_status"] == 201]
        deadline = time.time() + 120
        while time.time() < deadline:
            listed = {j["id"]: j["status"] for j in api.get("/jobs")["jobs"]}
            if all(listed[i] not in ("queued", "running") for i in accepted):
                break
            time.sleep(POLL)
        else:
            raise SystemExit("jobs still unfinished after 120 s")

        jobs = []
        for i in accepted:
            j = api.get(f"/jobs/{i}")
            jobs.append({**j, "t_created": rel(epoch(j["created_at"]), t0),
                         "t_started": rel(epoch(j["started_at"]), t0),
                         "t_finished": rel(epoch(j["finished_at"]), t0)})
    finally:
        for w in workers:
            w.stop()
        web.stop()

    host = socket.gethostname()
    worker_log = []
    for w in workers:
        for t, line in w.lines:
            m = LOG_LINE.match(line)
            # The start and stop lines name this machine and the MyOS checkout path.
            if not m or host in m["message"] or t < t0:
                continue
            worker_log.append({"t": rel(t, t0), "worker": w.name, "level": m["level"],
                               "logger": m["logger"], "message": m["message"]})
    worker_log.sort(key=lambda e: e["t"])
    return {
        "config": {"workers": WORKERS, "slots": WORKERS,
                   "timeout_seconds": settings.JOB_TIMEOUT_SECONDS,
                   "max_binary_bytes": settings.MAX_BINARY_BYTES,
                   "worker_idle_poll_seconds": 0.5},
        "submissions": submissions,
        "worker_log": worker_log,
        "jobs": jobs,
    }


# -------------------------------------------------------------- act: race

def capture_sql():
    """The statements claim_next(), claim_unsafe() and release() send."""
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    from jobs import scheduler

    out = {}
    for name, claim in (("claim_next", scheduler.claim_next), ("claim_unsafe", scheduler.claim_unsafe)):
        reset(1, 1)
        with CaptureQueriesContext(connection) as q:
            c = claim()
        out[name] = [x["sql"] for x in q.captured_queries]
        with CaptureQueriesContext(connection) as q:
            scheduler.release(c, status="success", serial_log="", exit_code=0, detail="")
        out["release"] = [x["sql"] for x in q.captured_queries]
    return out


def capture_race(rounds):
    args = argparse.Namespace(workers=4, jobs=4, slots=2, race_window=0.05, fake_run=0.0,
                              deadline=60.0, rounds=rounds)
    modes = {}
    for mode in ("unsafe", "locked"):
        modes[mode] = []
        for n in range(1, rounds + 1):
            r = tc.run_round(mode, args)
            t0 = r.events[0][1] if r.events else 0
            print(f"  {mode} round {n}: peak {r.peak}, double-booked {r.double_booked_claims}, "
                  f"duplicate runs {r.duplicate_runs}, {r.executions}/{r.n_jobs} executions", file=sys.stderr)
            modes[mode].append({
                "round": n,
                "violated": bool(r.violated),
                "peak": r.peak,
                "over_capacity_moments": r.over_capacity_moments,
                "double_booked_claims": r.double_booked_claims,
                "duplicate_runs": r.duplicate_runs,
                "executions": r.executions,
                "db_max_busy_slots": r.db_max_busy_slots,
                "db_max_running_jobs": r.db_max_running_jobs,
                "events": [{"t": rel(t, t0), "kind": kind, "worker": w, "slot": slot, "job": job,
                            "status": status} for kind, t, w, slot, job, status in r.events],
                "timeline": r.timeline,
            })
    return {
        "config": {"workers": args.workers, "jobs": args.jobs, "slots": args.slots,
                   "race_window_seconds": args.race_window, "run": "real QEMU", "rounds": rounds},
        "modes": modes,
        "sql": capture_sql(),
    }


# ------------------------------------------------------------------ main

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=str(ROOT / TRANSCRIPT))
    ap.add_argument("--rounds", type=int, default=3, help="race rounds per mode (default 3)")
    args = ap.parse_args()

    tc.setup_django(quiet=True)
    from django.conf import settings
    from django.core.management import call_command
    from django.db import connection

    repo = Path(settings.MYOS_REPO)
    print(f"building MyOS in {repo} ...", file=sys.stderr)
    subprocess.run(["make", "disk.img"], cwd=repo, check=True, stdout=subprocess.DEVNULL)
    call_command("migrate", verbosity=0)

    print("act 1: queue", file=sys.stderr)
    queue = capture_queue()
    print("act 2: race", file=sys.stderr)
    race = capture_race(args.rounds)
    reset(2, 0)

    commit, dirty = git_state(ROOT, exclude=TRANSCRIPT)
    myos_commit, myos_dirty = git_state(repo)
    with connection.cursor() as c:
        c.execute("SELECT VERSION()")
        mysql_version = c.fetchone()[0]
    import django
    doc = {
        "captured_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "commit": commit,
        "dirty": dirty,
        "myos_commit": myos_commit,
        "myos_dirty": myos_dirty,
        "versions": {
            "python": sys.version.split()[0],
            "django": django.get_version(),
            "mysql": mysql_version,
            "qemu": sh(settings.QEMU_BINARY, "--version").splitlines()[0],
        },
        "queue": queue,
        "race": race,
    }
    with open(args.out, "w") as f:
        json.dump(doc, f, indent=1, ensure_ascii=False)
        f.write("\n")
    print(f"wrote {args.out}: {len(queue['jobs'])} jobs, {args.rounds} race rounds per mode, "
          f"commit {commit}{' (DIRTY)' if dirty else ''}, MyOS {myos_commit}"
          f"{' (DIRTY)' if myos_dirty else ''}", file=sys.stderr)


if __name__ == "__main__":
    main()
