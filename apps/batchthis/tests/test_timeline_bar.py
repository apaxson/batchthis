import datetime

import pytest
from django.utils import timezone

from ..factories import BatchFactory, FermenterFactory, VesselFactory
from ..models import AgingTank, BatchStage, Vessel
from ..services import transfer_batch, transition_stage_event

FERMENTATION, AGING, BOTTLING = BatchStage.STATE_FERMENTATION, BatchStage.STATE_AGING, BatchStage.STATE_BOTTLING


def _stage(shortid):
    return BatchStage.objects.get(shortid=shortid)


def _ago(days):
    return timezone.now() - datetime.timedelta(days=days)


def _batch():
    vessel = VesselFactory(name="Carboy 1", status=Vessel.STATUS_ACTIVE)
    batch = BatchFactory(fermenter=FermenterFactory(vessel=vessel))
    batch.vessel = vessel
    batch.startdate = _ago(200)
    batch.save()
    return batch


def _tank(name):
    vessel = VesselFactory(name=name, status=Vessel.STATUS_READY)
    AgingTank.objects.create(vessel=vessel)
    return vessel


def _step(batch, shortid, days_ago, dst=None, notes=""):
    return transition_stage_event(batch, _stage(shortid), timestamp=_ago(days_ago), dst_vessel=dst, notes=notes)


def _summary(bar):
    return [(s.kind, s.state, s.label, s.vessel.name if s.vessel else None) for s in bar.segments]


# ---------- Which segments ----------

@pytest.mark.django_db
def test_an_unpitched_batch_is_all_future():
    bar = _batch().timeline_bar()

    assert _summary(bar) == [
        ("future", FERMENTATION, "Pitch", None),
        ("future", AGING, "Racking", None),
        ("future", BOTTLING, "Sterile Filtering", None),
    ]
    assert bar.completed_at is None


@pytest.mark.django_db
def test_a_fermenting_batch_has_a_current_segment_then_the_remaining_stages():
    batch = _batch()
    _step(batch, "pitch", 10)

    bar = batch.timeline_bar()

    assert _summary(bar) == [
        ("current", FERMENTATION, "Pitch", "Carboy 1"),
        ("future", AGING, "Racking", None),
        ("future", BOTTLING, "Sterile Filtering", None),
    ]


@pytest.mark.django_db
def test_an_aging_batch_shows_each_vessel_stay_including_transfers():
    batch = _batch()
    _step(batch, "pitch", 110, notes="EC-1118")
    _step(batch, "racking", 96, dst=_tank("Tank A"))
    transfer_batch(batch, _tank("Tank B"), reason="Pump failure", timestamp=_ago(60))
    _step(batch, "coarse-filtering", 12, dst=_tank("Tank C"))

    bar = batch.timeline_bar()

    assert _summary(bar) == [
        ("past", FERMENTATION, "Pitch", "Carboy 1"),
        ("past", AGING, "Racking", "Tank A"),
        ("past", AGING, "Transfer", "Tank B"),
        ("current", AGING, "Coarse Filtering", "Tank C"),
        ("future", BOTTLING, "Sterile Filtering", None),
    ]
    assert bar.segments[0].notes == "EC-1118"
    assert bar.segments[2].notes == "Pump failure"


@pytest.mark.django_db
def test_a_completed_batch_is_all_past_and_ends_at_completion():
    batch = _batch()
    _step(batch, "pitch", 100)
    _step(batch, "racking", 80, dst=_tank("Tank A"))
    _step(batch, "sterile-filtering", 40, dst=_tank("Tank B"))
    complete = _step(batch, "complete-batch", 30)

    bar = batch.timeline_bar()

    assert [s.kind for s in bar.segments] == ["past", "past", "past"]
    assert bar.completed_at == complete.timestamp
    assert bar.segments[-1].end == complete.timestamp


# ---------- Widths ----------

@pytest.mark.django_db
def test_past_and_current_widths_are_proportional_to_time():
    batch = _batch()
    _step(batch, "pitch", 90)
    _step(batch, "racking", 60, dst=_tank("Tank A"))  # 30 days fermenting
    _step(batch, "racking", 45, dst=_tank("Tank B"))  # 15 days in Tank A

    fermentation, tank_a, _current, _future = batch.timeline_bar().segments

    assert fermentation.weight == pytest.approx(tank_a.weight * 2, rel=1e-3)


@pytest.mark.django_db
def test_future_stages_without_a_plan_are_fixed_width_placeholders():
    batch = _batch()
    _step(batch, "pitch", 10)

    future = [s for s in batch.timeline_bar().segments if s.kind == "future"]

    assert all(s.duration is None and not s.planned and s.weight > 0 for s in future)
    assert future[0].weight == future[1].weight


@pytest.mark.django_db
def test_planned_durations_size_future_stages_to_scale():
    batch = _batch()
    _step(batch, "pitch", 10)
    plan = {AGING: datetime.timedelta(days=90), BOTTLING: datetime.timedelta(days=30)}

    current, aging, bottling = batch.timeline_bar(planned_durations=plan).segments

    assert (aging.planned, aging.duration) == (True, datetime.timedelta(days=90))
    assert aging.weight == pytest.approx(bottling.weight * 3)
    assert aging.weight == pytest.approx(current.weight * 9, rel=1e-3)


# ---------- State bands ----------

@pytest.mark.django_db
def test_segments_are_grouped_into_state_bands_with_totals():
    batch = _batch()
    _step(batch, "pitch", 90)
    _step(batch, "racking", 60, dst=_tank("Tank A"))
    _step(batch, "racking", 45, dst=_tank("Tank B"))

    bands = batch.timeline_bar().bands

    assert [(b.state, b.kind, len(b.segments)) for b in bands] == [
        (FERMENTATION, "past", 1),
        (AGING, "current", 2),
        (BOTTLING, "future", 1),
    ]
    assert bands[0].duration.days == 30
    assert bands[1].weight == pytest.approx(sum(s.weight for s in bands[1].segments))
    assert bands[2].duration is None


@pytest.mark.django_db
def test_timeline_bar_reuses_prefetched_events(django_assert_num_queries):
    from django.db.models import Prefetch

    from ..models import Batch, BatchStageEvent

    batch = _batch()
    _step(batch, "pitch", 90)
    _step(batch, "racking", 60, dst=_tank("Tank A"))
    batch = Batch.objects.select_related("vessel").prefetch_related(
        Prefetch("stage_events", queryset=BatchStageEvent.objects.select_related("stage", "vessel"))
    ).get(pk=batch.pk)

    with django_assert_num_queries(0):
        bar = batch.timeline_bar()
        [(s.vessel, s.label) for s in bar.segments]
