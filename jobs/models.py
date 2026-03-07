from django.db import models


class Slot(models.Model):
    """One unit of "how many MyOS VMs may run at once".

    A slot is not a QEMU process. Every job spawns its own QEMU with its own
    disk image, so jobs never share an emulator. Slots only exist so that
    workers can agree, through row locks, on how many run concurrently.
    """

    class Status(models.TextChoices):
        IDLE = "idle"
        BUSY = "busy"

    status = models.CharField(max_length=8, choices=Status.choices, default=Status.IDLE)
    # Which job holds the slot. Lets release/reap free a slot only if it
    # still belongs to the job they are finishing.
    current_job = models.ForeignKey(
        "Job", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )

    def __str__(self):
        return f"Slot {self.pk} ({self.status})"


class Job(models.Model):
    class Status(models.TextChoices):
        QUEUED = "queued"
        RUNNING = "running"
        SUCCESS = "success"
        FAILED = "failed"
        CRASHED = "crashed"
        TIMEOUT = "timeout"

    FINISHED = (Status.SUCCESS, Status.FAILED, Status.CRASHED, Status.TIMEOUT)

    uploaded_binary = models.FileField(upload_to="binaries/")
    original_name = models.CharField(max_length=255, blank=True)
    status = models.CharField(max_length=8, choices=Status.choices, default=Status.QUEUED)
    serial_log = models.TextField(blank=True)
    exit_code = models.IntegerField(null=True, blank=True)
    # Why the job ended the way it did, e.g. "exit code 1",
    # "no verdict within 3.0s", or "mkfs failed: ...".
    detail = models.TextField(blank=True)
    slot = models.ForeignKey(Slot, null=True, blank=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [models.Index(fields=["status", "created_at"])]

    def __str__(self):
        return f"Job {self.pk} ({self.status})"
