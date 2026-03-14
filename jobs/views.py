import logging

from django.conf import settings
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_http_methods

from .models import Job, Slot
from .validation import ELF32_EHDR_SIZE, UploadError, check_elf_header, check_size

log = logging.getLogger(__name__)


@require_GET
def dashboard(request):
    return render(request, "jobs/dashboard.html", {"max_binary_bytes": settings.MAX_BINARY_BYTES})


@require_GET
def slot_list(request):
    return JsonResponse({"slots": list(Slot.objects.order_by("id").values("id", "status", "current_job_id"))})


def _error(status: int, code: str, message: str) -> JsonResponse:
    return JsonResponse({"error": {"code": code, "message": message}}, status=status)


def _serialize(job: Job, *, with_log: bool) -> dict:
    data = {
        "id": job.id,
        "status": job.status,
        "exit_code": job.exit_code,
        "detail": job.detail,
        "binary": job.original_name,
        "slot": job.slot_id,
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "finished_at": job.finished_at.isoformat() if job.finished_at else None,
    }
    if with_log:
        data["serial_log"] = job.serial_log
    return data


@csrf_exempt  # API-only; no auth or sessions in scope.
@require_http_methods(["GET", "POST"])
def jobs_collection(request):
    if request.method == "POST":
        return _create_job(request)
    return _list_jobs(request)


def _create_job(request):
    upload = request.FILES.get("binary")
    if upload is None:
        return _error(400, "missing_file", "send the ELF as multipart form field 'binary'")

    try:
        check_size(upload.size)
        # ?validate=false lets a malformed binary through so MyOS's own
        # loader can be exercised. The size check above is not skippable.
        if request.GET.get("validate", "true").lower() != "false":
            upload.seek(0)
            check_elf_header(upload.read(ELF32_EHDR_SIZE))
            upload.seek(0)
    except UploadError as e:
        return _error(400, e.code, e.message)

    try:
        job = Job.objects.create(uploaded_binary=upload, original_name=upload.name[:255])
    except Exception:
        log.exception("failed to store upload %r", upload.name)
        return _error(500, "storage_error", "could not store the uploaded binary")

    log.info("queued job %s (%s, %d bytes)", job.id, upload.name, upload.size)
    return JsonResponse(_serialize(job, with_log=False), status=201)


def _list_jobs(request):
    jobs = Job.objects.all()
    if status := request.GET.get("status"):
        wanted = [s.strip() for s in status.split(",") if s.strip()]
        unknown = sorted(set(wanted) - set(Job.Status.values))
        if unknown:
            return _error(
                400, "bad_status",
                f"unknown status {', '.join(unknown)}; use one of {', '.join(Job.Status.values)}",
            )
        jobs = jobs.filter(status__in=wanted)
    return JsonResponse({"jobs": [_serialize(j, with_log=False) for j in jobs.defer("serial_log")]})


@require_GET
def job_detail(request, job_id: int):
    try:
        job = Job.objects.get(pk=job_id)
    except Job.DoesNotExist:
        return _error(404, "not_found", f"job {job_id} does not exist")
    return JsonResponse(_serialize(job, with_log=True))
