import datetime

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone

from ..factories import BatchFactory, FermenterFactory, VesselFactory
from ..models import AgingTank, BatchStage, Vessel
from ..services import allowed_next_stages, transfer_batch, transition_stage_event


def _stage(shortid):
    return BatchStage.objects.get(shortid=shortid)


def _ago(days):
    return timezone.now() - datetime.timedelta(days=days)


def _batch():
    vessel = VesselFactory(name="Carboy 1", status=Vessel.STATUS_ACTIVE)
    batch = BatchFactory(fermenter=FermenterFactory(vessel=vessel))
    batch.vessel = vessel
    batch.startdate = _ago(120)
    batch.save()
    return batch


def _tank(name, status=Vessel.STATUS_READY):
    vessel = VesselFactory(name=name, status=status)
    AgingTank.objects.create(vessel=vessel)
    return vessel


def _step(batch, shortid, days_ago, dst=None):
    return transition_stage_event(batch, _stage(shortid), timestamp=_ago(days_ago), dst_vessel=dst)


def _aging_batch():
    batch = _batch()
    _step(batch, "pitch", 100)
    _step(batch, "racking", 80, dst=_tank("Tank A"))
    return batch


# ---------- Transfer only (no stage change) ----------

@pytest.mark.django_db
def test_a_transfer_moves_the_batch_and_records_a_stageless_event():
    batch = _aging_batch()
    src = batch.current_vessel
    dst = _tank("Tank B")

    event = transfer_batch(batch, dst, reason="Pump failure", timestamp=_ago(50))

    batch.refresh_from_db()
    src.refresh_from_db()
    dst.refresh_from_db()
    assert batch.vessel == dst
    assert (src.status, dst.status) == (Vessel.STATUS_DIRTY, Vessel.STATUS_ACTIVE)
    assert (event.stage, event.vessel, event.notes, event.label) == (None, dst, "Pump failure", "Transfer")


@pytest.mark.django_db
def test_a_transfer_keeps_the_batchs_stage_and_allowed_next_stages():
    batch = _aging_batch()
    before = [s.shortid for s in allowed_next_stages(batch)]

    transfer_batch(batch, _tank("Tank B"), reason="Pump failure", timestamp=_ago(50))

    assert batch.current_state == BatchStage.STATE_AGING
    assert batch.current_stage_event.stage.shortid == "racking"
    assert [s.shortid for s in allowed_next_stages(batch)] == before


@pytest.mark.django_db
def test_a_transfer_logs_the_reason_in_the_activity_log_and_vessel_history():
    batch = _aging_batch()
    src = batch.current_vessel
    dst = _tank("Tank B")

    transfer_batch(batch, dst, reason="Pump failure", timestamp=_ago(50))

    transfers = list(batch.activity.filter(text__startswith="Transferred").values_list("text", flat=True))
    assert transfers == ["Transferred from [Tank A] to [Tank B] :: Pump failure"]
    assert "Pump failure" in src.status_events.latest("timestamp").notes
    assert "Pump failure" in dst.status_events.get().notes


@pytest.mark.django_db
def test_a_backdated_transfer_stamps_the_vessel_history_with_its_time():
    batch = _aging_batch()
    dst = _tank("Tank B")
    when = _ago(50)

    transfer_batch(batch, dst, reason="Pump failure", timestamp=when)

    assert dst.status_events.get().timestamp == when


@pytest.mark.django_db
def test_a_transfer_starts_a_new_time_in_vessel_row_but_not_full_aging():
    batch = _aging_batch()
    full_aging_start = batch.full_aging().start

    transfer_batch(batch, _tank("Tank B"), reason="Pump failure", timestamp=_ago(50))

    stays = batch.vessel_durations()
    assert [(s.label, s.vessel.name, s.state) for s in stays] == [
        ("Pitch", "Carboy 1", BatchStage.STATE_FERMENTATION),
        ("Racking", "Tank A", BatchStage.STATE_AGING),
        ("Transfer", "Tank B", BatchStage.STATE_AGING),
    ]
    assert stays[1].duration.days == 30
    aging = batch.full_aging()
    assert aging.start == full_aging_start and aging.is_open


@pytest.mark.django_db
def test_a_transfer_before_pitch_has_no_state_yet():
    batch = _batch()

    transfer_batch(batch, _tank("Tank B"), reason="Wrong carboy", timestamp=_ago(110))

    (stay,) = batch.vessel_durations()
    assert (stay.label, stay.state) == ("Transfer", None)
    assert batch.current_state is None


