from django.contrib import admin

from .models import Job, Slot


@admin.register(Slot)
class SlotAdmin(admin.ModelAdmin):
    list_display = ("id", "status", "current_job")


@admin.register(Job)
class JobAdmin(admin.ModelAdmin):
    list_display = ("id", "original_name", "status", "exit_code", "slot", "created_at", "finished_at")
    list_filter = ("status",)
    readonly_fields = ("serial_log", "detail", "created_at", "started_at", "finished_at")
