"""Claiming slots, running jobs, and giving slots back.

A job goes through three phases, and only the first and last touch shared
state:

    claim    short transaction: lock an idle slot + the oldest queued job,
             mark them busy/running, commit
    run      no transaction, no locks: build the disk image and run QEMU
             (mkfs has its own limit; JOB_TIMEOUT_SECONDS covers QEMU)
    release  short transaction: record the verdict, free the slot, commit

The run phase deliberately sits outside any transaction. Holding row locks
across a multi-second subprocess would make every other worker wait on us
(or, with SKIP LOCKED, make the slot look taken for the whole run anyway),
keep an InnoDB transaction open for seconds, and tie a DB connection to a
QEMU process. The cost is that "busy" is committed state: if a worker dies
mid-run nothing rolls it back, which is what reap_stale_jobs() is for.
"""
import logging
import time
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from . import runner
from .models import Job, Slot

log = logging.getLogger(__name__)


@dataclass
class Claim:
    slot_id: int
    job_id: int


def claim_next(race_window: float = 0.0) -> Claim | None:
    """Atomically take one idle slot and the oldest queued job.

    Lock order is always Slot, then Job (release and reap use the same
    order), reducing the risk of opposite application-level lock ordering.
    This is not a general guarantee against database deadlocks.

    SKIP LOCKED: if another worker is mid-claim on a row, we skip it
    instead of queueing behind it. A skipped idle slot is about to become
    busy anyway, and a skipped queued job is about to be taken, so waiting
    would only delay us to learn the row is gone.

    race_window sleeps between reading and writing. It exists only so the
    concurrency demo can hold claim_next() and claim_unsafe() to the same
    timing; here the sleep happens while we hold the locks, so it's safe.
    """
    with transaction.atomic():
        # SELECT ... WHERE status='idle' LIMIT 1 FOR UPDATE SKIP LOCKED
        slot = (
            Slot.objects.select_for_update(skip_locked=True)
            .filter(status=Slot.Status.IDLE)
            .order_by("id")
            .first()
        )
        if slot is None:
            return None
        # Locked too, or two workers holding different slots could both
        # pick the same job and run it twice.
        job = (
            Job.objects.select_for_update(skip_locked=True)
            .filter(status=Job.Status.QUEUED)
            .order_by("created_at", "id")
            .first()
        )
        if job is None:
            # Nothing to do; leaving the block commits no changes and drops
            # the slot lock.
            return None

        if race_window:
            time.sleep(race_window)

        slot.status = Slot.Status.BUSY
        slot.current_job = job
        slot.save(update_fields=["status", "current_job"])
        job.status = Job.Status.RUNNING
        job.slot = slot
        job.started_at = timezone.now()
        job.save(update_fields=["status", "slot", "started_at"])
    # Committed: from here on, other workers see the slot as busy.
    return Claim(slot.id, job.id)


def claim_unsafe(race_window: float = 0.0) -> Claim | None:
    """claim_next() without the row locks. For the concurrency demo ONLY.

    Still wrapped in transaction.atomic(), to show that a transaction by
    itself doesn't help: a plain SELECT is a non-locking read, so two
    workers can both see slot 1 as idle, both write "busy", and both
    believe they own it.
    """
    with transaction.atomic():
        slot = Slot.objects.filter(status=Slot.Status.IDLE).order_by("id").first()
        if slot is None:
            return None
        job = Job.objects.filter(status=Job.Status.QUEUED).order_by("created_at", "id").first()
        if job is None:
            return None

        if race_window:
            time.sleep(race_window)

        Slot.objects.filter(pk=slot.pk).update(status=Slot.Status.BUSY, current_job=job)
        Job.objects.filter(pk=job.pk).update(
            status=Job.Status.RUNNING, slot=slot, started_at=timezone.now()
        )
    return Claim(slot.id, job.id)


