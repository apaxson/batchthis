import datetime

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone

from ..factories import BatchFactory, FermenterFactory, VesselFactory
from ..models import AgingTank, BatchStage, BatchStageEvent, Vessel
from ..services import (
    add_activity_log,
    add_batch_log,
    set_vessel_status,
    transition_stage_event,
)


def _stage(shortid):
    return BatchStage.objects.get(shortid=shortid)


def _batch(days_ago=60):
    vessel = VesselFactory(name="Carboy 1", status=Vessel.STATUS_ACTIVE)
    batch = BatchFactory(fermenter=FermenterFactory(vessel=vessel))
    batch.vessel = vessel
    batch.startdate = timezone.now() - datetime.timedelta(days=days_ago)
    batch.save()
    return batch


def _tank(name="Aging Tank A", status=Vessel.STATUS_READY):
    vessel = VesselFactory(name=name, status=status)
    AgingTank.objects.create(vessel=vessel)
    return vessel


def _ago(days):
    return timezone.now() - datetime.timedelta(days=days)


def _stage_entries(batch):
    return list(batch.activity.filter(text__startswith="Stage [").values_list("text", flat=True))


def _transition(batch, shortid, days_ago, **kwargs):
    return transition_stage_event(batch, _stage(shortid), timestamp=_ago(days_ago), **kwargs)


# ---------- Each kind of stage ----------

@pytest.mark.django_db
def test_pitch_logs_the_event_in_the_current_vessel_without_moving_the_batch():
    batch = _batch()

    event = _transition(batch, "pitch", 50, notes="EC-1118")

    assert (event.stage.shortid, event.vessel, event.notes) == ("pitch", batch.current_vessel, "EC-1118")
    assert batch.current_state == BatchStage.STATE_FERMENTATION
    assert not batch.current_vessel.status_events.exists()


@pytest.mark.django_db
@pytest.mark.parametrize(
    "shortid", ["racking", "coarse-filtering", "fine-filtering", "sterile-filtering"]
)
def test_transferring_stages_move_the_batch_into_the_destination(shortid):
    batch = _batch()
    src = batch.current_vessel
    _transition(batch, "pitch", 50)
    if shortid != "racking":
        _transition(batch, "racking", 40, dst_vessel=_tank("First Tank"))
        src = batch.current_vessel
    dst = _tank()

    event = _transition(batch, shortid, 30, dst_vessel=dst)

    batch.refresh_from_db()
    src.refresh_from_db()
    dst.refresh_from_db()
    assert batch.vessel == dst
    assert event.vessel == dst
    assert (src.status, dst.status) == (Vessel.STATUS_DIRTY, Vessel.STATUS_ACTIVE)


@pytest.mark.django_db
def test_complete_batch_closes_out_the_batch_and_frees_its_vessel():
    batch = _batch()
    _transition(batch, "pitch", 50)
    _transition(batch, "racking", 40, dst_vessel=_tank("First Tank"))
    _transition(batch, "sterile-filtering", 20, dst_vessel=_tank("Bottling Tank"))
    bottling_tank = batch.current_vessel

    event = _transition(batch, "complete-batch", 10)

    batch.refresh_from_db()
    bottling_tank.refresh_from_db()
    assert batch.active is False
    assert event.vessel == bottling_tank
    assert bottling_tank.status == Vessel.STATUS_DIRTY
    assert batch.current_state == BatchStage.STATE_COMPLETED


# ---------- One ActivityLog entry per stage ----------

@pytest.mark.django_db
def test_each_stage_writes_one_activity_log_entry():
    batch = _batch()

    _transition(batch, "pitch", 50, notes="EC-1118")
    _transition(batch, "racking", 40, dst_vessel=_tank())

    assert _stage_entries(batch) == [
        "Stage [Pitch] :: [Carboy 1] :: EC-1118",
        "Stage [Racking] :: [Carboy 1] -> [Aging Tank A]",
    ]


@pytest.mark.django_db
def test_a_workflow_transfer_does_not_also_write_its_own_transfer_entry():
    batch = _batch()
    _transition(batch, "pitch", 50)

    _transition(batch, "racking", 40, dst_vessel=_tank())

    assert not batch.activity.filter(text__startswith="Transferred").exists()


@pytest.mark.django_db
def test_the_activity_entry_uses_the_stage_timestamp():
    batch = _batch()
    when = _ago(50)

    transition_stage_event(batch, _stage("pitch"), timestamp=when)

    assert batch.activity.get(text__startswith="Stage [").datetime == when


# ---------- Workflow rules ----------

@pytest.mark.django_db
@pytest.mark.parametrize("shortid", ["racking", "sterile-filtering", "complete-batch"])
def test_the_first_stage_must_be_pitch(shortid):
    batch = _batch()

    with pytest.raises(ValidationError, match="Pitch"):
        _transition(batch, shortid, 40, dst_vessel=_tank() if shortid != "complete-batch" else None)

    assert not batch.stage_events.exists()


@pytest.mark.django_db
def test_pitch_can_only_be_logged_once():
    batch = _batch()
    _transition(batch, "pitch", 50)

    with pytest.raises(ValidationError):
        _transition(batch, "pitch", 40)


@pytest.mark.django_db
def test_a_stage_must_start_from_the_batchs_current_state():
    batch = _batch()
    _transition(batch, "pitch", 50)  # now Fermentation

    with pytest.raises(ValidationError, match="Fermentation"):
        _transition(batch, "sterile-filtering", 40, dst_vessel=_tank())  # needs Aging
    with pytest.raises(ValidationError):
        _transition(batch, "complete-batch", 40)  # needs Bottling


