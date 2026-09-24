"""Step 11b: Sterile Filtering can package the batch into Bottles / Kegs - it leaves vessel tracking."""
import datetime

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from ..factories import BatchFactory, FermenterFactory, VesselFactory
from ..models import AgingTank, BatchStage, PlanStep, Vessel
from ..services import transfer_batch, transition_stage_event

BOTTLES, KEGS = PlanStep.VESSEL_BOTTLES, PlanStep.VESSEL_KEGS


def _stage(shortid):
    return BatchStage.objects.get(shortid=shortid)


def _ago(days):
    return timezone.now() - datetime.timedelta(days=days)


def _tank(name):
    vessel = VesselFactory(name=name, status=Vessel.STATUS_READY)
    AgingTank.objects.create(vessel=vessel)
    return vessel


def _batch_in_aging():
    """Pitched in Carboy 1, racked into Tank A - ready for Sterile Filtering."""
    carboy = VesselFactory(name="Carboy 1", status=Vessel.STATUS_ACTIVE)
    batch = BatchFactory(fermenter=FermenterFactory(vessel=carboy), vessel=carboy)
    batch.startdate = _ago(120)
    batch.save()
    transition_stage_event(batch, _stage("pitch"), timestamp=_ago(100))
    transition_stage_event(batch, _stage("racking"), timestamp=_ago(80), dst_vessel=_tank("Tank A"))
    return batch


def _package(batch, packaging=BOTTLES, days_ago=10):
    return transition_stage_event(batch, _stage("sterile-filtering"), timestamp=_ago(days_ago), packaging=packaging)


# ---------- Sterile Filtering into Bottles / Kegs ----------

@pytest.mark.django_db
@pytest.mark.parametrize("packaging", [BOTTLES, KEGS])
def test_packaging_empties_the_vessel_and_the_batch_leaves_vessel_tracking(packaging):
    batch = _batch_in_aging()
    tank = batch.current_vessel

    event = _package(batch, packaging)

    batch.refresh_from_db()
    tank.refresh_from_db()
    assert batch.packaging == packaging and batch.current_vessel is None and batch.active
    assert batch.current_state == BatchStage.STATE_BOTTLING
    assert (event.vessel, event.packaging) == (None, packaging)
    assert tank.status == Vessel.STATUS_DIRTY
    assert tank.status_events.latest("timestamp").notes == f"Batch packaged into {packaging}"
    assert tank.current_batch is None


@pytest.mark.django_db
def test_packaging_is_logged_on_the_batch():
    batch = _batch_in_aging()

    _package(batch)

    assert batch.activity.filter(text="Stage [Sterile Filtering] :: [Tank A] -> Bottles").exists()


@pytest.mark.django_db
def test_the_starting_fermenter_does_not_think_it_holds_a_packaged_batch():
    carboy = VesselFactory(name="Carboy 1", status=Vessel.STATUS_ACTIVE)
    batch = BatchFactory(fermenter=FermenterFactory(vessel=carboy), vessel=None, packaging=BOTTLES)

    assert carboy.current_batch is None
    assert batch.current_vessel is None


@pytest.mark.django_db
def test_complete_batch_after_packaging_has_no_vessel_to_flag():
    batch = _batch_in_aging()
    tank = batch.current_vessel
    _package(batch)
    events_before = tank.status_events.count()

    transition_stage_event(batch, _stage("complete-batch"), timestamp=_ago(5))

    batch.refresh_from_db()
    assert not batch.active and batch.packaging == BOTTLES
    assert tank.status_events.count() == events_before
    assert batch.activity.filter(text="Stage [Complete Batch] :: Bottles").exists()


# ---------- What can't package ----------

@pytest.mark.django_db
@pytest.mark.parametrize("shortid", ["racking", "coarse-filtering", "fine-filtering"])
def test_only_sterile_filtering_can_package(shortid):
    batch = _batch_in_aging()

    with pytest.raises(ValidationError, match="Only Sterile Filtering can package"):
        transition_stage_event(batch, _stage(shortid), timestamp=_ago(10), packaging=BOTTLES)

    assert batch.stage_events.count() == 2


@pytest.mark.django_db
def test_sterile_filtering_takes_a_vessel_or_packaging_not_both():
    batch = _batch_in_aging()

    with pytest.raises(ValidationError, match="not both"):
        transition_stage_event(batch, _stage("sterile-filtering"), timestamp=_ago(10),
                               dst_vessel=_tank("Tank B"), packaging=KEGS)


@pytest.mark.django_db
def test_sterile_filtering_needs_a_vessel_or_packaging():
    batch = _batch_in_aging()

    with pytest.raises(ValidationError, match="destination vessel or Bottles / Kegs"):
        transition_stage_event(batch, _stage("sterile-filtering"), timestamp=_ago(10))


@pytest.mark.django_db
def test_unknown_packaging_is_rejected():
    batch = _batch_in_aging()

    with pytest.raises(ValidationError, match="Bottles or Kegs"):
        transition_stage_event(batch, _stage("sterile-filtering"), timestamp=_ago(10), packaging="Cans")


@pytest.mark.django_db
def test_sterile_filtering_into_a_vessel_still_works():
    batch = _batch_in_aging()
    tank_b = _tank("Tank B")

    event = transition_stage_event(batch, _stage("sterile-filtering"), timestamp=_ago(10), dst_vessel=tank_b)

    batch.refresh_from_db()
    assert batch.current_vessel == tank_b and batch.packaging == ""
    assert event.packaging == ""


@pytest.mark.django_db
def test_a_packaged_batch_cannot_be_transferred():
    batch = _batch_in_aging()
    _package(batch)

    with pytest.raises(ValidationError, match="packaged"):
        transfer_batch(batch, _tank("Tank C"), reason="Oops", timestamp=_ago(5))


# ---------- Pages still work for a packaged batch ----------

@pytest.fixture
def client(db):
    client = Client()
    client.force_login(get_user_model().objects.create_user(username="cellarhand", password="pw"))
    return client


@pytest.mark.django_db
def test_pages_show_a_packaged_batch_as_in_bottles(client):
    batch = _batch_in_aging()
    _package(batch)

    for url in (reverse("batch", kwargs={"pk": batch.pk}), reverse("index"),
                reverse("editBatch", kwargs={"pk": batch.pk}), reverse("addDetailStage", kwargs={"pk": batch.pk})):
        response = client.get(url)
        assert response.status_code == 200, url
        assert "Bottles" in response.content.decode(), url


@pytest.mark.django_db
def test_batch_page_does_not_offer_transfer_for_a_packaged_batch(client):
    batch = _batch_in_aging()
    _package(batch)

    page = client.get(reverse("batch", kwargs={"pk": batch.pk})).content.decode()

    assert reverse("transferBatch", kwargs={"pk": batch.pk}) not in page
