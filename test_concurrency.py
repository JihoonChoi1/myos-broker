#!/usr/bin/env python
"""Show why claiming a slot needs SELECT ... FOR UPDATE.

Starts several worker *processes* (not threads: separate processes share
nothing but the database, exactly like several `run_worker` instances), lets
them all race for the same 2 slots at the same instant, and records when each
one really had a MyOS VM running.

    unsafe  scheduler.claim_unsafe(): SELECT, then UPDATE, inside
            transaction.atomic() but with no row lock
    locked  scheduler.claim_next(): SELECT ... FOR UPDATE SKIP LOCKED

Both modes use the same --race-window: a sleep between reading the slot and
writing "busy". It widens a gap that exists anyway: with the workers
released together by a barrier, unsafe mode double-books even at
--race-window 0, because the SELECT -> UPDATE round trip alone is long
enough. In locked mode the sleep happens while holding the row lock, so it
can only slow things down, never break them.

The ground truth is the workers' own start/finish timestamps, not the Slot
table: with only 2 Slot rows the table can never show more than 2 busy even
while 4 VMs run on "the same" slot. A DB observer is printed alongside to
make that point.

Uses its own database (myos_broker_demo by default) and wipes its Job and
Slot tables each round.

    python test_concurrency.py                    # both modes, compared
    python test_concurrency.py --mode unsafe --rounds 5
    python test_concurrency.py --fake-run 0.5     # sleep instead of QEMU
"""
import argparse
import multiprocessing as mp
import os
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "broker.settings")
# Never inherit the application's database: reset() deletes every job.
os.environ["MYSQL_DATABASE"] = os.environ.get("MYSQL_DEMO_DATABASE", "myos_broker_demo")

SAMPLES = [BASE_DIR / "samples" / "hello.elf", BASE_DIR / "samples" / "producer_consumer.elf"]


def setup_django(quiet: bool):
    import logging

    import django

    django.setup()
    from django.conf import settings

    database = settings.DATABASES["default"]["NAME"]
    if not database.endswith("_demo"):
        raise SystemExit("MYSQL_DEMO_DATABASE must end with '_demo'; refusing to reset a non-demo database")
    settings.MEDIA_ROOT = settings.BASE_DIR / "media" / "demo"
    if quiet:
        # Worker processes: the timeline below is the output. The
        # scheduler's per-job INFO lines would just interleave with it.
        logging.getLogger("jobs").setLevel(logging.ERROR)


# ---------------------------------------------------------------- workers

def worker_main(idx, mode, race_window, fake_run, barrier, events, deadline):
    setup_django(quiet=True)
    from django.db import connections

    from jobs import scheduler
    from jobs.models import Job

    claim = scheduler.claim_unsafe if mode == "unsafe" else scheduler.claim_next
    name = f"W{idx}"
    try:
        barrier.wait()  # everyone reaches claim() at the same moment
        while time.time() < deadline:
            c = claim(race_window)
            if c is None:
                if not Job.objects.filter(status=Job.Status.QUEUED).exists():
                    break
                time.sleep(0.02)  # all slots taken; try again shortly
                continue
            events.put(("start", time.time(), name, c.slot_id, c.job_id, None))
            if fake_run:
                time.sleep(fake_run)
                scheduler.release(c, status="success", serial_log="(fake run)", exit_code=0,
                                  detail="fake run")
                status = "success"
            else:
                status = scheduler.execute(c)  # real disk image + QEMU
            events.put(("end", time.time(), name, c.slot_id, c.job_id, status))
    finally:
        connections.close_all()
        events.put(("exit", time.time(), name, None, None, None))


# ------------------------------------------------------------ DB observer

class Observer(threading.Thread):
    """Polls what the database *claims* is happening."""

    def __init__(self):
        super().__init__(daemon=True)
        self.stop = threading.Event()
        self.max_busy_slots = 0
        self.max_running_jobs = 0

    def run(self):
        from django.db import connection

        from jobs.models import Job, Slot

        while not self.stop.is_set():
            self.max_busy_slots = max(self.max_busy_slots, Slot.objects.filter(status="busy").count())
            self.max_running_jobs = max(self.max_running_jobs, Job.objects.filter(status="running").count())
            time.sleep(0.005)
        connection.close()