def release(claim: Claim, *, status: str, serial_log: str, exit_code: int | None, detail: str) -> None:
    """Record the verdict and free the slot, in one transaction.

    Both writes are conditional (UPDATE ... WHERE <still ours>). An UPDATE
    takes the row lock itself, so no separate SELECT FOR UPDATE is needed.
    The conditions matter if reap_stale_jobs() already gave up on this job:
    we must not overwrite its verdict or free a slot that has since been
    handed to another job.
    """
    with transaction.atomic():
        # Same lock order as claim_next(): Slot first, then Job.
        freed = Slot.objects.filter(pk=claim.slot_id, current_job_id=claim.job_id).update(
            status=Slot.Status.IDLE, current_job=None
        )
        updated = Job.objects.filter(pk=claim.job_id, status=Job.Status.RUNNING).update(
            status=status,
            serial_log=serial_log,
            exit_code=exit_code,
            detail=detail,
            finished_at=timezone.now(),
        )
    if not freed or not updated:
        log.warning(
            "job %s: slot %s was already reclaimed (slot freed=%s, job updated=%s)",
            claim.job_id, claim.slot_id, bool(freed), bool(updated),
        )


def execute(claim: Claim) -> str:
    """Run a claimed job and release its slot, whatever happens."""
    status, serial, exit_code, detail = Job.Status.FAILED, "", None, ""
    try:
        job = Job.objects.get(pk=claim.job_id)
        result = runner.run_elf(
            Path(settings.MYOS_REPO),
            settings.QEMU_BINARY,
            Path(job.uploaded_binary.path),
            settings.JOB_TIMEOUT_SECONDS,
        )
        v = result.verdict
        status, serial, exit_code, detail = v.status, result.serial_log, v.exit_code, v.detail
        log.info("job %s on slot %s: %s (%s, %.2fs)", claim.job_id, claim.slot_id, status, detail, result.duration)
    except runner.RunnerError as e:
        # mkfs failed, QEMU couldn't start, artifacts missing: the program
        # never ran. It's still a terminal state for the job, and the
        # detail says it was the broker's fault, not the program's.
        detail = f"broker error: {e}"
        log.error("job %s on slot %s: %s", claim.job_id, claim.slot_id, detail)
    except Exception as e:
        detail = f"broker error: unexpected {type(e).__name__}: {e}"
        log.exception("job %s on slot %s crashed the worker", claim.job_id, claim.slot_id)
    finally:
        release(claim, status=status, serial_log=serial, exit_code=exit_code, detail=detail)
    return status


def reap_stale_jobs(older_than_seconds: float | None = None) -> int:
    """Fail old RUNNING jobs presumed to have lost their worker.

    This recovers DB bookkeeping only. It does not prove worker death or
    kill orphan QEMU processes; a paused/slow worker can outlive the cutoff.
    """
    if older_than_seconds is None:
        older_than_seconds = settings.STALE_JOB_SECONDS
    cutoff = timezone.now() - timedelta(seconds=older_than_seconds)
    reaped = 0
    with transaction.atomic():
        # Lock only the slot rows (OF self); without it MySQL would also
        # lock the joined job rows here, ahead of the Slot -> Job order.
        slots = list(
            Slot.objects.select_for_update(skip_locked=True, of=("self",)).filter(
                status=Slot.Status.BUSY,
                current_job__status=Job.Status.RUNNING,
                current_job__started_at__lt=cutoff,
            )
        )
        for slot in slots:
            Job.objects.filter(pk=slot.current_job_id, status=Job.Status.RUNNING).update(
                status=Job.Status.FAILED,
                detail=f"broker error: worker lost (running > {older_than_seconds:.0f}s)",
                finished_at=timezone.now(),
            )
            log.warning("reaped job %s, freeing slot %s", slot.current_job_id, slot.id)
            slot.status = Slot.Status.IDLE
            slot.current_job = None
            slot.save(update_fields=["status", "current_job"])
            reaped += 1
    return reaped
