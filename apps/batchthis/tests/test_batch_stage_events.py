import datetime

import pytest
from django.contrib import admin
from django.contrib.auth import get_user_model
from django.db.models import Prefetch, ProtectedError
from django.test import RequestFactory
from django.utils import timezone

from ..factories import BatchFactory, BatchStageEventFactory, VesselFactory
from ..models import Batch, BatchStage, BatchStageEvent

T0 = datetime.datetime(2026, 9, 1, 12, 0, tzinfo=datetime.UTC)


def _stage(shortid):
    return BatchStage.objects.get(shortid=shortid)


def _log(batch, shortid, days, vessel=None):
    return BatchStageEventFactory(
        batch=batch,
        stage=_stage(shortid),
        timestamp=T0 + datetime.timedelta(days=days),
        vessel=vessel or batch.current_vessel,
    )


# ---------- Model ----------

@pytest.mark.django_db
def test_events_come_back_in_timestamp_order_regardless_of_creation_order():
    batch = BatchFactory()
    racking = _log(batch, "racking", days=10)
    pitch = _log(batch, "pitch", days=0)

    assert list(batch.stage_events.all()) == [pitch, racking]


@pytest.mark.django_db
def test_events_with_the_same_timestamp_keep_the_order_they_were_logged():
    batch = BatchFactory()
    first = _log(batch, "coarse-filtering", days=5)
    second = _log(batch, "fine-filtering", days=5)

    assert list(batch.stage_events.all()) == [first, second]


@pytest.mark.django_db
def test_timestamp_defaults_to_now_but_can_be_set_after_the_fact():
    before = timezone.now()
    now_event = BatchStageEvent.objects.create(batch=BatchFactory(), stage=_stage("pitch"))
    backdated = _log(BatchFactory(), "pitch", days=0)

    assert before <= now_event.timestamp <= timezone.now()
    assert backdated.timestamp == T0


@pytest.mark.django_db
def test_a_stage_with_logged_events_cannot_be_deleted():
    _log(BatchFactory(), "pitch", days=0)

    with pytest.raises(ProtectedError):
        _stage("pitch").delete()


@pytest.mark.django_db
def test_deleting_a_vessel_keeps_the_event_without_it():
    batch = BatchFactory()
    vessel = VesselFactory()
    event = _log(batch, "pitch", days=0, vessel=vessel)

    vessel.delete()

    event.refresh_from_db()
    assert event.vessel is None


# ---------- Batch.current_stage_event / current_state ----------

@pytest.mark.django_db
def test_a_batch_with_no_events_has_no_current_stage_or_state():
    batch = BatchFactory()

    assert batch.current_stage_event is None
    assert batch.current_state is None


@pytest.mark.django_db
def test_current_stage_event_is_the_newest_regardless_of_creation_order():
    batch = BatchFactory()
    racking = _log(batch, "racking", days=10)
    _log(batch, "pitch", days=0)

    assert batch.current_stage_event == racking
    assert batch.current_state == BatchStage.STATE_AGING


@pytest.mark.django_db
def test_current_state_follows_the_workflow_to_completed():
    batch = BatchFactory()
    for days, shortid in enumerate(["pitch", "racking", "sterile-filtering", "complete-batch"]):
        _log(batch, shortid, days=days)

    assert batch.current_state == BatchStage.STATE_COMPLETED


# ---------- Batch.vessel_durations() - "time in vessel" ----------

@pytest.mark.django_db
def test_vessel_durations_is_empty_before_pitch():
    assert BatchFactory().vessel_durations() == []


@pytest.mark.django_db
def test_each_segment_runs_until_the_next_event():
    batch = BatchFactory()
    pitch = _log(batch, "pitch", days=0)
    racking = _log(batch, "racking", days=14)
    _log(batch, "sterile-filtering", days=60)

    fermentation, aging, _bottling = batch.vessel_durations()

    assert (fermentation.event, fermentation.state) == (pitch, BatchStage.STATE_FERMENTATION)
    assert (fermentation.start, fermentation.end) == (pitch.timestamp, racking.timestamp)
    assert fermentation.duration == datetime.timedelta(days=14)
    assert fermentation.is_open is False
    assert (aging.state, aging.duration) == (BatchStage.STATE_AGING, datetime.timedelta(days=46))


@pytest.mark.django_db
def test_the_newest_segment_is_open_and_measured_up_to_now():
    batch = BatchFactory()
    three_days_ago = timezone.now() - datetime.timedelta(days=3)
    BatchStageEventFactory(batch=batch, stage=_stage("pitch"), timestamp=three_days_ago)

    (fermentation,) = batch.vessel_durations()

    assert fermentation.is_open is True
    assert fermentation.end is None
    assert datetime.timedelta(days=3) <= fermentation.duration < datetime.timedelta(days=3, minutes=1)


@pytest.mark.django_db
def test_complete_batch_closes_the_timeline_instead_of_starting_a_segment():
    batch = BatchFactory()
    _log(batch, "pitch", days=0)
    _log(batch, "sterile-filtering", days=30)
    complete = _log(batch, "complete-batch", days=40)

    segments = batch.vessel_durations()

    assert [s.state for s in segments] == [BatchStage.STATE_FERMENTATION, BatchStage.STATE_BOTTLING]
    assert segments[-1].end == complete.timestamp
    assert not any(s.is_open for s in segments)


