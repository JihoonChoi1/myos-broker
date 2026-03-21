"""Verdict rules against serial logs captured from real MyOS runs."""
from django.test import SimpleTestCase

from jobs.runner import classify

BOOT = """Phase 1: Bootloader Fixed.
[Kernel] Launching User Shell (PID 1)...
Welcome to User Land Shell!
Type 'help' for commands.
"""

SUCCESS = BOOT + """> exec hello.elf
Executing: hello.elf
[ELF] Loading file: hello.elf
Hello from User Space! (Ring 3)

[Kernel] Process 2 exited with code 0.
Child exited with code:
> """

# producer_consumer: worker threads exit (with code 0) long before the main
# process, and one of them exits right after the main process's own line
# would be expected. Only the line just before "Child exited" is the job's.
THREADED = BOOT + """> exec hello.elf
[P1] Produced: 100

[Kernel] Process 5 exited with code 0.
  [C2] Consumed: 105

[Kernel] Process 7 exited with code 0.
=== All threads finished. 20/20 items ===

[Kernel] Process 2 exited with code 4.
Child exited with code:
> """

BAD_MAGIC = BOOT + """> exec hello.elf
Executing: hello.elf
[ELF] Loading file: hello.elf
[ELF] Error: Invalid ELF Magic.
Failed to execute program.

[Kernel] Process 2 exited with code 1.
Child exited with code:
> """

PAGE_FAULT = BOOT + """> exec hello.elf
about to null-deref

[!] EXCEPTION: Page Fault!
Faulting Address: 0x0
Error Code: 0x6 (NotPresent Write User )
System Halted.
"""


class ClassifyTests(SimpleTestCase):
    def test_success(self):
        v = classify(SUCCESS)
        self.assertEqual((v.status, v.exit_code), ("success", 0))

    def test_threaded_job_uses_exit_just_before_shell_notice(self):
        # The first "exited with code 0" belongs to a thread; reading it
        # would call this a success.
        v = classify(THREADED)
        self.assertEqual((v.status, v.exit_code), ("failed", 4))
        self.assertIn("process 2", v.detail)

    def test_thread_exit_alone_is_not_completion(self):
        partial = THREADED[: THREADED.index("=== All threads")]
        self.assertIsNone(classify(partial))

    def test_bad_magic_is_failed_with_loader_reason(self):
        v = classify(BAD_MAGIC)
        self.assertEqual((v.status, v.exit_code), ("failed", 1))
        self.assertIn("Invalid ELF Magic", v.detail)

    def test_page_fault_is_crashed(self):
        v = classify(PAGE_FAULT)
        self.assertEqual(v.status, "crashed")
        self.assertIn("Page Fault", v.detail)

    def test_exception_without_halt_line_yet_is_undecided(self):
        partial = PAGE_FAULT[: PAGE_FAULT.index("System Halted.")]
        self.assertIsNone(classify(partial))

    def test_halt_during_boot_is_crashed(self):
        v = classify("Phase 1\n[!] EXCEPTION: Page Fault!\nSystem Halted.\n")
        self.assertEqual(v.status, "crashed")

    def test_nothing_before_command_counts(self):
        self.assertIsNone(classify(BOOT))
        self.assertIsNone(classify(BOOT + "> exec hello.elf\nHello\n"))
