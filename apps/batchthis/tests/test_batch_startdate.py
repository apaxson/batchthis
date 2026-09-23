import datetime

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from ..factories import BatchFactory, FermenterFactory, RecipeFactory, VesselFactory
from ..models import Batch, Vessel


def _new_batch_with_startdate(startdate: str) -> Batch:
    fermenter = FermenterFactory(vessel=VesselFactory(status=Vessel.STATUS_READY))
    response = Client().post(
        reverse("addBatch"),
        data={
            "name": "startdate test batch",
            "startdate": startdate,
            "size": "6Gallons",
            "fermenter": fermenter.pk,
            "startingGravity": "1.09",
            "estimatedEndGravity": "1.005",
            "recipe": RecipeFactory().pk,
        },
    )
    assert response.status_code == 302
    return Batch.objects.get(name="startdate test batch")


@pytest.mark.django_db
def test_a_past_start_date_from_the_form_is_kept_at_the_start_of_that_day():
    # Regression: auto_now_add silently replaced this with "now".
    batch = _new_batch_with_startdate("2026-03-14")

    local = timezone.localtime(batch.startdate)
    assert (local.date(), local.time()) == (datetime.date(2026, 3, 14), datetime.time(0, 0))


@pytest.mark.django_db
def test_todays_start_date_from_the_form_uses_the_current_time():
    before = timezone.now()

    batch = _new_batch_with_startdate(timezone.localdate().isoformat())

    assert before <= batch.startdate <= timezone.now()


@pytest.mark.django_db
def test_start_date_is_timezone_aware():
    batch = _new_batch_with_startdate("2026-03-14")

    assert timezone.is_aware(batch.startdate)


@pytest.mark.django_db
def test_saving_a_batch_again_does_not_change_its_start_date():
    batch = _new_batch_with_startdate("2026-03-14")
    original = batch.startdate

    batch.name = "renamed"
    batch.save()

    batch.refresh_from_db()
    assert batch.startdate == original


@pytest.mark.django_db
def test_a_batch_created_without_a_start_date_defaults_to_now():
    before = timezone.now()

    batch = BatchFactory()

    assert before <= batch.startdate <= timezone.now()
