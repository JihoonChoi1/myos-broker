"""Claim/release bookkeeping on a real MySQL test database.

Cross-process races are covered by test_concurrency.py; these check the
single-worker state transitions and the "still ours?" guards.
"""
import shutil
import tempfile
from datetime import timedelta
from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone

from jobs import runner, scheduler
from jobs.models import Job, Slot

TMP_MEDIA = tempfile.mkdtemp(prefix="broker-test-media-")


def make_job():
    return Job.objects.create(uploaded_binary=SimpleUploadedFile("p.elf", b"\x7fELF"))


@override_settings(MEDIA_ROOT=TMP_MEDIA)
class SchedulerTests(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(TMP_MEDIA, ignore_errors=True)

    def test_seeded_with_two_idle_slots(self):
        self.assertEqual(list(Slot.objects.values_list("status", flat=True)), ["idle", "idle"])

    def test_claims_fifo_and_never_more_than_slot_count(self):
        jobs = [make_job() for _ in range(3)]
        a, b = scheduler.claim_next(), scheduler.claim_next()
        self.assertEqual([a.job_id, b.job_id], [jobs[0].id, jobs[1].id])
        self.assertNotEqual(a.slot_id, b.slot_id)
        self.assertIsNone(scheduler.claim_next())  # both slots busy
        self.assertEqual(Job.objects.get(pk=jobs[2].id).status, "queued")

        job = Job.objects.get(pk=a.job_id)
        self.assertEqual((job.status, job.slot_id), ("running", a.slot_id))
        self.assertIsNotNone(job.started_at)
        self.assertEqual(Slot.objects.get(pk=a.slot_id).current_job_id, a.job_id)

    def test_no_queued_job_leaves_slots_idle(self):
        self.assertIsNone(scheduler.claim_next())
        self.assertFalse(Slot.objects.filter(status="busy").exists())

    def test_release_records_verdict_and_frees_slot(self):
        make_job()
        c = scheduler.claim_next()
        scheduler.release(c, status="crashed", serial_log="log", exit_code=None, detail="boom")
        job = Job.objects.get(pk=c.job_id)
        self.assertEqual((job.status, job.serial_log, job.detail), ("crashed", "log", "boom"))
        self.assertIsNotNone(job.finished_at)
        slot = Slot.objects.get(pk=c.slot_id)
        self.assertEqual((slot.status, slot.current_job_id), ("idle", None))

    def test_late_release_does_not_free_a_reassigned_slot(self):
        make_job()
        make_job()
        stale = scheduler.claim_next()
        # Reaper gives up on the job; its slot is handed to the next job.
        scheduler.reap_stale_jobs(older_than_seconds=-1)
        fresh = scheduler.claim_next()
        self.assertEqual(fresh.slot_id, stale.slot_id)

        scheduler.release(stale, status="success", serial_log="", exit_code=0, detail="")
        self.assertEqual(Slot.objects.get(pk=fresh.slot_id).current_job_id, fresh.job_id)
        self.assertEqual(Job.objects.get(pk=stale.job_id).status, "failed")  # reaper's verdict stands

    def test_reaper_only_touches_old_running_jobs(self):
        make_job()
        c = scheduler.claim_next()
        self.assertEqual(scheduler.reap_stale_jobs(older_than_seconds=60), 0)
        Job.objects.filter(pk=c.job_id).update(started_at=timezone.now() - timedelta(seconds=120))
        self.assertEqual(scheduler.reap_stale_jobs(older_than_seconds=60), 1)
        job = Job.objects.get(pk=c.job_id)
        self.assertEqual(job.status, "failed")
        self.assertIn("worker lost", job.detail)
        self.assertEqual(Slot.objects.get(pk=c.slot_id).status, "idle")

    def test_infrastructure_error_fails_job_and_frees_slot(self):
        make_job()
        c = scheduler.claim_next()
        with mock.patch.object(runner, "run_elf", side_effect=runner.MkfsError("mkfs exited 1")):
            self.assertEqual(scheduler.execute(c), "failed")
        job = Job.objects.get(pk=c.job_id)
        self.assertEqual(job.detail, "broker error: mkfs exited 1")
        self.assertEqual(Slot.objects.get(pk=c.slot_id).status, "idle")

    def test_qemu_missing_is_reported(self):
        make_job()
        c = scheduler.claim_next()
        with override_settings(QEMU_BINARY="/nonexistent/qemu"), \
                mock.patch.object(runner, "build_disk_image", return_value="/tmp/disk.img"):
            scheduler.execute(c)
        job = Job.objects.get(pk=c.job_id)
        self.assertEqual(job.status, "failed")
        self.assertIn("could not start /nonexistent/qemu", job.detail)
