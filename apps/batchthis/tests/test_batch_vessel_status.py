from unittest import mock

import pytest
from django.core.exceptions import ValidationError
from django.test import Client
from django.urls import reverse

from ..factories import BatchFactory, FermenterFactory, RecipeFactory, VesselFactory
from ..models import Batch, Vessel


def _add_batch_post(fermenter, recipe, name="status test batch"):
    return Client().post(
        reverse("addBatch"),
        data={
            "name": name,
            "startdate": "2026-09-22",
            "size": "6Gallons",
            "fermenter": fermenter.pk,
            "startingGravity": "1.09",
            "estimatedEndGravity": "1.005",
            "recipe": recipe.pk,
        },
    )


@pytest.mark.django_db
def test_creating_a_batch_marks_its_vessel_active_and_logs_one_event():
    fermenter = FermenterFactory(vessel=VesselFactory(status=Vessel.STATUS_READY))

    response = _add_batch_post(fermenter, RecipeFactory(with_plan=True))

    assert response.status_code == 302
    batch = Batch.objects.get(name="status test batch")
    vessel = fermenter.vessel
    vessel.refresh_from_db()
    assert vessel.status == Vessel.STATUS_ACTIVE
    events = list(vessel.status_events.all())
    assert len(events) == 1
    assert events[0].status == Vessel.STATUS_ACTIVE
    assert events[0].batch == batch


@pytest.mark.django_db
@pytest.mark.parametrize("status", [Vessel.STATUS_ACTIVE, Vessel.STATUS_DIRTY])
def test_creating_a_batch_on_a_vessel_that_is_not_ready_is_rejected(status):
    fermenter = FermenterFactory(vessel=VesselFactory(status=status))

    response = _add_batch_post(fermenter, RecipeFactory())

    assert response.status_code == 200
    assert "fermenter" in response.context["form"].errors
    assert not Batch.objects.filter(name="status test batch").exists()
    vessel = fermenter.vessel
    vessel.refresh_from_db()
    assert vessel.status == status
    assert not vessel.status_events.exists()


@pytest.mark.django_db
def test_add_batch_form_only_offers_ready_fermenters():
    ready = FermenterFactory(vessel=VesselFactory(status=Vessel.STATUS_READY))
    FermenterFactory(vessel=VesselFactory(status=Vessel.STATUS_ACTIVE))
    FermenterFactory(vessel=VesselFactory(status=Vessel.STATUS_DIRTY))

    response = Client().get(reverse("addBatch"))

    offered = list(response.context["form"].fields["fermenter"].queryset)
    assert offered == [ready]


@pytest.mark.django_db
def test_saving_a_batch_does_not_change_vessel_status_on_its_own():
    # The old setActiveFermenter signal flipped status without an event; status
    # now only changes through set_vessel_status().
    vessel = VesselFactory(status=Vessel.STATUS_READY)

    BatchFactory(fermenter=FermenterFactory(vessel=vessel))

    vessel.refresh_from_db()
    assert vessel.status == Vessel.STATUS_READY
    assert not vessel.status_events.exists()


@pytest.mark.django_db
def test_completing_a_batch_marks_its_vessel_dirty_and_logs_one_event():
    vessel = VesselFactory(status=Vessel.STATUS_ACTIVE)
    batch = BatchFactory(fermenter=FermenterFactory(vessel=vessel))

    batch.complete()

    batch.refresh_from_db()
    vessel.refresh_from_db()
    assert batch.active is False
    assert batch.enddate is not None
    assert vessel.status == Vessel.STATUS_DIRTY
    events = list(vessel.status_events.all())
    assert len(events) == 1
    assert events[0].status == Vessel.STATUS_DIRTY
    assert events[0].batch == batch


@pytest.mark.django_db
def test_completing_an_already_complete_batch_raises_and_logs_nothing():
    vessel = VesselFactory(status=Vessel.STATUS_ACTIVE)
    batch = BatchFactory(fermenter=FermenterFactory(vessel=vessel))
    batch.complete()

    with pytest.raises(ValidationError):
        batch.complete()

    assert vessel.status_events.count() == 1


@pytest.mark.django_db
def test_batch_is_not_saved_if_the_vessel_status_cannot_be_set():
    fermenter = FermenterFactory(vessel=VesselFactory(status=Vessel.STATUS_READY))

    with mock.patch(
        "apps.batchthis.views.main.set_vessel_status", side_effect=RuntimeError("db down")
    ):
        response = _add_batch_post(fermenter, RecipeFactory(with_plan=True))

    assert response.status_code == 200
    assert response.context["form"].non_field_errors()
    assert not Batch.objects.filter(name="status test batch").exists()
    fermenter.vessel.refresh_from_db()
    assert fermenter.vessel.status == Vessel.STATUS_READY
