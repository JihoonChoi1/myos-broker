import logging
import os
import signal
import socket
import time
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import close_old_connections

from jobs import runner, scheduler
from jobs.models import Job

log = logging.getLogger("jobs.worker")


class Command(BaseCommand):
    help = "Take queued jobs, run each on MyOS under QEMU, and record the result."

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true",
                            help="exit when no queued jobs remain instead of polling forever")
        parser.add_argument("--poll", type=float, default=0.5,
                            help="seconds to sleep when there is nothing to claim (default 0.5)")

    def handle(self, *args, once, poll, **options):
        try:
            runner.check_artifacts(Path(settings.MYOS_REPO))
        except runner.RunnerError as e:
            raise CommandError(str(e))

        name = f"{socket.gethostname()}:{os.getpid()}"
        stopping = False

        def stop(signum, frame):
            nonlocal stopping
            stopping = True
            log.info("worker %s: signal %s, finishing current job then exiting", name, signum)

        signal.signal(signal.SIGINT, stop)
        signal.signal(signal.SIGTERM, stop)

        log.info("worker %s started (repo=%s, timeout=%.1fs)", name, settings.MYOS_REPO,
                 settings.JOB_TIMEOUT_SECONDS)
        last_reap = 0.0
        while not stopping:
            # Long-running process: drop connections the server may have
            # timed out while we were sleeping.
            close_old_connections()

            if time.monotonic() - last_reap > 10:
                if n := scheduler.reap_stale_jobs():
                    log.warning("reaped %d stale job(s)", n)
                last_reap = time.monotonic()

            claim = scheduler.claim_next()
            if claim is None:
                if once and not Job.objects.filter(status=Job.Status.QUEUED).exists():
                    break
                time.sleep(poll)
                continue

            log.info("claimed job %s on slot %s", claim.job_id, claim.slot_id)
            scheduler.execute(claim)

        log.info("worker %s stopped", name)
