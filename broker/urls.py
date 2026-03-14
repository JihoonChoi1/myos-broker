from django.contrib import admin
from django.urls import path

from jobs import views

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("admin/", admin.site.urls),
    path("slots", views.slot_list, name="slots"),
    path("jobs", views.jobs_collection, name="jobs"),
    path("jobs/<int:job_id>", views.job_detail, name="job-detail"),
]
