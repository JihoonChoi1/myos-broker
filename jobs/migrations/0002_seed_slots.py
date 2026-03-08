from django.db import migrations

SLOT_COUNT = 2


def seed(apps, schema_editor):
    Slot = apps.get_model("jobs", "Slot")
    for pk in range(1, SLOT_COUNT + 1):
        Slot.objects.get_or_create(pk=pk, defaults={"status": "idle"})


def unseed(apps, schema_editor):
    apps.get_model("jobs", "Slot").objects.filter(pk__lte=SLOT_COUNT).delete()


class Migration(migrations.Migration):
    dependencies = [("jobs", "0001_initial")]

    operations = [migrations.RunPython(seed, unseed)]