@pytest.mark.django_db
def test_the_next_stage_starts_from_the_vessel_the_batch_was_transferred_into():
    batch = _aging_batch()
    tank_b = _tank("Tank B")
    transfer_batch(batch, tank_b, reason="Pump failure", timestamp=_ago(50))

    _step(batch, "coarse-filtering", 40, dst=_tank("Tank C"))

    tank_b.refresh_from_db()
    assert tank_b.status == Vessel.STATUS_DIRTY


# ---------- Transfer with a stage change ----------

@pytest.mark.django_db
def test_choosing_a_transferring_stage_logs_that_stage_with_the_reason_as_notes():
    batch = _aging_batch()
    dst = _tank("Tank B")

    event = transfer_batch(batch, dst, reason="Clarity issue", stage=_stage("coarse-filtering"), timestamp=_ago(50))

    assert (event.stage.shortid, event.vessel, event.notes) == ("coarse-filtering", dst, "Clarity issue")
    assert batch.full_aging().is_open is False
    assert batch.activity.filter(text="Stage [Coarse Filtering] :: [Tank A] -> [Tank B] :: Clarity issue").exists()


@pytest.mark.django_db
def test_a_stage_that_does_not_transfer_the_batch_is_rejected():
    batch = _aging_batch()
    _step(batch, "sterile-filtering", 60, dst=_tank("Tank B"))

    with pytest.raises(ValidationError):
        transfer_batch(batch, _tank("Tank C"), reason="x", stage=_stage("complete-batch"), timestamp=_ago(50))

    assert batch.active


@pytest.mark.django_db
def test_a_stage_the_workflow_does_not_allow_next_is_rejected():
    batch = _batch()
    _step(batch, "pitch", 100)  # Fermentation

    with pytest.raises(ValidationError):
        transfer_batch(batch, _tank("Tank B"), reason="x", stage=_stage("sterile-filtering"), timestamp=_ago(50))


# ---------- Rejections change nothing ----------

@pytest.mark.django_db
@pytest.mark.parametrize("reason", ["", "   "])
def test_a_reason_is_required(reason):
    batch = _aging_batch()

    with pytest.raises(ValidationError, match="reason"):
        transfer_batch(batch, _tank("Tank B"), reason=reason, timestamp=_ago(50))

    assert batch.stage_events.count() == 2


@pytest.mark.django_db
@pytest.mark.parametrize("status", [Vessel.STATUS_ACTIVE, Vessel.STATUS_DIRTY, Vessel.STATUS_OUT])
def test_a_destination_that_is_not_clean_ready_is_rejected(status):
    batch = _aging_batch()
    dst = _tank("Tank B", status=status)

    with pytest.raises(ValidationError):
        transfer_batch(batch, dst, reason="Pump failure", timestamp=_ago(50))

    batch.refresh_from_db()
    assert batch.vessel.name == "Tank A"
    assert batch.stage_events.count() == 2
    assert not batch.activity.filter(text__startswith="Transferred").exists()


@pytest.mark.django_db
def test_a_completed_batch_cannot_be_transferred():
    batch = _aging_batch()
    _step(batch, "sterile-filtering", 60, dst=_tank("Tank B"))
    _step(batch, "complete-batch", 55)

    with pytest.raises(ValidationError, match="complete"):
        transfer_batch(batch, _tank("Tank C"), reason="x", timestamp=_ago(50))


@pytest.mark.django_db
def test_a_transfer_timestamp_in_the_future_is_rejected():
    batch = _aging_batch()

    with pytest.raises(ValidationError, match="future"):
        transfer_batch(batch, _tank("Tank B"), reason="x", timestamp=timezone.now() + datetime.timedelta(hours=1))


@pytest.mark.django_db
def test_a_transfer_before_the_latest_event_is_rejected():
    batch = _aging_batch()  # racked 80 days ago

    with pytest.raises(ValidationError, match="earlier"):
        transfer_batch(batch, _tank("Tank B"), reason="x", timestamp=_ago(90))


@pytest.mark.django_db
def test_a_stage_before_the_latest_transfer_is_rejected():
    batch = _aging_batch()
    transfer_batch(batch, _tank("Tank B"), reason="Pump failure", timestamp=_ago(50))

    with pytest.raises(ValidationError, match="earlier"):
        _step(batch, "coarse-filtering", 60, dst=_tank("Tank C"))
