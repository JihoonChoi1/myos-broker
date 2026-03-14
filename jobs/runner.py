"""Run one ELF on MyOS under QEMU and classify the result.

Nothing in here touches the database, so the verdict logic can be tested
against captured serial logs without a VM or MySQL.
"""
import re
import shutil
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path

# The job ELF is written into the slot mkfs knows as hello.elf.
JOB_SLOT_NAME = "hello.elf"
RUN_COMMAND = f"exec {JOB_SLOT_NAME}\n"
SIMPLEFS_FILE_BYTES = 48 * 512

# Copied from the MyOS repo into every job dir. mkfs hardcodes these paths
# relative to its cwd.
REPO_ARTIFACTS = [
    "boot.bin",
    "loader.bin",
    "kernel.bin",
    "mkfs",
    "programs/shell.elf",
    "programs/fork_cow.elf",
    "programs/thread_test.elf",
    "programs/producer_consumer.elf",
]

SHELL_READY = "Type 'help' for commands."
# Printed by the user shell after wait() returns, i.e. once the process it
# forked for `exec` has exited. Child threads print their own
# "[Kernel] Process N exited" lines earlier, so those are not completion.
CHILD_DONE = "Child exited with code:"
KERNEL_EXIT = re.compile(r"\[Kernel\] Process (\d+) exited with code (-?\d+)")
KERNEL_HALT = "System Halted."


class RunnerError(Exception):
    """Infrastructure failure: the job never got a fair run."""


class MissingArtifactError(RunnerError):
    pass


class InvalidArtifactError(RunnerError):
    pass


class MkfsError(RunnerError):
    pass


class QemuSpawnError(RunnerError):
    pass


@dataclass
class Verdict:
    status: str  # one of Job.Status values except queued/running
    exit_code: int | None
    detail: str


@dataclass
class RunResult:
    verdict: Verdict
    serial_log: str
    duration: float


def check_artifacts(repo: Path) -> None:
    missing = [a for a in REPO_ARTIFACTS if not (repo / a).is_file()]
    if missing:
        raise MissingArtifactError(
            f"MyOS build artifacts missing in {repo}: {', '.join(missing)} "
            f"(run `make disk.img` in the MyOS repo)"
        )
    # mkfs writes one direct-block pointer per file block without checking
    # the 48-entry inode array, including for trusted build artifacts.
    for artifact in REPO_ARTIFACTS:
        if artifact == "kernel.bin" or artifact.endswith(".elf"):
            check_file_size(repo / artifact)


def check_file_size(path: Path) -> None:
    size = path.stat().st_size
    if not 0 < size <= SIMPLEFS_FILE_BYTES:
        raise InvalidArtifactError(
            f"{path.name} is {size} bytes; SimpleFS requires 1..{SIMPLEFS_FILE_BYTES} bytes"
        )


