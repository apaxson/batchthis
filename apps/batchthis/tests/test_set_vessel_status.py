from unittest import mock

import pytest
from django.core.exceptions import ValidationError

from ..factories import BatchFactory, VesselFactory
from ..models import ActivityLog, Vessel, VesselStatusEvent
from ..services import set_vessel_status


@pytest.mark.django_db
def test_updates_vessel_status_and_creates_one_event():
    vessel = VesselFactory(status=Vessel.STATUS_READY)

    event = set_vessel_status(vessel, Vessel.STATUS_ACTIVE, notes="Pitched")

    vessel.refresh_from_db()
    assert vessel.status == Vessel.STATUS_ACTIVE
    assert list(vessel.status_events.all()) == [event]
    assert event.status == Vessel.STATUS_ACTIVE
    assert event.notes == "Pitched"
    assert event.batch is None


@pytest.mark.django_db
def test_links_the_event_to_a_batch_when_given():
    batch = BatchFactory()
    vessel = batch.fermenter.vessel

    event = set_vessel_status(vessel, Vessel.STATUS_DIRTY, batch=batch)

    assert event.batch == batch


@pytest.mark.django_db
def test_returned_event_becomes_the_current_status_event():
    vessel = VesselFactory()
    set_vessel_status(vessel, Vessel.STATUS_ACTIVE)

    latest = set_vessel_status(vessel, Vessel.STATUS_DIRTY)

    assert vessel.current_status_event == latest


@pytest.mark.django_db
def test_accepts_a_repeat_of_the_current_status():
    # No state-machine enforcement in v1 (TODO-VesselLifecycle.txt open decision).
    vessel = VesselFactory(status=Vessel.STATUS_READY)

    set_vessel_status(vessel, Vessel.STATUS_READY)

    assert vessel.status_events.count() == 1


@pytest.mark.django_db
def test_invalid_status_raises_and_changes_nothing():
    vessel = VesselFactory(status=Vessel.STATUS_READY)

    with pytest.raises(ValidationError):
        set_vessel_status(vessel, "Not A Real Status")

    vessel.refresh_from_db()
    assert vessel.status == Vessel.STATUS_READY
    assert not vessel.status_events.exists()


@pytest.mark.django_db
def test_vessel_status_rolls_back_if_the_event_cannot_be_written():
    vessel = VesselFactory(status=Vessel.STATUS_READY)

    with mock.patch.object(
        VesselStatusEvent.objects, "create", side_effect=RuntimeError("db down")
    ):
        with pytest.raises(RuntimeError):
            set_vessel_status(vessel, Vessel.STATUS_ACTIVE)

    vessel.refresh_from_db()
    assert vessel.status == Vessel.STATUS_READY
    assert not vessel.status_events.exists()


@pytest.mark.django_db
def test_does_not_write_to_the_batch_activity_log():
    # ActivityLog is reserved for Batch actions; vessel history lives only in
    # VesselStatusEvent.
    batch = BatchFactory()
    activity_before = ActivityLog.objects.count()

    set_vessel_status(batch.fermenter.vessel, Vessel.STATUS_DIRTY, batch=batch)

    assert ActivityLog.objects.count() == activity_before
