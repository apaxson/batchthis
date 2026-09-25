from unittest import mock

import pytest
from django.core.exceptions import ValidationError

from ..factories import BatchFactory, FermenterFactory, VesselFactory
from ..models import AgingTank, Batch, Vessel, VesselStatusEvent


def _batch_in(vessel_status=Vessel.STATUS_ACTIVE, **kwargs):
    fermenter = FermenterFactory(vessel=VesselFactory(status=vessel_status))
    return BatchFactory(fermenter=fermenter, **kwargs)


def _aging_tank(status=Vessel.STATUS_READY):
    vessel = VesselFactory(status=status)
    AgingTank.objects.create(vessel=vessel)
    return vessel


# ---------- Batch.current_vessel ----------

@pytest.mark.django_db
def test_current_vessel_falls_back_to_the_fermenter_for_batches_without_a_vessel_link():
    batch = _batch_in()

    assert batch.vessel is None
    assert batch.current_vessel == batch.fermenter.vessel


@pytest.mark.django_db
def test_current_vessel_is_the_vessel_link_when_set():
    batch = _batch_in()
    tank = _aging_tank()
    batch.vessel = tank
    batch.save()

    assert batch.current_vessel == tank


# ---------- Batch.transfer() ----------

@pytest.mark.django_db
def test_transfer_moves_the_batch_and_flips_both_vessels():
    batch = _batch_in()
    src = batch.fermenter.vessel
    dst = _aging_tank()

    batch.transfer(src, dst)

    batch.refresh_from_db()
    src.refresh_from_db()
    dst.refresh_from_db()
    assert batch.vessel == dst
    assert batch.fermenter.vessel == src  # the starting fermenter is kept
    assert src.status == Vessel.STATUS_DIRTY
    assert dst.status == Vessel.STATUS_ACTIVE
    assert src.current_batch is None
    assert dst.current_batch == batch


@pytest.mark.django_db
def test_transfer_logs_a_status_event_on_each_vessel_tied_to_the_batch():
    batch = _batch_in()
    src = batch.fermenter.vessel
    dst = _aging_tank()

    batch.transfer(src, dst)

    src_event = src.status_events.get()
    dst_event = dst.status_events.get()
    assert (src_event.status, src_event.batch) == (Vessel.STATUS_DIRTY, batch)
    assert (dst_event.status, dst_event.batch) == (Vessel.STATUS_ACTIVE, batch)
    assert dst.name in src_event.notes
    assert src.name in dst_event.notes


@pytest.mark.django_db
def test_transfer_writes_a_transfer_entry_in_the_batch_activity_log():
    batch = _batch_in()
    src = batch.fermenter.vessel
    dst = _aging_tank()

    batch.transfer(src, dst)

    entry = batch.activity.get(text__startswith="Transferred")
    assert src.name in entry.text and dst.name in entry.text


@pytest.mark.django_db
def test_a_second_transfer_starts_from_the_new_vessel():
    batch = _batch_in()
    fermenter_vessel = batch.fermenter.vessel
    first = _aging_tank()
    second = _aging_tank()
    batch.transfer(fermenter_vessel, first)

    batch.transfer(first, second)

    first.refresh_from_db()
    assert batch.current_vessel == second
    assert first.status == Vessel.STATUS_DIRTY


@pytest.mark.django_db
def test_complete_after_a_transfer_dirties_the_current_vessel_not_the_fermenter():
    batch = _batch_in()
    fermenter_vessel = batch.fermenter.vessel
    tank = _aging_tank()
    batch.transfer(fermenter_vessel, tank)

    batch.complete()

    tank.refresh_from_db()
    assert tank.status == Vessel.STATUS_DIRTY
    # One DIRTY event from the transfer, none from complete().
    assert fermenter_vessel.status_events.filter(status=Vessel.STATUS_DIRTY).count() == 1


@pytest.mark.django_db
@pytest.mark.parametrize(
    "dst_status", [Vessel.STATUS_ACTIVE, Vessel.STATUS_DIRTY, Vessel.STATUS_OUT]
)
def test_transfer_into_a_vessel_that_is_not_clean_ready_is_rejected(dst_status):
    batch = _batch_in()
    src = batch.fermenter.vessel
    dst = _aging_tank(status=dst_status)

    with pytest.raises(ValidationError):
        batch.transfer(src, dst)

    _assert_nothing_changed(batch, src, dst, dst_status)


@pytest.mark.django_db
def test_transfer_from_a_vessel_the_batch_is_not_in_is_rejected():
    batch = _batch_in()
    wrong_src = _aging_tank(status=Vessel.STATUS_ACTIVE)
    dst = _aging_tank()

    with pytest.raises(ValidationError):
        batch.transfer(wrong_src, dst)

    _assert_nothing_changed(batch, wrong_src, dst, Vessel.STATUS_READY)


@pytest.mark.django_db
def test_transfer_to_the_same_vessel_is_rejected():
    batch = _batch_in()
    src = batch.fermenter.vessel

    with pytest.raises(ValidationError):
        batch.transfer(src, src)

    assert not src.status_events.exists()


@pytest.mark.django_db
def test_transfer_of_a_completed_batch_is_rejected():
    batch = _batch_in(active=False)
    src = batch.fermenter.vessel
    dst = _aging_tank()

    with pytest.raises(ValidationError):
        batch.transfer(src, dst)

    _assert_nothing_changed(batch, src, dst, Vessel.STATUS_READY)


@pytest.mark.django_db
def test_transfer_rolls_back_everything_if_a_status_change_fails():
    batch = _batch_in()
    src = batch.fermenter.vessel
    dst = _aging_tank()
    real_create = VesselStatusEvent.objects.create
    calls = []

    def fail_on_second(**kwargs):
        calls.append(kwargs)
        if len(calls) == 2:
            raise RuntimeError("db down")
        return real_create(**kwargs)

    with mock.patch.object(VesselStatusEvent.objects, "create", side_effect=fail_on_second):
        with pytest.raises(RuntimeError):
            batch.transfer(src, dst)

    _assert_nothing_changed(Batch.objects.get(pk=batch.pk), src, dst, Vessel.STATUS_READY)
    assert not batch.activity.filter(text__startswith="Transferred").exists()


def _assert_nothing_changed(batch, src, dst, dst_status):
    batch.refresh_from_db()
    src.refresh_from_db()
    dst.refresh_from_db()
    assert batch.vessel is None
    assert dst.status == dst_status
    assert not src.status_events.exists()
    assert not dst.status_events.exists()


@pytest.mark.django_db
def test_creating_a_batch_records_its_fermenter_vessel_as_the_current_vessel():
    from ..factories import RecipeFactory
    from .test_batch_vessel_status import _add_batch_post

    fermenter = FermenterFactory(vessel=VesselFactory(status=Vessel.STATUS_READY))

    _add_batch_post(fermenter, RecipeFactory(with_plan=True))

    assert Batch.objects.get(name="status test batch").vessel == fermenter.vessel