# ---------------------------------------------------------------- a round

def reset(n_slots: int, n_jobs: int):
    from django.core.files import File
    from django.db import transaction

    from jobs.models import Job, Slot

    with transaction.atomic():
        for job in Job.objects.all():
            job.uploaded_binary.delete(save=False)
        Slot.objects.update(current_job=None)
        Job.objects.all().delete()
        Slot.objects.all().delete()
        Slot.objects.bulk_create([Slot(pk=i, status="idle") for i in range(1, n_slots + 1)])
        for i in range(n_jobs):
            path = SAMPLES[i % len(SAMPLES)]
            with path.open("rb") as f:
                Job.objects.create(uploaded_binary=File(f, name=path.name), original_name=path.name)


@dataclass
class RoundResult:
    mode: str
    capacity: int
    n_jobs: int
    events: list
    peak: int = 0
    over_capacity_moments: int = 0
    double_booked_claims: int = 0
    duplicate_runs: int = 0
    executions: int = 0
    db_max_busy_slots: int = 0
    db_max_running_jobs: int = 0
    timeline: list = field(default_factory=list)

    @property
    def violated(self):
        return self.peak > self.capacity or self.double_booked_claims or self.duplicate_runs


def run_round(mode, args) -> RoundResult:
    from jobs.models import Job

    reset(args.slots, args.jobs)
    ctx = mp.get_context("spawn")
    barrier = ctx.Barrier(args.workers)
    events = ctx.Queue()
    deadline = time.time() + args.deadline
    procs = [
        ctx.Process(target=worker_main,
                    args=(i + 1, mode, args.race_window, args.fake_run, barrier, events, deadline))
        for i in range(args.workers)
    ]
    observer = Observer()
    observer.start()
    for p in procs:
        p.start()

    collected, exited = [], 0
    while exited < len(procs):
        ev = events.get(timeout=args.deadline + 30)
        if ev[0] == "exit":
            exited += 1
        else:
            collected.append(ev)
    for p in procs:
        p.join()
    observer.stop.set()
    observer.join()

    r = RoundResult(mode, args.slots, args.jobs, sorted(collected, key=lambda e: e[1]))
    r.db_max_busy_slots = observer.max_busy_slots
    r.db_max_running_jobs = observer.max_running_jobs
    analyse(r)
    leftover = Job.objects.filter(status__in=["queued", "running"]).count()
    if leftover:
        r.timeline.append(f"  !! {leftover} job(s) still queued/running at deadline")
    return r


def analyse(r: RoundResult):
    t0 = r.events[0][1] if r.events else 0
    running = {}        # (worker) -> (slot, job)
    ever_started = set()
    for kind, t, w, slot, job, status in r.events:
        if kind == "start":
            notes = []
            holders = [ow for ow, (s, _) in running.items() if s == slot]
            if holders:
                r.double_booked_claims += 1
                notes.append(f"slot {slot} already held by {','.join(holders)}")
            if job in ever_started:
                r.duplicate_runs += 1
                notes.append(f"job {job} already ran/running")
            ever_started.add(job)
            running[w] = (slot, job)
            r.executions += 1
            n = len(running)
            r.peak = max(r.peak, n)
            if n > r.capacity:
                r.over_capacity_moments += 1
                notes.insert(0, f"OVER CAPACITY {n} > {r.capacity}")
            flag = "  <-- " + "; ".join(notes) if notes else ""
            line = f"  +{t - t0:6.3f}s  {w}  START job {job} on slot {slot}   running={n}{flag}"
        else:
            running.pop(w, None)
            line = f"  +{t - t0:6.3f}s  {w}  END   job {job} on slot {slot}   running={len(running)}  ({status})"
        r.timeline.append(line)


# ---------------------------------------------------------------- output