def build_disk_image(repo: Path, elf_path: Path, workdir: Path) -> Path:
    """Assemble a disk image in workdir with elf_path as hello.elf.

    kernel.bin is reused as-is; only mkfs runs, which takes well under a
    second.
    """
    check_artifacts(repo)
    check_file_size(elf_path)
    (workdir / "programs").mkdir(parents=True, exist_ok=True)
    for artifact in REPO_ARTIFACTS:
        shutil.copy2(repo / artifact, workdir / artifact)
    shutil.copyfile(elf_path, workdir / "programs" / JOB_SLOT_NAME)

    try:
        proc = subprocess.run(
            ["./mkfs"], cwd=workdir, capture_output=True, text=True, timeout=30
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        raise MkfsError(f"could not run mkfs: {e}") from e
    if proc.returncode != 0:
        raise MkfsError(f"mkfs exited {proc.returncode}: {proc.stdout[-500:]}{proc.stderr[-500:]}")
    # mkfs only warns and carries on when an input is missing; treat that as
    # a failure rather than booting an image without the job in it.
    if "WARNING" in proc.stdout:
        raise MkfsError(f"mkfs reported problems: {proc.stdout[-500:]}")

    disk = workdir / "disk.img"
    if not disk.is_file():
        raise MkfsError("mkfs succeeded but produced no disk.img")
    return disk


def classify(serial: str) -> Verdict | None:
    """Decide the job's outcome from the serial log so far, or None if the
    run hasn't reached a verdict yet.

    Only output after the command echo counts, so boot messages can't be
    mistaken for the job's.
    """
    start = serial.find(RUN_COMMAND.strip())
    if start == -1:
        # A kernel exception before the shell came up is still a crash.
        if KERNEL_HALT in serial:
            return Verdict("crashed", None, "kernel halted before the shell was ready")
        return None
    out = serial[start:]

    if KERNEL_HALT in out:
        m = re.search(r"EXCEPTION: ([^\n!]+)", out)
        what = m.group(1).strip() if m else "kernel exception"
        return Verdict("crashed", None, f"kernel halted: {what}")

    done = out.find(CHILD_DONE)
    if done == -1:
        return None
    # The process the shell was waiting on is the last one to exit before
    # the shell prints CHILD_DONE.
    exits = KERNEL_EXIT.findall(out[:done])
    if not exits:
        return Verdict("failed", None, "shell reported child exit but no exit code was printed")
    pid, code = exits[-1]
    code = int(code)
    if code == 0:
        return Verdict("success", 0, f"process {pid} exited with code 0")
    detail = f"process {pid} exited with code {code}"
    # exec() failures (e.g. the kernel's ELF loader rejecting the file)
    # surface only as exit 1 from the shell's child; say why.
    if m := re.search(r"\[ELF\] Error: ([^\n]+)", out):
        detail += f" (ELF loader: {m.group(1).strip()})"
    return Verdict("failed", code, detail)


def run_qemu(qemu: str, disk: Path, timeout: float) -> RunResult:
    """Boot disk headless, type the run command once the shell is up, and
    wait for a verdict. The VM is always killed before returning: MyOS halts
    instead of powering off, so QEMU never exits by itself.
    """
    cmd = [
        qemu,
        "-display", "none",
        "-monitor", "none",
        "-serial", "stdio",
        "-no-reboot",
        "-drive", f"format=raw,file={disk}",
    ]
    t0 = time.monotonic()
    try:
        proc = subprocess.Popen(
            cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT
        )
    except OSError as e:
        raise QemuSpawnError(f"could not start {qemu}: {e}") from e

    buf = bytearray()
    lock = threading.Lock()

    def pump():
        while chunk := proc.stdout.read1(4096):
            with lock:
                buf.extend(chunk)

    reader = threading.Thread(target=pump, daemon=True)
    reader.start()

    def text():
        with lock:
            return buf.decode("latin-1").replace("\r", "")

    verdict = None
    sent = False
    try:
        while True:
            elapsed = time.monotonic() - t0
            serial = text()
            if not sent and SHELL_READY in serial:
                proc.stdin.write(RUN_COMMAND.encode())
                proc.stdin.flush()
                sent = True
            verdict = classify(serial)
            if verdict:
                break
            if proc.poll() is not None:
                verdict = Verdict("failed", None, f"QEMU exited unexpectedly (status {proc.returncode})")
                break
            if elapsed >= timeout:
                where = "while running the job" if sent else "before the shell prompt appeared"
                verdict = Verdict("timeout", None, f"no verdict within {timeout:.1f}s ({where})")
                break
            time.sleep(0.02)
    finally:
        proc.kill()
        proc.wait()
        reader.join(timeout=1)

    return RunResult(verdict=verdict, serial_log=text(), duration=time.monotonic() - t0)


def run_elf(repo: Path, qemu: str, elf_path: Path, timeout: float) -> RunResult:
    workdir = Path(tempfile.mkdtemp(prefix="myos-job-"))
    try:
        disk = build_disk_image(repo, elf_path, workdir)
        return run_qemu(qemu, disk, timeout)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