@pytest.mark.django_db
def test_a_repeat_racking_restarts_the_aging_timer_in_the_new_vessel():
    batch = BatchFactory()
    first_tank, second_tank = VesselFactory(), VesselFactory()
    _log(batch, "pitch", days=0)
    _log(batch, "racking", days=14, vessel=first_tank)
    _log(batch, "racking", days=44, vessel=second_tank)
    _log(batch, "sterile-filtering", days=90)

    segments = batch.vessel_durations()

    aging = [s for s in segments if s.state == BatchStage.STATE_AGING]
    assert [(s.vessel, s.duration.days) for s in aging] == [(first_tank, 30), (second_tank, 46)]


@pytest.mark.django_db
def test_vessel_durations_uses_one_query_however_many_events(django_assert_num_queries):
    batch = BatchFactory()
    for days, shortid in enumerate(["pitch", "racking", "coarse-filtering", "fine-filtering", "racking"]):
        _log(batch, shortid, days=days * 7)

    with django_assert_num_queries(1):
        segments = batch.vessel_durations()
        [(s.event.stage.name, s.vessel) for s in segments]


@pytest.mark.django_db
def test_time_in_vessel_resets_on_every_racking_and_filtering():
    batch = BatchFactory()
    for days, shortid in [(0, "pitch"), (14, "racking"), (44, "racking"), (90, "coarse-filtering"),
                          (95, "fine-filtering"), (100, "sterile-filtering")]:
        _log(batch, shortid, days=days, vessel=VesselFactory())

    stays = batch.vessel_durations()

    assert [s.duration.days for s in stays[:-1]] == [14, 30, 46, 5, 5]
    assert len({s.vessel for s in stays}) == 6


# ---------- Batch.full_aging() ----------

@pytest.mark.django_db
def test_full_aging_is_none_before_the_first_racking():
    batch = BatchFactory()
    _log(batch, "pitch", days=0)

    assert batch.full_aging() is None


@pytest.mark.django_db
def test_full_aging_runs_from_the_first_racking_to_the_first_filtering_across_rackings():
    batch = BatchFactory()
    _log(batch, "pitch", days=0)
    first_racking = _log(batch, "racking", days=14)
    _log(batch, "racking", days=44)
    filtering = _log(batch, "sterile-filtering", days=90)

    aging = batch.full_aging()

    assert (aging.start, aging.end) == (first_racking.timestamp, filtering.timestamp)
    assert aging.duration == datetime.timedelta(days=76)
    assert aging.is_open is False


@pytest.mark.django_db
@pytest.mark.parametrize("filtering", ["coarse-filtering", "fine-filtering", "sterile-filtering"])
def test_full_aging_ends_at_the_first_filtering_of_any_kind(filtering):
    batch = BatchFactory()
    _log(batch, "pitch", days=0)
    _log(batch, "racking", days=14)
    _log(batch, filtering, days=50)
    # Still Aging per the workflow graph after a coarse/fine filter, but full
    # aging has ended - later rackings/filters don't reopen or extend it.
    _log(batch, "racking", days=60)
    _log(batch, "sterile-filtering", days=80)

    aging = batch.full_aging()

    assert aging.duration == datetime.timedelta(days=36)
    assert aging.is_open is False


@pytest.mark.django_db
def test_full_aging_is_open_and_measured_up_to_now_until_a_filtering_is_logged():
    batch = BatchFactory()
    BatchStageEventFactory(batch=batch, stage=_stage("pitch"), timestamp=timezone.now() - datetime.timedelta(days=40))
    BatchStageEventFactory(batch=batch, stage=_stage("racking"), timestamp=timezone.now() - datetime.timedelta(days=30))
    BatchStageEventFactory(batch=batch, stage=_stage("racking"), timestamp=timezone.now() - datetime.timedelta(days=10))

    aging = batch.full_aging()

    assert aging.is_open is True
    assert aging.end is None
    assert datetime.timedelta(days=30) <= aging.duration < datetime.timedelta(days=30, minutes=1)


@pytest.mark.django_db
def test_both_durations_reuse_prefetched_events_without_extra_queries(django_assert_num_queries):
    batch = BatchFactory()
    for days, shortid in enumerate(["pitch", "racking", "racking", "coarse-filtering"]):
        _log(batch, shortid, days=days * 7)
    batch = Batch.objects.prefetch_related(
        Prefetch("stage_events", queryset=BatchStageEvent.objects.select_related("stage", "vessel"))
    ).get(pk=batch.pk)

    with django_assert_num_queries(0):
        batch.vessel_durations()
        batch.full_aging()


# ---------- Admin: view-only ----------

@pytest.mark.django_db
def test_batch_stage_events_are_view_only_in_django_admin():
    request = RequestFactory().get("/admin/")
    request.user = get_user_model().objects.create_superuser("root", "root@example.com", "pw")
    model_admin = admin.site._registry[BatchStageEvent]
    event = _log(BatchFactory(), "pitch", days=0)

    assert model_admin.has_view_permission(request, event)
    assert not model_admin.has_add_permission(request)
    assert not model_admin.has_change_permission(request, event)
    assert not model_admin.has_delete_permission(request, event)
