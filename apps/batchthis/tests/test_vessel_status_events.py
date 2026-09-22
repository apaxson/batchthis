import datetime

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone

from ..factories import VesselFactory, VesselStatusEventFactory
from ..models import Vessel


@pytest.mark.django_db
def test_status_choices_reject_an_invalid_value():
    vessel = VesselFactory(status="Not A Real Status")

    with pytest.raises(ValidationError):
        vessel.full_clean()


@pytest.mark.django_db
def test_status_choices_accept_the_three_existing_values():
    for status in (Vessel.STATUS_READY, Vessel.STATUS_ACTIVE, Vessel.STATUS_DIRTY):
        vessel = VesselFactory(status=status)
        vessel.full_clean()  # should not raise


@pytest.mark.django_db
def test_events_for_a_vessel_come_back_oldest_first():
    vessel = VesselFactory()
    now = timezone.now()
    newest = VesselStatusEventFactory(vessel=vessel, status=Vessel.STATUS_DIRTY, timestamp=now)
    oldest = VesselStatusEventFactory(
        vessel=vessel, status=Vessel.STATUS_READY, timestamp=now - datetime.timedelta(days=2)
    )
    middle = VesselStatusEventFactory(
        vessel=vessel, status=Vessel.STATUS_ACTIVE, timestamp=now - datetime.timedelta(days=1)
    )

    assert list(vessel.status_events.all()) == [oldest, middle, newest]


@pytest.mark.django_db
def test_current_status_event_is_the_most_recent_one_regardless_of_creation_order():
    vessel = VesselFactory()
    now = timezone.now()
    # Deliberately create the newest one first, to prove current_status_event
    # doesn't just rely on insertion order or Meta.ordering's ascending default.
    newest = VesselStatusEventFactory(vessel=vessel, status=Vessel.STATUS_DIRTY, timestamp=now)
    VesselStatusEventFactory(
        vessel=vessel, status=Vessel.STATUS_READY, timestamp=now - datetime.timedelta(days=2)
    )

    assert vessel.current_status_event == newest


@pytest.mark.django_db
def test_current_status_event_is_none_with_no_events():
    vessel = VesselFactory()

    assert vessel.current_status_event is None


@pytest.mark.django_db
def test_time_in_current_status_is_none_with_no_events():
    vessel = VesselFactory()

    assert vessel.time_in_current_status() is None


@pytest.mark.django_db
def test_time_in_current_status_measures_since_the_latest_event():
    vessel = VesselFactory()
    VesselStatusEventFactory(
        vessel=vessel, status=Vessel.STATUS_ACTIVE, timestamp=timezone.now() - datetime.timedelta(hours=5)
    )

    elapsed = vessel.time_in_current_status()

    assert elapsed is not None
    assert elapsed >= datetime.timedelta(hours=5)
    assert elapsed < datetime.timedelta(hours=5, minutes=1)


@pytest.mark.django_db
def test_status_events_are_scoped_to_their_own_vessel():
    vessel_a = VesselFactory()
    vessel_b = VesselFactory()
    event_a = VesselStatusEventFactory(vessel=vessel_a)
    VesselStatusEventFactory(vessel=vessel_b)

    assert list(vessel_a.status_events.all()) == [event_a]


@pytest.mark.django_db
def test_status_event_str_includes_vessel_and_status():
    event = VesselStatusEventFactory(status=Vessel.STATUS_ACTIVE)

    text = str(event)

    assert str(event.vessel) in text
    assert Vessel.STATUS_ACTIVE in text
