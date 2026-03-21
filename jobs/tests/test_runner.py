import tempfile
from pathlib import Path
from unittest import mock

from django.test import SimpleTestCase

from jobs import runner


class DiskSafetyTests(SimpleTestCase):
    def test_oversized_build_artifact_rejected_before_mkfs(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            for name in runner.REPO_ARTIFACTS:
                path = repo / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"x")
            for name in ("kernel.bin", "programs/shell.elf"):
                path = repo / name
                path.write_bytes(b"x" * (runner.SIMPLEFS_FILE_BYTES + 1))
                with self.assertRaises(runner.InvalidArtifactError):
                    runner.check_artifacts(repo)
                path.write_bytes(b"x")
            runner.check_artifacts(repo)

    def test_direct_runner_cannot_bypass_size_limit(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "input.elf"
            for size in (0, runner.SIMPLEFS_FILE_BYTES + 1):
                path.write_bytes(b"x" * size)
                with mock.patch.object(runner, "check_artifacts"), mock.patch.object(runner.subprocess, "run") as mkfs:
                    with self.assertRaises(runner.InvalidArtifactError):
                        runner.build_disk_image(Path(directory), path, Path(directory))
                    mkfs.assert_not_called()
            path.write_bytes(b"x" * runner.SIMPLEFS_FILE_BYTES)
            runner.check_file_size(path)