@pytest.mark.django_db
def test_racking_can_be_repeated_during_aging():
    batch = _batch()
    _transition(batch, "pitch", 50)
    _transition(batch, "racking", 40, dst_vessel=_tank("Tank A"))

    event = _transition(batch, "racking", 30, dst_vessel=_tank("Tank B"))

    assert event.vessel.name == "Tank B"
    assert batch.current_state == BatchStage.STATE_AGING


@pytest.mark.django_db
def test_nothing_can_be_logged_after_complete_batch():
    batch = _batch()
    _transition(batch, "pitch", 50)
    _transition(batch, "racking", 40, dst_vessel=_tank("Tank A"))
    _transition(batch, "sterile-filtering", 30, dst_vessel=_tank("Tank B"))
    _transition(batch, "complete-batch", 20)

    with pytest.raises(ValidationError):
        _transition(batch, "racking", 10, dst_vessel=_tank("Tank C"))


@pytest.mark.django_db
def test_a_transferring_stage_requires_a_destination_vessel():
    batch = _batch()
    _transition(batch, "pitch", 50)

    with pytest.raises(ValidationError, match="destination"):
        _transition(batch, "racking", 40)


@pytest.mark.django_db
@pytest.mark.parametrize("shortid", ["pitch", "complete-batch"])
def test_a_non_transferring_stage_rejects_a_destination_vessel(shortid):
    batch = _batch()
    if shortid == "complete-batch":
        _transition(batch, "pitch", 50)
        _transition(batch, "racking", 40, dst_vessel=_tank("Tank A"))
        _transition(batch, "sterile-filtering", 30, dst_vessel=_tank("Tank B"))

    with pytest.raises(ValidationError):
        _transition(batch, shortid, 20, dst_vessel=_tank("Tank C"))


# ---------- Timestamps ----------

@pytest.mark.django_db
def test_timestamp_defaults_to_now():
    batch = _batch()
    before = timezone.now()

    event = transition_stage_event(batch, _stage("pitch"))

    assert before <= event.timestamp <= timezone.now()


@pytest.mark.django_db
def test_a_timestamp_in_the_future_is_rejected():
    batch = _batch()

    with pytest.raises(ValidationError, match="future"):
        transition_stage_event(batch, _stage("pitch"), timestamp=timezone.now() + datetime.timedelta(hours=1))


@pytest.mark.django_db
def test_a_timestamp_before_the_latest_stage_is_rejected():
    batch = _batch()
    _transition(batch, "pitch", 30)

    with pytest.raises(ValidationError, match="earlier"):
        _transition(batch, "racking", 40, dst_vessel=_tank())


@pytest.mark.django_db
def test_pitch_before_the_batch_start_date_is_rejected():
    batch = _batch(days_ago=10)

    with pytest.raises(ValidationError, match="start"):
        _transition(batch, "pitch", 20)


@pytest.mark.django_db
def test_a_backdated_transfer_stamps_the_vessel_history_with_the_stage_time():
    batch = _batch()
    src = batch.current_vessel
    dst = _tank()
    _transition(batch, "pitch", 50)
    when = _ago(40)

    transition_stage_event(batch, _stage("racking"), timestamp=when, dst_vessel=dst)

    assert src.status_events.get().timestamp == when
    assert dst.status_events.get().timestamp == when


@pytest.mark.django_db
def test_a_backdated_complete_batch_sets_the_end_date_to_the_stage_time():
    batch = _batch()
    _transition(batch, "pitch", 50)
    _transition(batch, "racking", 40, dst_vessel=_tank("Tank A"))
    _transition(batch, "sterile-filtering", 30, dst_vessel=_tank("Tank B"))
    when = _ago(20)

    transition_stage_event(batch, _stage("complete-batch"), timestamp=when)

    batch.refresh_from_db()
    assert batch.enddate == when


# ---------- All or nothing ----------

@pytest.mark.django_db
def test_a_rejected_transfer_saves_nothing():
    batch = _batch()
    _transition(batch, "pitch", 50)
    busy = _tank(status=Vessel.STATUS_ACTIVE)
    entries_before = batch.activity.count()

    with pytest.raises(ValidationError):
        _transition(batch, "racking", 40, dst_vessel=busy)

    batch.refresh_from_db()
    assert batch.stage_events.count() == 1
    assert batch.activity.count() == entries_before
    assert batch.current_state == BatchStage.STATE_FERMENTATION


# ---------- The two log helpers ----------

@pytest.mark.django_db
def test_add_batch_log_creates_the_stage_event():
    batch = _batch()
    when = _ago(5)

    event = add_batch_log(batch, _stage("pitch"), timestamp=when, vessel=batch.current_vessel, notes="n")

    assert BatchStageEvent.objects.get() == event
    assert (event.batch, event.timestamp, event.vessel, event.notes) == (batch, when, batch.current_vessel, "n")


@pytest.mark.django_db
def test_add_activity_log_adds_an_entry_to_the_batch():
    batch = _batch()
    when = _ago(5)

    entry = add_activity_log(batch, "Something happened", timestamp=when)

    assert batch.activity.filter(pk=entry.pk, text="Something happened", datetime=when).exists()


@pytest.mark.django_db
def test_set_vessel_status_accepts_a_timestamp():
    vessel = VesselFactory(status=Vessel.STATUS_DIRTY)
    when = _ago(3)

    event = set_vessel_status(vessel, Vessel.STATUS_READY, timestamp=when)

    assert event.timestamp == when
