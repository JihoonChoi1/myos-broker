import os
import subprocess
import sys
from pathlib import Path

from django.test import SimpleTestCase


class DemoIsolationTests(SimpleTestCase):
    def test_demo_does_not_inherit_application_database(self):
        root = Path(__file__).resolve().parents[2]
        env = {**os.environ, "MYSQL_DATABASE": "production_jobs"}
        env.pop("MYSQL_DEMO_DATABASE", None)
        result = subprocess.run(
            [sys.executable, "-c", "import test_concurrency, os; print(os.environ['MYSQL_DATABASE'])"],
            cwd=root, env=env, capture_output=True, text=True, check=True,
        )
        self.assertEqual(result.stdout.strip(), "myos_broker_demo")

    def test_non_demo_database_is_rejected_before_migration(self):
        root = Path(__file__).resolve().parents[2]
        result = subprocess.run(
            [sys.executable, "test_concurrency.py", "--fake-run", "0.01"],
            cwd=root, env={**os.environ, "MYSQL_DEMO_DATABASE": "production_jobs"},
            capture_output=True, text=True,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("refusing to reset a non-demo database", result.stderr)