def print_round(r: RoundResult, n: int, show_timeline: bool):
    title = {"unsafe": "UNSAFE: SELECT then UPDATE (no row lock)",
             "locked": "LOCKED: SELECT ... FOR UPDATE SKIP LOCKED"}[r.mode]
    print(f"\n=== {title}  [round {n}] ===")
    if show_timeline:
        print("\n".join(r.timeline))
    verdict = "VIOLATED" if r.violated else "OK"
    print(f"  -> peak concurrent VMs {r.peak} (capacity {r.capacity}), "
          f"double-booked slot claims {r.double_booked_claims}, "
          f"duplicate job runs {r.duplicate_runs}, "
          f"{r.executions} executions for {r.n_jobs} jobs   [{verdict}]")
    print(f"     DB said: max busy slots {r.db_max_busy_slots}, max running jobs {r.db_max_running_jobs}")


def print_comparison(results: dict):
    print("\n" + "=" * 78)
    print("SUMMARY")
    print("=" * 78)
    rows = [
        ("rounds with a violation", lambda rs: f"{sum(bool(r.violated) for r in rs)}/{len(rs)}"),
        (f"peak concurrent runs (capacity {next(iter(results.values()))[0].capacity})", lambda rs: str(max(r.peak for r in rs))),
        ("double-booked slot claims", lambda rs: str(sum(r.double_booked_claims for r in rs))),
        ("duplicate job runs", lambda rs: str(sum(r.duplicate_runs for r in rs))),
        ("executions / jobs submitted", lambda rs: f"{sum(r.executions for r in rs)}/{sum(r.n_jobs for r in rs)}"),
        ("DB max busy slots", lambda rs: str(max(r.db_max_busy_slots for r in rs))),
        ("DB max running jobs", lambda rs: str(max(r.db_max_running_jobs for r in rs))),
    ]
    modes = list(results)
    print(f"{'':36}" + "".join(f"{m:>18}" for m in modes))
    for label, fn in rows:
        print(f"{label:36}" + "".join(f"{fn(results[m]):>18}" for m in modes))
    if "unsafe" in results:
        capacity = results["unsafe"][0].capacity
        print(f"\nNote: the Slot table contains only {capacity} rows, so its busy count cannot")
        print("reveal runs that were double-booked onto the same slot. The table can't reveal the")
        print("race; only the workers' own timelines do.")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mode", choices=["unsafe", "locked", "both"], default="both")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--jobs", type=int, default=4)
    ap.add_argument("--slots", type=int, default=2)
    ap.add_argument("--rounds", type=int, default=1)
    ap.add_argument("--race-window", type=float, default=0.05,
                    help="seconds between reading a slot and marking it busy (default 0.05)")
    ap.add_argument("--fake-run", type=float, default=0.0,
                    help="sleep this long instead of running QEMU (default: real QEMU)")
    ap.add_argument("--deadline", type=float, default=60.0, help="per-round time limit in seconds")
    ap.add_argument("--quiet", action="store_true", help="summaries only, no timelines")
    args = ap.parse_args()
    if min(args.workers, args.jobs, args.slots, args.rounds) < 1:
        ap.error("workers, jobs, slots and rounds must be positive")
    if args.race_window < 0 or args.fake_run < 0 or args.deadline <= 0:
        ap.error("race-window and fake-run must be nonnegative; deadline must be positive")

    setup_django(quiet=True)
    from django.conf import settings
    from django.core.management import call_command

    call_command("migrate", verbosity=0)
    print(f"database {settings.DATABASES['default']['NAME']}, {args.workers} worker processes, "
          f"{args.jobs} jobs, {args.slots} slots, race window {args.race_window}s, "
          f"{'fake run ' + str(args.fake_run) + 's' if args.fake_run else 'real QEMU'}")

    modes = ["unsafe", "locked"] if args.mode == "both" else [args.mode]
    results = {m: [] for m in modes}
    for m in modes:
        for n in range(1, args.rounds + 1):
            r = run_round(m, args)
            results[m].append(r)
            print_round(r, n, show_timeline=not args.quiet and (n == 1 or r.violated and m == "locked"))
    print_comparison(results)

    # Exit nonzero if the locked version ever broke, so this can gate CI.
    if "locked" in results and any(r.violated for r in results["locked"]):
        sys.exit(1)


if __name__ == "__main__":
    main()
