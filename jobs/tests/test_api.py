import shutil
import struct
import tempfile

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings

from jobs.models import Job

TMP_MEDIA = tempfile.mkdtemp(prefix="broker-test-media-")


def elf_header(machine=3, elf_class=1):
    head = bytearray(52)
    head[:4] = b"\x7fELF"
    head[4] = elf_class
    head[5] = 1  # little-endian
    struct.pack_into("<HH", head, 16, 2, machine)  # ET_EXEC, e_machine
    return bytes(head)


def upload(data, name="prog.elf"):
    return SimpleUploadedFile(name, data, content_type="application/octet-stream")


@override_settings(MEDIA_ROOT=TMP_MEDIA)
class CreateJobTests(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(TMP_MEDIA, ignore_errors=True)

    def post(self, data, query=""):
        return self.client.post(f"/jobs{query}", {"binary": upload(data)})

    def test_valid_elf_is_queued(self):
        r = self.post(elf_header() + b"\0" * 100)
        self.assertEqual(r.status_code, 201)
        self.assertEqual(r.json()["status"], "queued")
        self.assertEqual(Job.objects.get().status, Job.Status.QUEUED)

    def test_exactly_the_limit_is_accepted(self):
        r = self.post(elf_header() + b"\0" * (48 * 512 - 52))
        self.assertEqual(r.status_code, 201)

    def test_one_byte_over_the_limit_is_rejected(self):
        r = self.post(elf_header() + b"\0" * (48 * 512 - 52 + 1))
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json()["error"]["code"], "file_too_large")
        self.assertFalse(Job.objects.exists())

    def test_size_limit_applies_even_without_validation(self):
        r = self.post(b"x" * (48 * 512 + 1), query="?validate=false")
        self.assertEqual(r.json()["error"]["code"], "file_too_large")

    def test_non_elf_is_rejected(self):
        r = self.post(b"#!/bin/sh\necho hi\n" + b"\0" * 60)
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json()["error"]["code"], "not_elf")

    def test_non_elf_accepted_when_validation_skipped(self):
        r = self.post(b"\x7fXLF" + b"\0" * 60, query="?validate=false")
        self.assertEqual(r.status_code, 201)

    def test_wrong_arch_and_class(self):
        self.assertEqual(self.post(elf_header(machine=62)).json()["error"]["code"], "wrong_arch")
        self.assertEqual(self.post(elf_header(elf_class=2)).json()["error"]["code"], "wrong_elf_class")

    def test_empty_and_missing_file(self):
        self.assertEqual(self.post(b"").json()["error"]["code"], "empty_file")
        r = self.client.post("/jobs")
        self.assertEqual((r.status_code, r.json()["error"]["code"]), (400, "missing_file"))


@override_settings(MEDIA_ROOT=TMP_MEDIA)
class ReadJobTests(TestCase):
    def setUp(self):
        self.done = Job.objects.create(uploaded_binary=upload(elf_header()), status="success",
                                       serial_log="hello", exit_code=0)
        self.queued = Job.objects.create(uploaded_binary=upload(elf_header()))

    def test_detail_includes_serial_log(self):
        body = self.client.get(f"/jobs/{self.done.id}").json()
        self.assertEqual((body["status"], body["serial_log"], body["exit_code"]), ("success", "hello", 0))

    def test_missing_job_is_json_404(self):
        r = self.client.get("/jobs/999999")
        self.assertEqual((r.status_code, r.json()["error"]["code"]), (404, "not_found"))

    def test_list_filters_by_status(self):
        ids = lambda q: [j["id"] for j in self.client.get(f"/jobs{q}").json()["jobs"]]
        self.assertEqual(sorted(ids("")), sorted([self.done.id, self.queued.id]))
        self.assertEqual(ids("?status=queued"), [self.queued.id])
        self.assertEqual(sorted(ids("?status=queued,success")), sorted([self.done.id, self.queued.id]))
        self.assertNotIn("serial_log", self.client.get("/jobs").json()["jobs"][0])

    def test_unknown_status_filter_is_400(self):
        r = self.client.get("/jobs?status=queued,bogus")
        self.assertEqual((r.status_code, r.json()["error"]["code"]), (400, "bad_status"))

    def test_dashboard_and_slot_snapshot(self):
        from jobs.models import Slot

        response = self.client.get("/")
        self.assertContains(response, "myOS Run Lab")
        self.assertContains(response, 'data-max-bytes="24576"')
        slot = Slot.objects.first()
        slot.status = Slot.Status.BUSY
        slot.current_job = self.queued
        slot.save()
        snapshot = self.client.get("/slots").json()["slots"]
        self.assertEqual(snapshot[0], {"id": slot.pk, "status": "busy", "current_job_id": self.queued.pk})
        self.assertEqual(self.client.post("/slots").status_code, 405)
