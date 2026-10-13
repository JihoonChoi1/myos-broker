"""The website demo's recording (demo/transcript.json) must agree with the code."""
import json
import unittest
from pathlib import Path

from django.test import SimpleTestCase

from jobs.runner import classify

TRANSCRIPT = Path(__file__).resolve().parents[2] / "demo" / "transcript.json"


@unittest.skipUnless(TRANSCRIPT.is_file(), "no demo transcript captured")
class TranscriptTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.doc = json.loads(TRANSCRIPT.read_text())

    def test_recorded_verdicts_match_classify(self):
        for job in self.doc["queue"]["jobs"]:
            with self.subTest(job=job["binary"]):
                verdict = classify(job["serial_log"])
                if job["status"] == "timeout":
                    # The runner, not classify(), decides a timeout.
                    self.assertIsNone(verdict)
                else:
                    self.assertEqual((verdict.status, verdict.exit_code), (job["status"], job["exit_code"]))

    def test_locked_rounds_never_violate(self):
        for r in self.doc["race"]["modes"]["locked"]:
            self.assertFalse(r["violated"])
            self.assertEqual(r["executions"], self.doc["race"]["config"]["jobs"])
